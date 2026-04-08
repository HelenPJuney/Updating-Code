import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useRoomContext,
  useParticipants,
} from '@livekit/components-react';

const API = import.meta.env.VITE_BACKEND_URL || '';
const WS_URL = import.meta.env.VITE_BACKEND_WS || (API ? `${API.replace(/^http/, 'ws')}/ws/events` : '');

/* ═══════════════════════════════════════════════════════════════════════════════
   Department options
   ═══════════════════════════════════════════════════════════════════════════════ */
const DEPT_OPTIONS = [
  'Billing Department',
  'Technical Department',
  'Sales Department',
  'General Support',
];

/* ═══════════════════════════════════════════════════════════════════════════════
   ActiveCallView — Renders inside LiveKitRoom when agent is on a call
   ═══════════════════════════════════════════════════════════════════════════════ */
function ActiveCallView({ callInfo, onEndCall }) {
  const room = useRoomContext();
  const participants = useParticipants();
  const callerWasSeen = useRef(false);
  const endedRef = useRef(false);

  const autoEnd = useCallback(() => {
    if (endedRef.current) return;
    endedRef.current = true;
    room.disconnect();
    onEndCall();
  }, [room, onEndCall]);

  // Bug Fix 2: Empty session — if no one joins within 8s, disconnect + log
  useEffect(() => {
    const t = setTimeout(() => {
      if (!callerWasSeen.current) {
        console.warn('[ActiveCall] Empty session — caller never joined, disconnecting');
        autoEnd();
      }
    }, 8000);
    return () => clearTimeout(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Bug Fix 1: Caller disconnect — if caller was seen then leaves, auto-end
  useEffect(() => {
    const callerPresent = participants.some(
      (p) => p.identity && (p.identity.startsWith('caller-') || p.identity === callInfo.callerId)
    );
    if (callerPresent) callerWasSeen.current = true;
    if (callerWasSeen.current && !callerPresent && !endedRef.current) {
      setTimeout(autoEnd, 2000); // small grace period for reconnects
    }
  }, [participants, autoEnd, callInfo.callerId]);

  const handleEnd = useCallback(() => {
    room.disconnect();
    onEndCall();
  }, [room, onEndCall]);

  return (
    <div className="incoming-call-popup" style={{ borderColor: 'rgba(52, 211, 153, 0.4)', background: 'linear-gradient(135deg, rgba(52, 211, 153, 0.08), rgba(34, 211, 238, 0.08))' }}>
      <div className="incoming-label" style={{ color: 'var(--accent-emerald)' }}>
        <span className="ring-indicator" style={{ background: 'var(--accent-emerald)' }} />
        ACTIVE CALL
      </div>
      <div className="incoming-caller-info" style={{ position: 'relative' }}>
        <div>
          <div className="incoming-caller-name">
            📞 {callInfo.callerId || callInfo.userEmail || 'Unknown Caller'}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
            <span className="incoming-dept-badge">{callInfo.department}</span>
            {callInfo.sessionId && (
              <span className="queue-meta" style={{ alignSelf: 'center' }}>
                Session: {callInfo.sessionId.substring(0, 8)}...
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="incoming-actions">
        <button className="btn btn-danger" onClick={handleEnd}>
          ✕ End Call
        </button>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   OutboundPopup — 10-second countdown popup for outbound callbacks
   ═══════════════════════════════════════════════════════════════════════════════ */
const DECLINE_REASONS = [
  'Feeling unwell',
  'Need a break',
  'In a meeting',
  'Technical issue',
  'On another call',
];

function OutboundPopup({ outbound, onAccept, onDecline }) {
  const [countdown, setCountdown] = useState(20);
  const [showDeclineForm, setShowDeclineForm] = useState(false);
  const [reason, setReason] = useState(DECLINE_REASONS[0]);
  const [snoozeMinutes, setSnoozeMinutes] = useState(10);
  const autoFiredRef = useRef(false);

  useEffect(() => {
    if (countdown <= 0) {
      // Auto-execute: start calling missed customers automatically
      if (!autoFiredRef.current) {
        autoFiredRef.current = true;
        onAccept(outbound);
      }
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [countdown, outbound, onAccept]);

  return (
    <div className="outbound-popup">
      <div className="outbound-countdown" style={{ color: countdown <= 5 ? 'var(--accent-rose, #f43f5e)' : undefined }}>
        ⏱️ {countdown}s
      </div>
      <div className="outbound-label">📤 OUTBOUND CALLBACK</div>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: '0.75rem', position: 'relative' }}>
        {countdown > 0 ? 'Customer left queue. Auto-calling in ' + countdown + 's if no action.' : 'Auto-calling now...'}
      </p>
      <div style={{ background: 'rgba(59, 130, 246, 0.1)', padding: '0.75rem', borderRadius: '0.5rem', marginBottom: '1rem', borderLeft: '3px solid var(--accent-cyan)' }}>
        <p style={{ color: 'var(--text-primary)', fontSize: '0.95rem', fontWeight: 600, margin: '0 0 0.25rem 0' }}>
          📧 {outbound.user_email}
        </p>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: '0' }}>
          {outbound.department}
        </p>
      </div>

      {!showDeclineForm ? (
        <div className="incoming-actions" style={{ position: 'relative' }}>
          <button className="btn btn-success" onClick={() => onAccept(outbound)}>
            ✓ Accept Callback
          </button>
          <button className="btn btn-danger" onClick={() => setShowDeclineForm(true)}>
            ✕ Decline
          </button>
        </div>
      ) : (
        <div className="decline-form">
          <label>Justification (logged for productivity)</label>
          <select
            className="decline-select"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ marginBottom: '0.75rem' }}
          >
            {DECLINE_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <label>Ignore outbound calls for</label>
          <select
            className="decline-select"
            value={snoozeMinutes}
            onChange={(e) => setSnoozeMinutes(Number(e.target.value))}
          >
            <option value={5}>5 minutes</option>
            <option value={10}>10 minutes</option>
            <option value={15}>15 minutes</option>
            <option value={30}>30 minutes</option>
          </select>
          <button
            className="btn btn-danger"
            style={{ width: '100%', justifyContent: 'center' }}
            onClick={() => onDecline(outbound, reason, snoozeMinutes)}
          >
            Confirm Decline & Snooze
          </button>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   SnoozeWidget — visible when agent has active snooze
   ═══════════════════════════════════════════════════════════════════════════════ */
function SnoozeWidget({ snoozeUntil, onResume }) {
  const [remaining, setRemaining] = useState('');

  useEffect(() => {
    const update = () => {
      const now = new Date();
      const until = new Date(snoozeUntil);
      const diff = Math.max(0, Math.floor((until - now) / 1000));
      if (diff <= 0) {
        setRemaining('Expired');
        return;
      }
      const mins = Math.floor(diff / 60);
      const secs = diff % 60;
      setRemaining(`${mins}:${secs.toString().padStart(2, '0')}`);
    };
    update();
    const iv = setInterval(update, 1000);
    return () => clearInterval(iv);
  }, [snoozeUntil]);

  if (!snoozeUntil) return null;

  return (
    <div className="snooze-widget">
      <div>
        <div className="snooze-label">⏱️ Outbound Snooze Active</div>
        <div className="snooze-timer">{remaining} remaining</div>
      </div>
      <button className="btn btn-primary" style={{ fontSize: '0.75rem', padding: '0.4rem 0.8rem' }} onClick={onResume}>
        Resume Now
      </button>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   AgentDashboard — Main export
   States: login → online → in-call
   ═══════════════════════════════════════════════════════════════════════════════ */
export function AgentDashboard() {
  const [phase, setPhase] = useState('login'); // login | online | in-call
  const [agentName, setAgentName] = useState('');
  const [department, setDepartment] = useState(DEPT_OPTIONS[0]);
  const [agentIdentity, setAgentIdentity] = useState('');
  const [seqNumber, setSeqNumber] = useState(0);

  // Online dashboard state
  const [queueCallers, setQueueCallers] = useState([]);
  const [deptAgents, setDeptAgents] = useState([]);
  const [outboundPopup, setOutboundPopup] = useState(null);
  const [snoozeUntil, setSnoozeUntil] = useState(null);

  // Admin config panel
  const [showAdmin, setShowAdmin] = useState(false);
  const [adminConfig, setAdminConfig] = useState({
    work_start: '09:00', work_end: '18:00',
    work_days: '0,1,2,3,4,5', timezone: 'Asia/Kolkata',
    avg_resolution_seconds: 300,
  });
  const [adminSaved, setAdminSaved] = useState(false);

  // In-call state
  const [callToken, setCallToken] = useState(null);
  const [callUrl, setCallUrl] = useState('');
  const [callInfo, setCallInfo] = useState({});

  const wsRef = useRef(null);
  const pollTimerRef = useRef(null);
  const agentPollTimerRef = useRef(null);

  // ── Login ────────────────────────────────────────────────────────────────
  const handleLogin = useCallback(async () => {
    if (!agentName.trim()) return;
    const identity = `agent-${agentName.trim().toLowerCase().replace(/\s+/g, '-')}-${Math.random().toString(36).substring(2, 8)}`;
    setAgentIdentity(identity);

    try {
      const res = await fetch(`${API}/cc/agent/online`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ agent_identity: identity, agent_name: agentName.trim(), department }),
      });
      if (!res.ok) throw new Error(`Login failed: ${res.status}`);
      const data = await res.json();
      setSeqNumber(data.sequence_number);
      setPhase('online');
    } catch (err) {
      console.error('Agent login error:', err);
    }
  }, [agentName, department]);

  // ── Go Offline ───────────────────────────────────────────────────────────
  const handleGoOffline = useCallback(async () => {
    try {
      await fetch(`${API}/cc/agent/offline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ agent_identity: agentIdentity }),
      });
    } catch (err) {
      console.error('Offline error:', err);
    }
    setPhase('login');
    setAgentIdentity('');
    setSeqNumber(0);
    setQueueCallers([]);
    setDeptAgents([]);
    setOutboundPopup(null);
    setSnoozeUntil(null);
  }, [agentIdentity]);

  // ── Polling: Queue + Agent list ──────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'online' && phase !== 'in-call') return;

    const pollQueue = async () => {
      try {
        const res = await fetch(`${API}/cc/queue?department=${encodeURIComponent(department)}`, {
          headers: { 'ngrok-skip-browser-warning': '1' },
        });
        if (res.ok) {
          const data = await res.json();
          setQueueCallers(data.callers || []);
        }
      } catch (e) { /* ignore */ }
    };

    const pollAgents = async () => {
      try {
        const res = await fetch(`${API}/cc/agent/department/${encodeURIComponent(department)}`, {
          headers: { 'ngrok-skip-browser-warning': '1' },
        });
        if (res.ok) {
          const data = await res.json();
          setDeptAgents(data.agents || []);

          // Check snooze status for self
          const self = (data.agents || []).find((a) => a.agent_identity === agentIdentity);
          if (self?.ignore_outbounds_until) {
            setSnoozeUntil(self.ignore_outbounds_until);
          } else {
            setSnoozeUntil(null);
          }
        }
      } catch (e) { /* ignore */ }
    };

    pollQueue();
    pollAgents();
    pollTimerRef.current = setInterval(pollQueue, 2000);
    agentPollTimerRef.current = setInterval(pollAgents, 3000);

    return () => {
      clearInterval(pollTimerRef.current);
      clearInterval(agentPollTimerRef.current);
    };
  }, [phase, department, agentIdentity]);

  // ── WebSocket: Outbound callback events ──────────────────────────────────
  useEffect(() => {
    if (phase !== 'online' && phase !== 'in-call') return;
    if (!agentIdentity) return;

    let ws;
    const connectWs = () => {
      const targetWs = WS_URL || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/events`;
      ws = new WebSocket(targetWs);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (
            data.type === 'outbound_callback' &&
            data.target_agent === agentIdentity &&
            phase === 'online'
          ) {
            setOutboundPopup(data);
          }
        } catch (e) { /* ignore */ }
      };

      ws.onclose = () => {
        // Reconnect after 3s
        setTimeout(() => {
          if (phase === 'online' || phase === 'in-call') {
            connectWs();
          }
        }, 3000);
      };

      wsRef.current = ws;
    };

    connectWs();

    return () => {
      if (ws) ws.close();
    };
  }, [phase, agentIdentity]);

  // ── Accept call from queue ───────────────────────────────────────────────
  const handleAcceptCall = useCallback(async (caller) => {
    try {
      const res = await fetch(
        `${API}/cc/agent/accept/${caller.session_id}?agent_identity=${encodeURIComponent(agentIdentity)}&agent_name=${encodeURIComponent(agentName)}`,
        {
          method: 'POST',
          headers: { 'ngrok-skip-browser-warning': '1' },
        }
      );
      if (!res.ok) throw new Error(`Accept failed: ${res.status}`);
      const data = await res.json();

      setCallToken(data.token);
      setCallUrl(data.url);
      setCallInfo({
        sessionId: data.session_id,
        room: data.room,
        callerId: data.caller_id,
        userEmail: data.user_email,
        department,
      });
      setPhase('in-call');
    } catch (err) {
      console.error('Accept call error:', err);
    }
  }, [agentIdentity, agentName, department]);

  // ── End call ─────────────────────────────────────────────────────────────
  const handleEndCall = useCallback(async () => {
    try {
      await fetch(`${API}/cc/agent/end-call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({
          agent_identity: agentIdentity,
          session_id: callInfo.sessionId,
        }),
      });
    } catch (err) {
      console.error('End call error:', err);
    }
    setCallToken(null);
    setCallUrl('');
    setCallInfo({});
    setPhase('online');
  }, [agentIdentity, callInfo.sessionId]);

  // ── Accept outbound ──────────────────────────────────────────────────────
  const handleAcceptOutbound = useCallback(async (ob) => {
    try {
      const res = await fetch(`${API}/cc/outbound/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ outbound_id: ob.outbound_id, agent_identity: agentIdentity }),
      });
      if (!res.ok) throw new Error(`Outbound accept failed: ${res.status}`);
      const data = await res.json();

      setCallToken(data.token);
      setCallUrl(data.url);
      setCallInfo({
        sessionId: `outbound-${ob.outbound_id}`,
        room: data.room,
        callerId: ob.user_email,
        userEmail: ob.user_email,
        department: ob.department,
      });
      setOutboundPopup(null);
      setPhase('in-call');
    } catch (err) {
      console.error('Outbound accept error:', err);
    }
  }, [agentIdentity]);

  // ── Decline outbound ─────────────────────────────────────────────────────
  const handleDeclineOutbound = useCallback(async (ob, reason, snoozeMinutes) => {
    try {
      const res = await fetch(`${API}/cc/outbound/decline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({
          outbound_id: ob.outbound_id,
          agent_identity: agentIdentity,
          reason,
          snooze_minutes: snoozeMinutes,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setSnoozeUntil(data.snooze_until);
      }
    } catch (err) {
      console.error('Decline error:', err);
    }
    setOutboundPopup(null);
  }, [agentIdentity]);

  // ── Admin config ─────────────────────────────────────────────────────────
  const loadAdminConfig = useCallback(async () => {
    try {
      const res = await fetch(`${API}/cc/admin/config`, { headers: { 'ngrok-skip-browser-warning': '1' } });
      if (res.ok) {
        const data = await res.json();
        const c = data.config || {};
        setAdminConfig({
          work_start: c.work_start || '09:00',
          work_end: c.work_end || '18:00',
          work_days: c.work_days || '0,1,2,3,4,5',
          timezone: c.timezone || 'Asia/Kolkata',
          avg_resolution_seconds: parseInt(c.avg_resolution_seconds || '300', 10),
        });
      }
    } catch (e) { /* ignore */ }
  }, []);

  const saveAdminConfig = useCallback(async () => {
    try {
      await fetch(`${API}/cc/admin/business-hours`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify(adminConfig),
      });
      setAdminSaved(true);
      setTimeout(() => setAdminSaved(false), 2000);
    } catch (e) { console.error('Admin save error:', e); }
  }, [adminConfig]);

  // ── Resume outbound ──────────────────────────────────────────────────────
  const handleResume = useCallback(async () => {
    try {
      await fetch(`${API}/cc/outbound/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ agent_identity: agentIdentity }),
      });
      setSnoozeUntil(null);
    } catch (err) {
      console.error('Resume error:', err);
    }
  }, [agentIdentity]);

  // ══════════════════════════════════════════════════════════════════════════
  // RENDER: Login Phase
  // ══════════════════════════════════════════════════════════════════════════
  if (phase === 'login') {
    return (
      <div className="agent-login glass-card-static">
        <h2>Agent Login</h2>
        <p className="ivr-detail-text" style={{ marginBottom: '1.5rem', marginTop: '0.5rem' }}>
          Sign in to start receiving calls
        </p>

        <input
          type="text"
          className="agent-name-input"
          placeholder="Your name"
          value={agentName}
          onChange={(e) => setAgentName(e.target.value)}
          id="agent-name-input"
          autoFocus
        />

        <select
          className="decline-select"
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
          id="agent-dept-select"
        >
          {DEPT_OPTIONS.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>

        <button
          className="btn btn-primary"
          style={{ width: '100%', justifyContent: 'center', padding: '0.9rem', marginTop: '0.5rem' }}
          onClick={handleLogin}
          disabled={!agentName.trim()}
          id="agent-go-online-btn"
        >
          Go Online →
        </button>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════
  // RENDER: Online / In-Call Dashboard 
  // ══════════════════════════════════════════════════════════════════════════
  return (
    <div className="agent-panel">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="agent-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          Agent Panel
          <span className="agent-seq-badge">
            Agent {seqNumber} · {department.split(' ')[0]}
          </span>
        </h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span className={`agent-status-badge ${phase === 'in-call' ? 'badge-busy' : 'badge-online'}`}>
            <span className="status-dot" style={phase === 'in-call' ? { background: 'var(--accent-amber)' } : {}} />
            {phase === 'in-call' ? 'Busy' : 'Online'}
          </span>
          <button className="btn btn-ghost" onClick={handleGoOffline} id="agent-go-offline-btn">
            Go Offline
          </button>
        </div>
      </div>

      {/* ── Active Call (in-call phase) ──────────────────────────────────── */}
      {phase === 'in-call' && callToken && (
        <LiveKitRoom
          video={false}
          audio={true}
          token={callToken}
          serverUrl={callUrl}
          connect={true}
          onDisconnected={handleEndCall}
        >
          <RoomAudioRenderer />
          <ActiveCallView callInfo={callInfo} onEndCall={handleEndCall} />
        </LiveKitRoom>
      )}

      {/* ── Outbound Callback Popup ─────────────────────────────────────── */}
      {outboundPopup && phase === 'online' && (
        <OutboundPopup
          outbound={outboundPopup}
          onAccept={handleAcceptOutbound}
          onDecline={handleDeclineOutbound}
        />
      )}

      {/* ── Snooze Widget ───────────────────────────────────────────────── */}
      <SnoozeWidget snoozeUntil={snoozeUntil} onResume={handleResume} />

      {/* ── Admin Config Panel ──────────────────────────────────────────── */}
      <div className="glass-card-static" style={{ marginBottom: '1.5rem' }}>
        <div className="queue-dashboard-title" style={{ cursor: 'pointer' }} onClick={() => { setShowAdmin(v => !v); if (!showAdmin) loadAdminConfig(); }}>
          <h3>⚙️ Admin Settings</h3>
          <span className="queue-count-badge">{showAdmin ? '▲ Hide' : '▼ Show'}</span>
        </div>
        {showAdmin && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '1rem' }}>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <label style={{ flex: 1, minWidth: '120px' }}>
                <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>Work Start</span>
                <input type="time" className="agent-name-input" value={adminConfig.work_start}
                  onChange={e => setAdminConfig(c => ({ ...c, work_start: e.target.value }))} />
              </label>
              <label style={{ flex: 1, minWidth: '120px' }}>
                <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>Work End</span>
                <input type="time" className="agent-name-input" value={adminConfig.work_end}
                  onChange={e => setAdminConfig(c => ({ ...c, work_end: e.target.value }))} />
              </label>
            </div>
            <label>
              <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>
                Work Days (comma-separated: 0=Mon … 6=Sun)
              </span>
              <input type="text" className="agent-name-input" value={adminConfig.work_days}
                onChange={e => setAdminConfig(c => ({ ...c, work_days: e.target.value }))} placeholder="0,1,2,3,4,5" />
            </label>
            <label>
              <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>Timezone</span>
              <input type="text" className="agent-name-input" value={adminConfig.timezone}
                onChange={e => setAdminConfig(c => ({ ...c, timezone: e.target.value }))} placeholder="Asia/Kolkata" />
            </label>
            <label>
              <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>
                Avg Call Resolution Time (seconds) — used for queue wait estimates
              </span>
              <input type="number" className="agent-name-input" value={adminConfig.avg_resolution_seconds} min={30}
                onChange={e => setAdminConfig(c => ({ ...c, avg_resolution_seconds: parseInt(e.target.value, 10) || 300 }))} />
            </label>
            <button className="btn btn-primary" style={{ justifyContent: 'center' }} onClick={saveAdminConfig}>
              {adminSaved ? '✓ Saved!' : 'Save Settings'}
            </button>
          </div>
        )}
      </div>

      {/* ── Department Agents ───────────────────────────────────────────── */}
      <div className="glass-card-static" style={{ marginBottom: '1.5rem' }}>
        <div className="queue-dashboard-title">
          <h3>Department Agents</h3>
          <span className="queue-count-badge">{deptAgents.length} registered</span>
        </div>
        <div className="agent-list">
          {deptAgents.length === 0 && (
            <div className="queue-empty">No agents registered in this department</div>
          )}
          {deptAgents.map((a) => (
            <div key={a.agent_identity} className="agent-list-item">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                <span className="agent-seq-badge" style={{ minWidth: '1.8rem', textAlign: 'center', padding: '0.2rem 0.5rem' }}>
                  {a.sequence_number}
                </span>
                <span className="agent-list-name">
                  {a.agent_name}
                  {a.agent_identity === agentIdentity ? ' (You)' : ''}
                </span>
              </div>
              <span className={`agent-status-badge ${a.status === 'online' ? 'badge-online' : a.status === 'busy' ? 'badge-busy' : 'badge-offline'}`}>
                <span
                  className="status-dot"
                  style={{
                    background:
                      a.status === 'online' ? 'var(--accent-emerald)' :
                      a.status === 'busy' ? 'var(--accent-amber)' :
                      'var(--text-muted)',
                    width: '6px', height: '6px', animation: 'none',
                  }}
                />
                {a.status === 'online' ? 'Online' : a.status === 'busy' ? 'On a call' : a.status}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Call Queue ───────────────────────────────────────────────────── */}
      <div className="glass-card-static">
        <div className="queue-dashboard-title">
          <h3>Call Queue — {department}</h3>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <span className="queue-count-badge">{queueCallers.length} waiting</span>
            <button
              onClick={() => {
                setQueueCallers([]);
                setTimeout(async () => {
                  try {
                    const res = await fetch(`${API}/cc/queue?department=${encodeURIComponent(department)}`, {
                      headers: { 'ngrok-skip-browser-warning': '1' },
                    });
                    if (res.ok) {
                      const data = await res.json();
                      setQueueCallers(data.callers || []);
                    }
                  } catch (e) { /* ignore */ }
                }, 100);
              }}
              className="btn btn-ghost"
              style={{ fontSize: '0.7rem', padding: '0.3rem 0.6rem', minWidth: '40px' }}
              title="Refresh queue"
            >
              🔄 Refresh
            </button>
          </div>
        </div>
        <div className="queue-list">
          {queueCallers.length === 0 && (
            <div className="queue-empty">🎉 No callers waiting — all clear!</div>
          )}
          {queueCallers.map((caller) => (
            <div key={caller.session_id} className="queue-item">
              <div className="queue-item-info">
                <span className="queue-caller-id">📞 {caller.caller_id}</span>
                <span className="queue-meta">
                  #{caller.position} · Waiting {caller.wait_sec}s
                  {caller.user_email && ` · ${caller.user_email}`}
                </span>
              </div>
              {phase === 'online' && (
                <button
                  className="btn btn-success"
                  onClick={() => handleAcceptCall(caller)}
                  style={{ fontSize: '0.78rem', padding: '0.4rem 0.8rem' }}
                >
                  ✓ Accept
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
