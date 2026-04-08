import React, { useState, useEffect } from 'react';
import { 
  LiveKitRoom, 
  RoomAudioRenderer, 
  DisconnectButton,
  useRoomContext
} from '@livekit/components-react';

function ActiveReceiverLayout({ onHardTeardown, currentCall, updateAgentStatus }) {
  const room = useRoomContext();
  const [customMsg, setCustomMsg] = useState('');
  
  useEffect(() => {
    const handleDisconnect = () => onHardTeardown();
    room.on('disconnected', handleDisconnect);
    return () => {
      room.off('disconnected', handleDisconnect);
    };
  }, [room, onHardTeardown]);

  const handleSendMsg = () => {
    updateAgentStatus('busy', customMsg);
    alert('Custom wait message updated for waiting callers!');
  };

  return (
    <div className="call-interface">
      <h2 style={{ color: '#56d364' }}>🟢 Connected to Call</h2>
      <p style={{ margin: '0.5rem 0', color: '#a0aec0' }}>Active 1-on-1 session</p>
      
      <div style={{ background: '#0f1117', padding: '1rem', borderRadius: '8px', margin: '1rem 0' }}>
         <p style={{ color: '#90cdf4' }}>📞 Caller ID: {currentCall?.caller_id || 'Unknown'}</p>
         <p style={{ color: '#718096', fontSize: '0.9em' }}>Room: {currentCall?.room_id}</p>
      </div>

      <div style={{ background: '#1a202c', padding: '1rem', borderRadius: '8px', margin: '1rem 0' }}>
         <h4 style={{ margin: '0 0 0.5rem 0', color: '#cbd5e0' }}>Queue Announcement Control</h4>
         <div style={{ display: 'flex', gap: '0.5rem' }}>
           <input 
             type="text" 
             value={customMsg}
             onChange={e => setCustomMsg(e.target.value)}
             placeholder="e.g. 5 minutes or 'I will be right with you'"
             style={{ flex: 1, padding: '0.5rem', borderRadius: '4px', border: '1px solid #4a5568', background: '#2d3748', color: 'white' }}
           />
           <button onClick={handleSendMsg} style={{ background: '#3182ce', color: 'white', border: 'none', padding: '0.5rem 1rem', borderRadius: '4px', cursor: 'pointer' }}>
             Send
           </button>
         </div>
      </div>

      <div style={{ margin: '2rem 0', height: '100px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
         <p style={{color: '#a0aec0'}}>Audio Active</p>
      </div>

      <div style={{ marginTop: '2rem' }}>
        <DisconnectButton className="call-btn end-btn">
          End Call
        </DisconnectButton>
      </div>
    </div>
  );
}

export function ReceiverInterface() {
  const [status, setStatus] = useState('offline'); // offline | online | connected | error
  const [queue, setQueue] = useState([]);
  const [ringing, setRinging] = useState([]);
  const [tokenData, setTokenData] = useState({ token: null, url: null, room: null });
  const [currentCall, setCurrentCall] = useState(null);

  const updateAgentStatus = async (newStatus, customMessage = null) => {
    try {
      await fetch('/livekit/agent-status', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'ngrok-skip-browser-warning': '1' 
        },
        body: JSON.stringify({ status: newStatus, custom_message: customMessage })
      });
    } catch (e) {
      console.error("Failed to update status", e);
    }
  };

  useEffect(() => {
    if (status !== 'online' && status !== 'connected') return;

    const pollInterval = setInterval(async () => {
      try {
        const res = await fetch('/livekit/queue-info', {
          headers: { 'ngrok-skip-browser-warning': '1' }
        });
        if (res.ok) {
          const data = await res.json();
          setQueue(data.callers || []);
          setRinging(data.ringing || []);
        }
      } catch (err) {
        console.error("Queue poll failed:", err);
      }
    }, 2000);

    return () => clearInterval(pollInterval);
  }, [status]);

  const goOnline = () => {
    setStatus('online');
    updateAgentStatus('available', '');
  };

  const goOffline = () => {
    setStatus('offline');
    setTokenData({ token: null, url: null, room: null });
    setCurrentCall(null);
    updateAgentStatus('offline', '');
  };

  const endCall = () => {
    setTokenData({ token: null, url: null, room: null });
    setCurrentCall(null);
    setStatus('online'); // Go back to monitoring
    updateAgentStatus('available', '');
  };

  const acceptCall = async (caller) => {
    try {
      const res = await fetch(`/livekit/accept-call/${caller.session_id}?identity=helen-receiver&name=Helen`, {
        method: 'POST',
        headers: { 'ngrok-skip-browser-warning': '1' }
      });
      if (res.ok) {
        const data = await res.json();
        setTokenData({ 
            token: data.token, 
            url: data.url || 'wss://sch-natyyy4y.livekit.cloud', 
            room: data.room 
        });
        setCurrentCall(caller);
        setStatus('connected');
        updateAgentStatus('busy', '');
      } else {
        alert("Failed to accept call");
      }
    } catch (err) {
      console.error(err);
      alert("Error accepting call");
    }
  };

  const declineCall = async (caller) => {
    try {
      await fetch(`/livekit/decline-call/${caller.session_id}`, {
        method: 'POST',
        headers: { 'ngrok-skip-browser-warning': '1' }
      });
      // Will disappear from ringing on next poll
      setRinging(ringing.filter(r => r.session_id !== caller.session_id));
    } catch (e) {
      console.error(e);
    }
  };

  const queueDashboard = (
    <div style={{ background: '#0f1117', padding: '1rem', borderRadius: '8px', margin: '1rem 0' }}>
       <h3 style={{ margin: '0 0 1rem 0', color: '#e2e8f0' }}>Queue Dashboard</h3>
       <p style={{ marginBottom: '1rem', color: '#a0aec0' }}>Callers waiting: {queue.length}</p>
       {queue.length === 0 && <p style={{ color: '#718096' }}>No active callers.</p>}
       
       <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
         {queue.map(q => (
           <div key={q.session_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#1a202c', padding: '0.8rem', borderRadius: '4px', marginBottom: '0.5rem' }}>
             <div>
               <p style={{ color: '#90cdf4', margin: 0 }}>📞 Caller ID: {q.caller_id}</p>
               <p style={{ color: '#a0aec0', fontSize: '0.8em', margin: 0 }}>Wait time: {q.wait_sec}s | Pos: {q.position}</p>
             </div>
             {status === 'online' && (
               <button onClick={() => acceptCall(q)} style={{ background: '#3182ce', color: 'white', border: 'none', padding: '0.5rem 1rem', borderRadius: '4px', cursor: 'pointer' }}>
                 Accept
               </button>
             )}
           </div>
         ))}
       </div>
    </div>
  );

  if (status === 'offline') {
    return (
      <div className="card" style={{ maxWidth: '600px', margin: '0 auto', textAlign: 'center' }}>
         <h2>Receiver Dashboard</h2>
         <p style={{ color: '#718096', marginBottom: '1.5rem' }}>Start listening for routed callers dynamically.</p>
         <button className="call-btn" onClick={goOnline}>🎧 Go Online (Listen for Calls)</button>
      </div>
    );
  }

  if (status === 'online') {
    return (
      <div className="card" style={{ maxWidth: '600px', margin: '0 auto', position: 'relative' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ color: '#56d364' }}>🟢 Receiver Online</h2>
            <button className="call-btn end-btn" style={{ padding: '0.4rem 1rem', background: '#e53e3e' }} onClick={goOffline}>Go Offline</button>
        </div>
        <p style={{ margin: '0.5rem 0', color: '#a0aec0' }}>Monitoring isolated caller queue...</p>
        
        {ringing.length > 0 && (
          <div style={{ 
            background: '#2b6cb0', 
            padding: '1.5rem', 
            borderRadius: '8px', 
            margin: '1rem 0',
            border: '2px solid #63b3ed',
            boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)'
          }}>
             <h3 style={{ margin: '0 0 1rem 0', color: 'white' }}>🔔 Incoming Call!</h3>
             <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
               <div>
                  <p style={{ margin: '0', color: '#bee3f8', fontSize: '1.1em', fontWeight: 'bold' }}>Caller ID: {ringing[0].caller_id}</p>
                  <p style={{ margin: '0', color: '#ebf8ff', fontSize: '0.9em' }}>Wait Time: {ringing[0].wait_sec}s</p>
               </div>
               <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button onClick={() => acceptCall(ringing[0])} style={{ background: '#48bb78', color: 'white', border: 'none', padding: '0.75rem 1.5rem', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}>
                    Accept
                  </button>
                  <button onClick={() => declineCall(ringing[0])} style={{ background: '#e53e3e', color: 'white', border: 'none', padding: '0.75rem 1.5rem', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}>
                    Decline
                  </button>
               </div>
             </div>
          </div>
        )}

        {queueDashboard}
      </div>
    );
  }

  if (status === 'connected') {
      return (
        <div className="card" style={{ maxWidth: '600px', margin: '0 auto' }}>
          <LiveKitRoom
            video={false}
            audio={true}
            token={tokenData.token}
            serverUrl={tokenData.url}
            connect={true}
            onDisconnected={endCall}
          >
            <ActiveReceiverLayout 
                onHardTeardown={endCall} 
                currentCall={currentCall} 
                updateAgentStatus={updateAgentStatus} 
            />
            <RoomAudioRenderer />
          </LiveKitRoom>
          {queueDashboard}
        </div>
      );
  }

  return null;
}
