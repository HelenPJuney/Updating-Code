import { useState, useCallback } from 'react';

// Central state machine hook for session and room logic
export function useCallSession() {
  const [status, setStatus] = useState('idle'); // idle | requesting | queued | connected | error
  const [sessionData, setSessionData] = useState({ token: null, url: null, roomName: null, sessionId: null });
  const [errorMsg, setErrorMsg] = useState('');

  const startCall = async (agentId) => {
    try {
      setStatus('requesting');
      setErrorMsg('');
      
      // Use the actual backend endpoint for caller token
      const response = await fetch(`/livekit/caller-token?department=${agentId}`, {
        headers: { 'ngrok-skip-browser-warning': '1' } // Required for ngrok bypass
      });

      if (!response.ok) throw new Error(`Backend error: ${response.status}`);
      const data = await response.json();
      
      setSessionData({ 
        token: data.token, 
        url: data.url || 'wss://sch-natyyy4y.livekit.cloud',
        roomName: data.room,
        sessionId: data.session_id
      });
      
      setStatus(data.wait_seconds > 0 ? 'queued' : 'connected');
    } catch (error) {
      console.error('Call failed to start:', error);
      setErrorMsg(error.message);
      setStatus('error');
    }
  };

  const endCall = useCallback(() => {
    if (sessionData.sessionId) {
        // Cleanup caller queue on exit
        fetch(`/livekit/caller-queue/${sessionData.sessionId}`, {
            method: 'DELETE',
            headers: { 'ngrok-skip-browser-warning': '1' }
        }).catch(() => {});
    }
    setSessionData({ token: null, url: null, roomName: null, sessionId: null });
    setStatus('idle');
    setErrorMsg('');
  }, [sessionData.sessionId]);

  return { status, setStatus, sessionData, errorMsg, startCall, endCall };
}
