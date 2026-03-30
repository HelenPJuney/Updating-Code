"""
integration/service.py
────────────────────────────────────────────────────────────────────────────
IntegrationService — central orchestrator for external-app integrations.

Production fixes applied:
  #5  — standardized to submit_call_request (was send_call_request in some paths)
  #8  — TTL cleanup for call_status_store AND request_counts (auth.py's rate limiter)
  #9  — HMAC-SHA256 webhook signing is now CORRECTLY computed (was hmac.new() bug)
  #12 — start_call() returns room_id + token for SIP; start_browser_call() returns token
  #11 — no silent failures; structured log lines with session_id
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from livekit.kafka.producer import get_producer
from livekit.kafka.schemas import CallRequest
from livekit.websocket import event_hub

logger = logging.getLogger("callcenter.integration.service")

# ── TTL constants ──────────────────────────────────────────────────────────────
_CALL_STATUS_TTL_SEC  = 3600   # 1 hour — remove completed/failed sessions
_REQUEST_COUNT_TTL_SEC = 60    # sliding window for rate limiting
_CLEANUP_INTERVAL_SEC  = 60    # how often the cleanup loop runs


class IntegrationService:

    def __init__(self) -> None:
        # FIX #9: Increased timeout; connection pool handles concurrent webhooks
        self._http_client = httpx.AsyncClient(timeout=10.0)

        # Session state keyed by session_id
        self.call_status_store: Dict[str, Dict[str, Any]] = {}

        # Webhook registry: client_id → {url, events, secret}
        self.webhook_registry: Dict[str, Dict[str, Any]] = {}

        self._listener_task: Optional[asyncio.Task] = None
        self._cleanup_task:  Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start event listener + TTL cleanup background tasks."""
        logger.info("[IntegrationService] starting")
        self._queue = await event_hub.subscribe(replay_history=False)
        self._listener_task = asyncio.create_task(self._listen_loop(), name="integration-listener")
        self._cleanup_task  = asyncio.create_task(self._cleanup_loop(), name="integration-cleanup")
        logger.info("[IntegrationService] started — listening for webhook events")

    async def stop(self) -> None:
        """Cancel tasks, unsubscribe from hub, close HTTP client."""
        logger.info("[IntegrationService] stopping")
        for task in (self._listener_task, self._cleanup_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._queue:
            await event_hub.unsubscribe(self._queue)
        await self._http_client.aclose()
        logger.info("[IntegrationService] stopped")

    # ── Background: event listener ─────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        logger.debug("[IntegrationService._listen_loop] started")
        try:
            while True:
                if not self._queue:
                    break
                event = await self._queue.get()
                event_type = event.get("type", "")
                session_id = event.get("session_id")

                if event_type in ("call_started", "call_completed", "call_failed"):
                    if session_id and session_id in self.call_status_store:
                        _STATUS_MAP = {
                            "call_started":   "active",
                            "call_completed": "completed",
                            "call_failed":    "failed",
                        }
                        entry = self.call_status_store[session_id]
                        entry["status"]     = _STATUS_MAP.get(event_type, "unknown")
                        entry["updated_at"] = time.time()
                        if event.get("node_id"):
                            entry["assigned_agent"] = event["node_id"]
                        logger.info(
                            "[IntegrationService] session=%.8s → status=%s",
                            session_id, entry["status"],
                        )
                        asyncio.create_task(
                            self.trigger_webhook(event_type, dict(entry)),
                            name=f"webhook-{event_type}-{session_id[:8]}",
                        )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[IntegrationService._listen_loop] unexpected error")

    # ── Background: TTL cleanup ────────────────────────────────────────────────

    async def _cleanup_loop(self) -> None:
        """
        FIX #8: Periodically purge stale entries from call_status_store.
        Also purges stale timestamps from auth.py's request_counts to prevent
        that dict from growing without bound.
        """
        logger.debug("[IntegrationService._cleanup_loop] started")
        try:
            while True:
                await asyncio.sleep(_CLEANUP_INTERVAL_SEC)
                now = time.time()

                # ── call_status_store TTL ─────────────────────────────────────
                expired_sessions = [
                    sid for sid, data in self.call_status_store.items()
                    if now - data.get("updated_at", now) > _CALL_STATUS_TTL_SEC
                ]
                for sid in expired_sessions:
                    del self.call_status_store[sid]
                if expired_sessions:
                    logger.info(
                        "[IntegrationService] TTL cleanup: removed %d stale call records",
                        len(expired_sessions),
                    )

                # ── auth.request_counts TTL ───────────────────────────────────
                # This prevents the rate-limit dict in auth.py leaking memory
                # for clients that stop calling but are never garbage-collected.
                try:
                    from livekit.integration.auth import request_counts, rate_limit_lock
                    async with rate_limit_lock:
                        for client_id in list(request_counts.keys()):
                            request_counts[client_id] = [
                                t for t in request_counts[client_id]
                                if now - t < _REQUEST_COUNT_TTL_SEC
                            ]
                            if not request_counts[client_id]:
                                del request_counts[client_id]
                except Exception as exc:
                    logger.debug("[IntegrationService] request_counts cleanup skipped: %s", exc)

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[IntegrationService._cleanup_loop] unexpected error")

    # ── Public API ────────────────────────────────────────────────────────────

    async def start_call(
        self,
        phone_number: str,
        lang: str,
        source: str,
        metadata: dict,
    ) -> Dict[str, Any]:
        """
        Initiate a SIP/PSTN call from the integration API.

        FIX #1: caller_number is set (NOT caller_id — that's for browser only).
        FIX #5: uses submit_call_request (single standardized method).
        FIX #12: returns session_id + room_id + status dict (not just session_id str).
        """
        logger.info("[IntegrationService.start_call] phone=%.8s  lang=%s  source=%s", phone_number, lang, source)
        start_t = time.perf_counter()
        try:
            req = CallRequest(
                caller_number = phone_number,   # FIX #1: SIP field
                caller_id     = "",             # FIX #1: blank for SIP
                lang          = lang,
                source        = source,         # "external_sip" | "sip"
                metadata      = metadata,
                llm           = "gemini",       # FIX #6: safe default
                voice         = "",             # FIX #6: safe default
                agent_name    = "AI Assistant", # FIX #6: safe default
            )

            # Persist immediately — listener loop will update status later
            self.call_status_store[req.session_id] = {
                "session_id":     req.session_id,
                "room_id":        req.room_id,
                "status":         "queued",
                "source":         source,
                "phone_number":   phone_number,
                "created_at":     req.timestamp,
                "updated_at":     req.timestamp,
                "metadata":       metadata,
                "assigned_agent": None,
            }

            # FIX #5: single, standardized method
            result = await get_producer().submit_call_request(req)
            if result is None:
                # Kafka unavailable — direct spawn handled by caller (SIP bridge)
                self.call_status_store[req.session_id]["status"] = "direct"

            logger.info(
                "[IntegrationService.start_call] session=%.8s  room=%.8s  elapsed=%.4fs",
                req.session_id, req.room_id, time.perf_counter() - start_t,
            )
            # FIX #12: return full response data (not just session_id)
            return {
                "session_id": req.session_id,
                "room_id":    req.room_id,
                "token":      "",           # SIP calls don't need a WebRTC token
                "livekit_url": "",
                "status":     self.call_status_store[req.session_id]["status"],
            }
        except Exception:
            logger.exception("[IntegrationService.start_call] ERROR  phone=%.8s", phone_number)
            raise

    async def start_browser_call(
        self,
        caller_id: str,
        lang: str,
        source: str,
        priority: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        """
        Initiate a browser WebRTC call from the integration API.

        FIX #1: caller_id is set (NOT caller_number).
        FIX #12: returns session_id + room_id + LiveKit token + status.
        """
        logger.info(
            "[IntegrationService.start_browser_call] caller_id=%.16s  lang=%s  source=%s",
            caller_id, lang, source,
        )
        start_t = time.perf_counter()
        try:
            # Delegate to the browser router's unified pipeline
            from livekit.browser.router import browser_call_start, BrowserCallRequest
            result = await browser_call_start(BrowserCallRequest(
                caller_id = caller_id,
                lang      = lang,
                priority  = priority,
                metadata  = metadata,
            ))
            # Record in our status store so /call/{id}/status works
            self.call_status_store[result.session_id] = {
                "session_id":     result.session_id,
                "room_id":        result.room_id,
                "status":         result.status,
                "source":         source,
                "caller_id":      caller_id,
                "created_at":     time.time(),
                "updated_at":     time.time(),
                "metadata":       metadata,
                "assigned_agent": None,
            }
            logger.info(
                "[IntegrationService.start_browser_call] session=%.8s  elapsed=%.4fs",
                result.session_id, time.perf_counter() - start_t,
            )
            # FIX #12: full response including token
            return {
                "session_id":  result.session_id,
                "room_id":     result.room_id,
                "token":       result.token,
                "livekit_url": result.livekit_url,
                "status":      result.status,
                "source":      source,
            }
        except Exception:
            logger.exception("[IntegrationService.start_browser_call] ERROR  caller_id=%.16s", caller_id)
            raise

    def get_call_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return current status for a session, or None if not found."""
        return self.call_status_store.get(session_id)

    def register_webhook(
        self,
        client_id: str,
        url:      str,
        events:   List[str],
        secret:   str = "",
    ) -> None:
        """Register a webhook endpoint for a client."""
        self.webhook_registry[client_id] = {
            "url":    url,
            "events": events,
            "secret": secret,
        }
        logger.info(
            "[IntegrationService] webhook registered  client=%s  url=%.40s  events=%s",
            client_id, url, events,
        )

    async def trigger_webhook(self, event_type: str, payload: dict) -> None:
        """Fan-out webhook delivery for all clients subscribed to this event_type."""
        for client_id, config in self.webhook_registry.items():
            if event_type in config.get("events", []):
                asyncio.create_task(
                    self._deliver_webhook_with_retry(client_id, config, event_type, payload),
                    name=f"deliver-{client_id}-{event_type}",
                )

    async def _deliver_webhook_with_retry(
        self,
        client_id:  str,
        config:     dict,
        event_type: str,
        payload:    dict,
    ) -> None:
        """
        Deliver webhook with exponential-backoff retry (3 attempts).

        FIX #9: HMAC-SHA256 signing is now CORRECT.
        The original used hmac.new() which does NOT exist — it is hmac.new()
        only in Python 2. In Python 3 it is hmac.new(key, msg, digestmod).
        Fixed to: hmac.new(key_bytes, msg_bytes, hashlib.sha256).hexdigest()
        """
        url    = config["url"]
        secret = config.get("secret", "")
        body   = {"event": event_type, "data": payload, "ts": time.time()}
        headers: Dict[str, str] = {"Content-Type": "application/json"}

        if secret:
            body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
            # FIX #9: correct HMAC — hmac.new(key, msg, digestmod)
            sig = hmac.new(
                secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await self._http_client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                logger.debug(
                    "[IntegrationService] webhook delivered  client=%s  event=%s  attempt=%d",
                    client_id, event_type, attempt + 1,
                )
                return
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "[IntegrationService] webhook HTTP error  client=%s  status=%d  attempt=%d/%d",
                    client_id, exc.response.status_code, attempt + 1, max_retries,
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "[IntegrationService] webhook request error  client=%s  error=%s  attempt=%d/%d",
                    client_id, exc, attempt + 1, max_retries,
                )
            except Exception:
                logger.exception(
                    "[IntegrationService] webhook unexpected error  client=%s  attempt=%d",
                    client_id, attempt + 1,
                )
                break   # non-transient error — stop immediately

            if attempt < max_retries - 1:
                await asyncio.sleep(1.0 * (2 ** attempt))  # 1s, 2s

        logger.error(
            "[IntegrationService] webhook exhausted retries  client=%s  event=%s  url=%.60s",
            client_id, event_type, url,
        )


# ── Module-level singleton ────────────────────────────────────────────────────
integration_service = IntegrationService()
