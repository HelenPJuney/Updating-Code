import React, { useState } from 'react';
import { UserDashboard } from './components/UserDashboard';
import { AgentDashboard } from './components/AgentDashboard';
import { CallerView as AiAssistView } from './components/CallerView';

/**
 * App — Root component with nav tabs for:
 *   1. Call In (UserDashboard) — email + department → queue
 *   2. Agent Panel (AgentDashboard) — multi-agent ops dashboard
 *   3. AI Assistant (CallerView) — legacy IVR flow
 *
 * Premium dark glassmorphism UI.
 */
function App() {
  const [tab, setTab] = useState('caller');

  return (
    <div className="app-shell">
      {/* ── Premium Nav Bar ──────────────────────────────────── */}
      <nav className="nav-bar">
        <div className="nav-brand">
          <div className="nav-logo">📞</div>
          <span className="nav-title">AI Call Center</span>
        </div>

        <div className="nav-tabs">
          <button
            id="tab-caller"
            className={`nav-tab ${tab === 'caller' ? 'active' : ''}`}
            onClick={() => setTab('caller')}
          >
            Call In
          </button>
          <button
            id="tab-agent"
            className={`nav-tab ${tab === 'agent' ? 'active' : ''}`}
            onClick={() => setTab('agent')}
          >
            Agent Panel
          </button>
          <button
            id="tab-ai-assist"
            className={`nav-tab ${tab === 'ai-assist' ? 'active' : ''}`}
            onClick={() => setTab('ai-assist')}
          >
            AI Assistant
          </button>
        </div>

        <div className="nav-status">
          <span className="status-dot" />
          System Online
        </div>
      </nav>

      {/* ── Main Content ─────────────────────────────────────── */}
      <main className="main-content">
        {tab === 'caller' && <UserDashboard />}
        {tab === 'agent' && <AgentDashboard />}
        {tab === 'ai-assist' && <AiAssistView />}
      </main>
    </div>
  );
}

export default App;
