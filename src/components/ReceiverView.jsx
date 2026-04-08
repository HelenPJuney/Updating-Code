import React, { useState, useEffect, useCallback } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useRoomContext,
} from '@livekit/components-react';

const API = import.meta.env.VITE_BACKEND_URL || '';

/* ═══════════════════════════════════════════════════════════════════════════════
   UrgencyBar — Visual 1-5 urgency indicator
   ═══════════════════════════════════════════════════════════════════════════════ */
function UrgencyBar({ level }) {
  if (!level) return null;
  return (
    <div className="urgency-bar">
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
   ActiveCallLayout — Agent's in-call experience
   ═══════════════════════════════════════════════════════════════════════════════ */
function ActiveCallLayout({ onEnd, currentCall, updateAgentStatus }) {
  const room = useRoomContext();
  const [customMsg, setCustomMsg] = useState('');

  useEffect(() => {
    const handleDisconnect = () => onEnd();
    room.on('disconnected', handleDisconnect);
    return () => room.off('disconnected', handleDisconnect);
  }, [room, onEnd]);

  const sendCustomMessage = (msgText) => {
    const finalMsg = msgText || customMsg;
    if (finalMsg.trim()) {
      updateAgentStatus('busy', finalMsg);
      if (!msgText) setCustomMsg('');
    }
  };

  return (
    <div className="agent-active-call">
      <div className="active-call-header">
        <span className="active-call-dot" />
        <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>Active Call</h3>
      </div>

      <div style={{
        background: 'rgba(15, 20, 35, 0.5)',
        border: '1px solid var(--border-glass)',
        borderRadius: 'var(--radius-md)',
        padding: '1rem',
        marginBottom: '1rem',
      }}>
        <p style={{ color: 'var(--accent-cyan)', fontSize: '0.85rem', fontWeight: 500 }}>
          📞 {currentCall?.caller_id || 'Unknown Caller'}
        </p>
        {currentCall?.department && (
          <span className="incoming-dept-badge" style={{ marginTop: '0.5rem', display: 'inline-block' }}>
            {currentCall.department}
          </span>
        )}
        {currentCall?.urgency && (
          <div style={{ marginTop: '0.4rem' }}>
            <UrgencyBar level={currentCall.urgency} />
          </div>
        )}
      </div>

      {/* Queue announcement control */}
      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        Notify Waiting Callers
      </p>
      
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        <button className="btn badge-online" style={{ padding: '0.3rem 0.6rem', fontSize: '0.75rem' }} onClick={() => sendCustomMessage("Please wait 2 minutes.")}>Wait 2 Mins</button>
        <button className="btn badge-online" style={{ padding: '0.3rem 0.6rem', fontSize: '0.75rem' }} onClick={() => sendCustomMessage("Please wait 5 minutes.")}>Wait 5 Mins</button>
        <button className="btn badge-busy" style={{ padding: '0.3rem 0.6rem', fontSize: '0.75rem' }} onClick={() => sendCustomMessage("We are handling an emergency, thank you for holding.")}>Emergency Delay</button>
      </div>

      <div className="custom-msg-row">
        <input
          className="custom-msg-input"
          type="text"
          value={customMsg}
          onChange={e => setCustomMsg(e.target.value)}
          placeholder="Or type custom announcement..."
          onKeyDown={e => e.key === 'Enter' && sendCustomMessage()}
        />
        <button className="btn btn-primary" onClick={() => sendCustomMessage()}>Send</button>
      </div>

      <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
        <button className="btn btn-danger" onClick={() => {
          room.disconnect();
          onEnd();
        }}>
          ✕ End Call
        </button>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   ReceiverView — Agent dashboard with queue monitoring & call acceptance
   States: offline → online (monitoring) → connected (in-call)
   ═══════════════════════════════════════════════════════════════════════════════ */
export function ReceiverView() {
  const [status, setStatus] = useState('offline'); // offline | online | connected
  const [selectedDept, setSelectedDept] = useState('All Departments'); // new state for agent's department
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [queue, setQueue] = useState([]);
  const [ringing, setRinging] = useState([]);
  const [tokenData, setTokenData] = useState({});
  const [currentCall, setCurrentCall] = useState(null);

  const DEPARTMENTS = [
    'All Departments',
    'Tech Department',
    'Billing Department',
    'Sales Department',
    'Support Department'
  ];

  // ── Agent status update ────────────────────────────────────────────────
  const updateAgentStatus = useCallback(async (newStatus, customMessage = null) => {
    try {
      await fetch(`${API}/livekit/agent-status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ status: newStatus, custom_message: customMessage }),
      });
    } catch (e) {
      console.error('Status update failed:', e);
    }
  }, []);

  // ── Queue polling ──────────────────────────────────────────────────────
  useEffect(() => {
    if (status !== 'online' && status !== 'connected') return;
    const poll = setInterval(async () => {
      try {
        const res = await fetch(`${API}/livekit/queue-info`, {
          headers: { 'ngrok-skip-browser-warning': '1' },
        });
        if (res.ok) {
          const data = await res.json();
          setQueue(data.callers || []);
          setRinging(data.ringing || []);
        }
      } catch (e) { /* silent */ }
    }, 2000);
    return () => clearInterval(poll);
  }, [status]);

  const goOnline = () => {
    setStatus('online');
    updateAgentStatus('available', '');
  };

  const goOffline = () => {
    setStatus('offline');
    setTokenData({});
    setCurrentCall(null);
    updateAgentStatus('offline', '');
  };

  const endCall = () => {
    setTokenData({});
    setCurrentCall(null);
    setStatus('online');
    updateAgentStatus('available', '');
  };

  const acceptCall = async (caller) => {
    try {
      const res = await fetch(`${API}/livekit/accept-call/${caller.session_id}?identity=helen-receiver&name=Helen`, {
        method: 'POST',
        headers: { 'ngrok-skip-browser-warning': '1' },
      });
      if (res.ok) {
        const data = await res.json();
        setTokenData({
          token: data.token,
          url: data.url,
          room: data.room,
        });
        setCurrentCall(caller);
        setStatus('connected');
        updateAgentStatus('busy', '');
      }
    } catch (e) {
      console.error('Accept call failed:', e);
    }
  };

  const declineCall = async (caller) => {
    try {
      await fetch(`${API}/livekit/decline-call/${caller.session_id}`, {
        method: 'POST',
        headers: { 'ngrok-skip-browser-warning': '1' },
      });
      setRinging(r => r.filter(x => x.session_id !== caller.session_id));
    } catch (e) { /* ignore */ }
  };

  // ── Queue Dashboard Component ──────────────────────────────────────────
  const QueueDashboard = () => {
    // Filter queue based on agent's selected department
    const displayQueue = selectedDept === 'All Departments' 
      ? queue 
      : queue.filter(q => q.department === selectedDept || !q.department);

    return (
      <div className="queue-dashboard">
        <div className="queue-dashboard-title">
          <h3>Call Queue {selectedDept !== 'All Departments' && `(${selectedDept})`}</h3>
          <span className="queue-count-badge">{displayQueue.length}</span>
        </div>
        {displayQueue.length === 0 ? (
          <div className="queue-empty">No callers waiting in this queue</div>
        ) : (
          <div className="queue-list">
            {displayQueue.map(q => (
              <div key={q.session_id} className="queue-item">
                <div className="queue-item-info">
                  <span className="queue-caller-id">📞 {q.caller_id}</span>
                  <span className="queue-meta">
                    Position #{q.position} · Waiting {Math.round(q.wait_sec)}s
                    {q.department && ` · ${q.department}`}
                  </span>
                </div>
                {status === 'online' && (
                  <button className="btn btn-success" onClick={() => acceptCall(q)}>
                    Accept
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // Render based on agent status
  // ═══════════════════════════════════════════════════════════════════════════

  // OFFLINE
  if (status === 'offline') {
    return (
      <div className="agent-panel glass-card-static" style={{ textAlign: 'center' }}>
        <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🎧</div>
        <h2 style={{ marginBottom: '0.5rem' }}>Agent Dashboard</h2>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
          Select your department and go online to start receiving calls
        </p>

        <div style={{ marginBottom: '2rem', textAlign: 'left', maxWidth: '300px', margin: '0 auto 2rem', position: 'relative' }}>
          <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.85rem', textAlign: 'center' }}>
            Select Department
          </label>
          <div 
            onClick={() => setIsDropdownOpen(!isDropdownOpen)}
            style={{
              width: '100%', 
              padding: '0.9rem', 
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(15, 20, 35, 0.8)',
              border: '1px solid var(--border-glass)',
              color: 'var(--text-primary)',
              fontSize: '0.95rem',
              cursor: 'pointer',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}
          >
            {selectedDept} <span>{isDropdownOpen ? '▲' : '▼'}</span>
          </div>
          <div 
            style={{
              display: isDropdownOpen ? 'block' : 'none',
              position: 'absolute',
              top: '100%',
              left: 0,
              right: 0,
              background: '#0a0d1a',
              border: '1px solid var(--accent-cyan)',
              borderRadius: 'var(--radius-sm)',
              marginTop: '0.3rem',
              zIndex: 50,
              overflow: 'hidden',
              boxShadow: '0 8px 16px rgba(0,0,0,0.5)'
            }}
          >
            {DEPARTMENTS.map(dept => (
              <div 
                key={dept} 
                onClick={() => { 
                  setSelectedDept(dept); 
                  setIsDropdownOpen(false);
                }}
                style={{
                  padding: '0.8rem 1rem',
                  cursor: 'pointer',
                  borderBottom: '1px solid rgba(255,255,255,0.05)',
                  background: selectedDept === dept ? 'rgba(0, 240, 255, 0.1)' : 'transparent',
                  color: selectedDept === dept ? 'var(--accent-cyan)' : 'var(--text-primary)',
                }}
                onMouseEnter={e => e.target.style.background = 'rgba(0, 240, 255, 0.15)'}
                onMouseLeave={e => e.target.style.background = selectedDept === dept ? 'rgba(0, 240, 255, 0.1)' : 'transparent'}
              >
                {dept}
              </div>
            ))}
          </div>
        </div>

        <button className="btn btn-success" onClick={goOnline} id="go-online-btn" style={{ padding: '0.8rem 2rem', fontSize: '1rem' }}>
          Go Online as {selectedDept === 'All Departments' ? 'General Agent' : selectedDept.split(' ')[0]}
        </button>
      </div>
    );
  }

  // ONLINE — Monitoring queue
  if (status === 'online') {
    // Filter ringing calls for the selected department
    const displayRinging = selectedDept === 'All Departments'
      ? ringing
      : ringing.filter(r => r.department === selectedDept || !r.department);

    return (
      <div className="agent-panel glass-card-static">
        <div className="agent-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <h2>Agent Panel</h2>
            <span className="agent-status-badge badge-online">
              <span className="status-dot" /> Online ({selectedDept === 'All Departments' ? 'All' : selectedDept.split(' ')[0]})
            </span>
          </div>
          <button className="btn btn-ghost" onClick={goOffline}>Go Offline</button>
        </div>

        {/* Incoming call pop-up */}
        {displayRinging.map(caller => (
          <div key={caller.session_id} className="incoming-call-popup">
            <div className="incoming-label">
              <span className="ring-indicator" />
              INCOMING CALL
            </div>
            <div className="incoming-caller-info">
              <div>
                <p className="incoming-caller-name">{caller.caller_id}</p>
                {caller.department && (
                  <span className="incoming-dept-badge">{caller.department}</span>
                )}
              </div>
              {caller.urgency && <UrgencyBar level={caller.urgency} />}
            </div>
            <div className="incoming-actions">
              <button className="btn btn-success" onClick={() => acceptCall(caller)}>
                ✓ Accept
              </button>
              <button className="btn btn-danger" onClick={() => declineCall(caller)}>
                ✕ Decline
              </button>
            </div>
          </div>
        ))}

        <QueueDashboard />
      </div>
    );
  }

  // CONNECTED — In active call
  if (status === 'connected' && tokenData.token) {
    return (
      <div className="agent-panel glass-card-static">
        <div className="agent-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <h2>Agent Panel</h2>
            <span className="agent-status-badge badge-busy">
              ● Busy
            </span>
          </div>
        </div>

        <LiveKitRoom
          video={false}
          audio={true}
          token={tokenData.token}
          serverUrl={tokenData.url}
          connect={true}
          onDisconnected={endCall}
        >
          <RoomAudioRenderer />
          <ActiveCallLayout
            onEnd={endCall}
            currentCall={currentCall}
            updateAgentStatus={updateAgentStatus}
          />
        </LiveKitRoom>

        <QueueDashboard />
      </div>
    );
  }

  return null;
}
