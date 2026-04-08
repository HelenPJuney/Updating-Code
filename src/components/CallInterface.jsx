import React, { useEffect, useCallback, useRef } from 'react';
import { 
  LiveKitRoom, 
  RoomAudioRenderer, 
  DisconnectButton,
  useRoomContext,
  useParticipants
} from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';

function ActiveRoomLayout({ onHardTeardown }) {
  const room = useRoomContext();
  const participants = useParticipants();
  const audioContextRef = useRef(null);
  
  // Identify if agent is presence
  const agentConnected = participants.some(p => p.identity && p.identity.includes('helen-receiver'));

  useEffect(() => {
    const handleData = (payload, participant, kind, topic) => {
      // Only handle TTS if we are in the queue state (agent not connected)
      // or if you want it always, remove the check. User said "peoples who are in the queue"
      if (agentConnected) return; 

      if (topic !== 'tts') return;

      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text);
        
        if (data.action === 'play_tts' && data.text) {
          console.log(`[TTS] Requesting audio for: "${data.text.substring(0, 40)}${data.text.length > 40 ? '...' : ''}"`);
          
          fetch('/tts/speak', {
            method: 'POST',
            headers: { 
              'Content-Type': 'application/json',
              'ngrok-skip-browser-warning': '1' 
            },
            body: JSON.stringify({ 
              text: data.text, 
              voice: 'en_US-ryan-high',
              room_id: room.name
            })
          })
          .then(async res => {
            if (!res.ok) {
                const errText = await res.text();
                throw new Error(`TTS fetch error: ${res.status} - ${errText}`);
            }
            console.log('[TTS] WAV received, playing...');
            return res.blob();
          })
          .then(blob => {
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            
            audio.play().catch(err => {
              console.warn('[TTS] Autoplay blocked or failed:', err);
              // Store url in a global or state if we want to retry but usually user interaction is required
            });
            
            audio.onended = () => {
                URL.revokeObjectURL(url);
                console.log('[TTS] Playback finished');
            };
          })
          .catch(err => console.error('[TTS] Processing failed:', err));
        }
      } catch (err) {
        console.warn('[TTS] Data parse error:', err);
      }
    };

    const handleDisconnect = () => {
      console.log('[Room] Disconnected');
      onHardTeardown();
    };

    room.on(RoomEvent.DataReceived, handleData);
    room.on(RoomEvent.Disconnected, handleDisconnect);

    return () => {
      room.off(RoomEvent.DataReceived, handleData);
      room.off(RoomEvent.Disconnected, handleDisconnect);
    };
  }, [room, agentConnected, onHardTeardown]);

  return (
    <div className="call-interface">
      {!agentConnected ? (
        <div className="status-queued">
          <div className="queue-animation">
            <span className="queue-spinner">🕒</span>
          </div>
          <h2>Waiting in Queue</h2>
          <p className="queue-msg">
            Please hold... An agent will be with you shortly.
            <br/>
            <small style={{ color: '#8b949e' }}>Announcement audio will play automatically.</small>
          </p>
        </div>
      ) : (
        <div className="status-connected">
          <h2 style={{ color: '#56d364' }}>🟢 Connected to Agent</h2>
          <div style={{ margin: '2rem 0', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div className="audio-visualizer-mini">
              <p style={{ color: '#a0aec0' }}>Voice Active</p>
            </div>
          </div>
        </div>
      )}
      
      <div style={{ marginTop: '2rem' }}>
        <DisconnectButton className="call-btn end-btn">
          End Call
        </DisconnectButton>
      </div>
    </div>
  );
}

export function CallInterface({ sessionData, onTeardown }) {
  if (!sessionData.token || !sessionData.url) return null;

  return (
    <LiveKitRoom
      video={false}
      audio={true}
      token={sessionData.token}
      serverUrl={sessionData.url}
      connect={true}
      onDisconnected={onTeardown}
    >
      {/* Renders AI/TTS voice/Agent voice properly */}
      <RoomAudioRenderer /> 
      <ActiveRoomLayout onHardTeardown={onTeardown} />
    </LiveKitRoom>
  );
}
