import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useRoomContext,
  useParticipants,
} from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';

/* ═══════════════════════════════════════════════════════════════════════════════
   BACKEND_BASE — resolves to the Vite proxy or the ngrok URL
   ═══════════════════════════════════════════════════════════════════════════════ */
const API = import.meta.env.VITE_BACKEND_URL || '';  // empty = same origin (Vite proxy handles it)

/* ═══════════════════════════════════════════════════════════════════════════════
   Audio Visualizer Component — Pulsing concentric rings
   State drives the animation class: idle | listening | speaking | routing
   ═══════════════════════════════════════════════════════════════════════════════ */
function AudioVisualizer({ state, icon }) {
  return (
    <div className={`audio-visualizer ${state}`}>
      <div className="viz-ring" />
      <div className="viz-ring" />
      <div className="viz-ring" />
      <div className="viz-ring" />
      <div className="viz-core">{icon || '🎙️'}</div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   IVR Flow — Runs locally before connecting to LiveKit
   Handles: TTS playback, speech recognition, Gemini classification, routing
   ═══════════════════════════════════════════════════════════════════════════════ */
function IvrFlow({ onRouted, onEnd }) {
  const [ivrState, setIvrState] = useState('greeting');
  const [transcript, setTranscript] = useState('');
  const [statusText, setStatusText] = useState('Connecting...');
  const [detailText, setDetailText] = useState('');
  const [routingResult, setRoutingResult] = useState(null);

  const recognitionRef = useRef(null);
  const silenceTimerRef = useRef(null);
  const isListeningRef = useRef(false);

  // ── Play audio from a URL (used for TTS WAVs) ─────────────────────────
  const playAudio = useCallback((url) => {
    return new Promise((resolve, reject) => {
      const audio = new Audio(url);
      audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
      audio.onerror = reject;
      audio.play().catch(reject);
    });
  }, []);

  // ── Play TTS from backend endpoint ─────────────────────────────────────
  const playTtsFromEndpoint = useCallback(async (endpoint) => {
    try {
      const res = await fetch(`${API}${endpoint}`, {
        headers: { 'ngrok-skip-browser-warning': '1' }
      });
      if (!res.ok) throw new Error(`TTS fetch failed: ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      await playAudio(url);
    } catch (e) {
      console.warn('[TTS] Playback error:', e);
    }
  }, [playAudio]);

  // ── Web Speech API — potato-friendly STT (runs in browser, not on PC) ──
  const startListening = useCallback(() => {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
      console.warn('Web Speech API not supported');
      setDetailText('Speech recognition not available in this browser.');
      // Auto fallback if no speech API
      setTimeout(() => classifyAndRoute('Help me please'), 3000);
      return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    let finalTranscript = '';

    recognition.onresult = (event) => {
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);

      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          finalTranscript += event.results[i][0].transcript + ' ';
        } else {
          interim += event.results[i][0].transcript;
        }
      }
      setTranscript(finalTranscript + interim);

      silenceTimerRef.current = setTimeout(() => {
        if (finalTranscript.trim().length > 5) {
          recognition.stop();
          isListeningRef.current = false;
          setTranscript(finalTranscript.trim());
          handleConfirmation(finalTranscript.trim());
        }
      }, 5000);
    };

    recognition.onerror = (event) => {
      console.warn('Speech recognition error:', event.error);
      if (event.error === 'not-allowed') {
        setDetailText('Microphone access denied. Please allow microphone.');
      }
    };

    recognition.onend = () => {
      if (isListeningRef.current) {
        try { recognition.start(); } catch (e) { /* ignore */ }
      }
    };

    recognitionRef.current = recognition;
    isListeningRef.current = true;
    recognition.start();
  }, []);

  // ── Confirmation flow: "That's it sir?" ────────────────────────────────
  const handleConfirmation = useCallback(async (currentTranscript) => {
    setIvrState('confirming');
    setStatusText('Confirming your request...');
    setDetailText('');

    await playTtsFromEndpoint('/ivr/confirmation-prompt');
    setDetailText('Say "yes" to confirm, or continue speaking...');

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setTimeout(() => classifyAndRoute(currentTranscript), 3000);
      return;
    }

    const confirmRec = new SpeechRecognition();
    confirmRec.continuous = false;
    confirmRec.interimResults = false;
    confirmRec.lang = 'en-US';

    let answered = false;
    const autoTimer = setTimeout(() => {
      if (!answered) {
        answered = true;
        confirmRec.stop();
        classifyAndRoute(currentTranscript);
      }
    }, 8000);

    confirmRec.onresult = (event) => {
      const text = event.results[0][0].transcript.toLowerCase().trim();
      if (!answered) {
        answered = true;
        clearTimeout(autoTimer);
        confirmRec.stop();

        if (text.includes('yes') || text.includes('yeah') || text.includes('correct') || text.includes('right') || text.includes('yep')) {
          classifyAndRoute(currentTranscript);
        } else if (text.includes('no') || text.includes('nah') || text.includes('wait')) {
          setIvrState('listening');
          setStatusText('Listening...');
          setDetailText('Please describe your issue.');
          setTranscript('');
          startListening();
        } else {
          classifyAndRoute(currentTranscript + ' ' + text);
        }
      }
    };

    confirmRec.onerror = () => {
      if (!answered) {
        answered = true;
        clearTimeout(autoTimer);
        classifyAndRoute(currentTranscript);
      }
    };

    try { confirmRec.start(); } catch (e) {
      if (!answered) {
        answered = true;
        clearTimeout(autoTimer);
        classifyAndRoute(currentTranscript);
      }
    }
  }, [playTtsFromEndpoint, startListening]);

  // ── Classify intent with Gemini and route ──────────────────────────────
  const classifyAndRoute = useCallback(async (finalText) => {
    setIvrState('classifying');
    setStatusText('Analyzing your request...');
    setDetailText('Our AI is determining the best department for you.');

    // Generate a temporary session_id for the IVR processing (not the final LiveKit session)
    const tempSessionId = 'temp-' + Math.random().toString(36).substr(2, 9);
    
    try {
      const res = await fetch(`${API}/ivr/process`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'ngrok-skip-browser-warning': '1',
        },
        body: JSON.stringify({
          session_id: tempSessionId,
          room_id: 'ivr-room',
          transcript: finalText,
          caller_id: 'web-caller',
        }),
      });

      if (!res.ok) throw new Error(`IVR process error: ${res.status}`);
      const data = await res.json();

      setRoutingResult(data);
      setIvrState('routing');
      setStatusText(`Routing to ${data.department}`);
      setDetailText(data.routing_message);

      const deptSlug = data.department.toLowerCase().replace(/\s+/g, '-');
      await playTtsFromEndpoint(`/ivr/routing-audio/${deptSlug}`);

      onRouted(data);

    } catch (err) {
      console.error('[IVR] Classification failed:', err);
      setStatusText('Routing to Support Department');
      setDetailText('Connecting you to general support.');
      
      const fallback = { department: 'Support Department', urgency: 3, routing_message: 'Routing to Support Department.' };
      setRoutingResult(fallback);
      setIvrState('routing');
      
      await playTtsFromEndpoint(`/ivr/routing-audio/support-department`);
      onRouted(fallback);
    }
  }, [playTtsFromEndpoint, onRouted]);

  // ── Startup: Play greeting then start listening ────────────────────────
  useEffect(() => {
    let mounted = true;

    async function startIvr() {
      await new Promise(r => setTimeout(r, 800));
      if (!mounted) return;

      setIvrState('greeting');
      setStatusText('AI Assistant');
      setDetailText('Playing greeting...');

      await playTtsFromEndpoint('/ivr/greeting');
      if (!mounted) return;

      setIvrState('listening');
      setStatusText('Listening...');
      setDetailText('Please describe your issue. I\'ll route you to the best department.');
      startListening();
    }

    startIvr();

    return () => {
      mounted = false;
      isListeningRef.current = false;
      if (recognitionRef.current) {
        try { recognitionRef.current.stop(); } catch (e) { /* ignore */ }
      }
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    };
  }, []);

  const vizState = {
    greeting: 'speaking',
    listening: 'listening',
    confirming: 'speaking',
    classifying: 'routing',
    routing: 'routing',
  }[ivrState] || 'idle';

  const vizIcon = {
    greeting: '🤖',
    listening: '🎙️',
    confirming: '❓',
    classifying: '🧠',
    routing: '✨',
  }[ivrState] || '🎙️';

  return (
    <div className="ivr-screen glass-card-static">
      <AudioVisualizer state={vizState} icon={vizIcon} />

      <p className="ivr-status-text">{statusText}</p>
      <p className="ivr-detail-text">{detailText}</p>

      {(ivrState === 'listening' || ivrState === 'confirming' || ivrState === 'classifying') && transcript && (
        <div className="ivr-transcript-box">
          <p className="ivr-transcript-label">Your words</p>
          <p className="ivr-transcript-text">"{transcript}"</p>
        </div>
      )}

      {routingResult && ivrState === 'routing' && (
        <div style={{ marginBottom: '1.5rem' }}>
          <span className="incoming-dept-badge" style={{ fontSize: '0.85rem', padding: '0.4rem 1rem' }}>
            {routingResult.department}
          </span>
          <UrgencyBar level={routingResult.urgency} />
        </div>
      )}

      <button className="btn btn-danger" onClick={onEnd}>
        ✕ Cancel Calling
      </button>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   ActiveRoomView — Rendered AFTER routing, when connected to LiveKit
   ═══════════════════════════════════════════════════════════════════════════════ */
function ActiveRoomView({ routingResult, onEnd }) {
  const room = useRoomContext();
  const participants = useParticipants();
  const agentConnected = participants.some(p => p.identity && p.identity.startsWith('agent-'));
  const activeAudioRef = useRef(null);

  // Stop any playing TTS audio as soon as the agent connects
  useEffect(() => {
    if (agentConnected && activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }
  }, [agentConnected]);

  useEffect(() => {
    const handleData = (payload, _participant, _kind, topic) => {
      if (agentConnected || topic !== 'tts') return;
      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text);
        if (data.action === 'play_tts' && data.text) {
          fetch(`${API}/tts/speak`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
            body: JSON.stringify({ text: data.text, voice: 'en_US-ryan-high', room_id: room.name }),
          })
          .then(r => r.blob())
          .then(blob => {
            if (agentConnected) return; // Check again in case state changed during fetch
            
            // Stop any existing audio before playing a new one
            if (activeAudioRef.current) {
              activeAudioRef.current.pause();
            }
            
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            activeAudioRef.current = audio;
            
            audio.play().catch(() => {});
            audio.onended = () => {
              URL.revokeObjectURL(url);
              if (activeAudioRef.current === audio) {
                activeAudioRef.current = null;
              }
            };
          })
          .catch(e => console.warn('[TTS] Data channel play error:', e));
        }
      } catch (e) { /* ignore parse errors */ }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => room.off(RoomEvent.DataReceived, handleData);
  }, [room, agentConnected]);

  return (
    <div className="ivr-screen glass-card-static">
      <AudioVisualizer state={agentConnected ? 'listening' : 'routing'} icon={agentConnected ? '🟢' : '⏳'} />
      
      {!agentConnected ? (
        <>
          <p className="ivr-status-text">Waiting in {routingResult?.department} Queue</p>
          <p className="ivr-detail-text">Please hold for the next available agent. Audio announcements will play automatically.</p>
          {routingResult && (
            <div style={{ marginBottom: '1.5rem' }}>
              <span className="incoming-dept-badge" style={{ fontSize: '0.85rem', padding: '0.4rem 1rem' }}>
                {routingResult.department}
              </span>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="ivr-status-text" style={{ color: 'var(--accent-emerald)' }}>Connected to Agent</p>
          <p className="ivr-detail-text">You are now speaking with a live agent.</p>
        </>
      )}

      <button className="btn btn-danger" onClick={() => { room.disconnect(); onEnd(); }}>
        ✕ End Call
      </button>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   Urgency Bar — Visual 1-5 indicator
   ═══════════════════════════════════════════════════════════════════════════════ */
function UrgencyBar({ level }) {
  return (
    <div className="urgency-bar" style={{ justifyContent: 'center', marginTop: '0.5rem' }}>
      {[1, 2, 3, 4, 5].map(i => (
        <div
          key={i}
          className={`urgency-dot ${i <= level ? 'active' : ''} ${
            level <= 2 ? 'low' : level <= 3 ? 'medium' : 'high'
          }`}
        />
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   CallerView — Top-level caller component
   States: idle → calling (IVR) → routed/connected
   ═══════════════════════════════════════════════════════════════════════════════ */
export function CallerView() {
  const [phase, setPhase] = useState('idle'); // idle | ivr | requesting | active | error
  const [sessionData, setSessionData] = useState({});
  const [error, setError] = useState('');

  const normalizeDepartmentForCC = useCallback((dept) => {
    if (!dept) return 'General Support';
    const normalized = dept.trim().toLowerCase();
    if (normalized === 'tech department') return 'Technical Department';
    if (normalized === 'support department') return 'General Support';
    return dept;
  }, []);

  const buildAnonymousEmail = useCallback(() => {
    const stamp = Date.now().toString(36);
    const rand = Math.random().toString(36).slice(2, 8);
    return `ai-assist-${stamp}-${rand}@example.local`;
  }, []);

  const endCall = useCallback(() => {
    if (sessionData.sessionId) {
      fetch(`${API}/cc/call/${sessionData.sessionId}`, {
        method: 'DELETE',
        headers: { 'ngrok-skip-browser-warning': '1' },
      }).catch(() => {});
    }
    setSessionData({});
    setPhase('idle');
    setError('');
  }, [sessionData.sessionId]);

  const handleRouted = useCallback((result) => {
    console.log('[IVR] Routed:', result);
    // The caller stays in the room — agent will join the same room via accept-call
  }, []);

  // ── Idle: Big call button ──────────────────────────────────────────────
  if (phase === 'idle' || phase === 'error') {
    return (
      <div className="caller-idle glass-card-static">
        {error && (
          <div className="error-toast">
            ⚠️ {error}
          </div>
        )}
        <h2>Need Help?</h2>
        <p className="subtitle">
          Call our AI assistant. It will understand your issue and
          route you to the perfect department — instantly.
        </p>
        <button className="call-button" onClick={() => setPhase('ivr')} id="start-call-btn">
          📞
        </button>
        <p className="call-button-label">Tap to call</p>
      </div>
    );
  }

  // ── IVR Phase: Runs locally without LiveKit ────────────────────────────
  if (phase === 'ivr') {
    return (
      <IvrFlow 
        onRouted={async (routingResult) => {
          try {
            // Now that we have the intent, get the LiveKit token and join queue!
            setPhase('requesting');
            const mappedDepartment = normalizeDepartmentForCC(routingResult.department);
            const anonEmail = buildAnonymousEmail();
            const res = await fetch(`${API}/cc/call`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
              body: JSON.stringify({
                email: anonEmail,
                department: mappedDepartment,
                skip_outbound: true,
              }),
            });
            if (!res.ok) throw new Error(`Server error: ${res.status}`);
            const data = await res.json();
            if (data.rejected) {
              setError(data.offline_message || 'We are currently closed.');
              setPhase('error');
              return;
            }
            setSessionData({
              token: data.token,
              url: data.url || data.livekit_url,
              roomName: data.room,
              sessionId: data.session_id,
              identity: data.caller_identity || data.identity,
              routingResult: { ...routingResult, department: mappedDepartment }
            });
            setPhase('active');
          } catch (e) {
            setError(e.message);
            setPhase('error');
          }
        }} 
        onEnd={endCall} 
      />
    );
  }

  // ── Active call (IVR + Connected) ──────────────────────────────────────
  if (phase === 'active' && sessionData.token) {
    return (
      <LiveKitRoom
        video={false}
        audio={true}
        token={sessionData.token}
        serverUrl={sessionData.url}
        connect={true}
        onDisconnected={endCall}
      >
        <RoomAudioRenderer />
        <ActiveRoomView
          routingResult={sessionData.routingResult}
          onEnd={endCall}
        />
      </LiveKitRoom>
    );
  }

  return null;
}
