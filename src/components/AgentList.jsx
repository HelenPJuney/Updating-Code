import React from 'react';

// Added General Call (AI Router) and Categories mapping precisely to their prompt requests
const agents = [
  { id: 'general', name: 'General AI Agent', avatar: '🤖', desc: 'Faster Whisper Transcribe & LLM Router' },
  { id: 'sales', name: 'Sales Support AI', avatar: '💼', desc: 'Pre-sales inquiries & pricing' },
  { id: 'tech', name: 'Tech Support AI', avatar: '🛠️', desc: 'Troubleshooting & technical help' },
  { id: 'billing', name: 'Billing AI', avatar: '💳', desc: 'Invoice & account management' }
];

export function AgentList({ onCallAgent, disabled }) {
  return (
    <div className="agent-grid">
      {agents.map(agent => (
        <div key={agent.id} className="agent-card">
          <div className="agent-avatar">{agent.avatar}</div>
          <h3>{agent.name}</h3>
          <p style={{ color: '#8b949e', fontSize: '0.9rem' }}>{agent.desc}</p>
          <button 
            className="call-btn" 
            onClick={() => onCallAgent(agent.id)}
            disabled={disabled}
            style={agent.id === 'general' ? { background: '#2ea043' } : {}}
          >
            Call {agent.name.split(' ')[0]}
          </button>
        </div>
      ))}
    </div>
  );
}
