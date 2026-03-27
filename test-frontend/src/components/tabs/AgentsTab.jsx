import { useState } from 'react'
import { UserPlus, Trash2, RefreshCw } from 'lucide-react'
import { api } from '../../lib/api.js'
import { useDashboard } from '../../context/DashboardContext.jsx'
import { Button } from '../ui/Button.jsx'
import styles from './Tabs.module.css'

export function AgentsTab() {
  const { agents, refreshAgents } = useDashboard()
  const [form, setForm] = useState({ agent_id: '', name: '', skills: '', max_calls: 2, node_id: '' })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const register = async () => {
    if (!form.agent_id) { setResult({ ok: false, msg: 'Agent ID required' }); return }
    setLoading(true)
    const res = await api.registerAgent({
      agent_id: form.agent_id,
      name: form.name || form.agent_id,
      skills: form.skills.split(',').map(s => s.trim()).filter(Boolean),
      max_calls: Number(form.max_calls) || 2,
      node_id: form.node_id || undefined,
      available: true,
    })
    setLoading(false)
    if (res.ok) {
      setResult({ ok: true, msg: `Registered: ${res.data.agent_id}` })
      setForm({ agent_id: '', name: '', skills: '', max_calls: 2, node_id: '' })
      refreshAgents()
    } else {
      setResult({ ok: false, msg: JSON.stringify(res.data.detail ?? res.data) })
    }
  }

  const remove = async (id) => {
    await api.deregisterAgent(id)
    refreshAgents()
  }

  return (
    <div className={styles.panel}>
      <div className={styles.formGrid}>
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}><UserPlus size={13} /> Register Agent</h3>
          <div className={styles.row2}>
            <div className={styles.field}>
              <label>Agent ID</label>
              <input value={form.agent_id} onChange={e => set('agent_id', e.target.value)} placeholder="agent-001" />
            </div>
            <div className={styles.field}>
              <label>Display Name</label>
              <input value={form.name} onChange={e => set('name', e.target.value)} placeholder="John Smith" />
            </div>
          </div>
          <div className={styles.field}>
            <label>Skills (comma-separated)</label>
            <input value={form.skills} onChange={e => set('skills', e.target.value)} placeholder="english, billing, technical" />
          </div>
          <div className={styles.row2}>
            <div className={styles.field}>
              <label>Max Calls</label>
              <input type="number" min="1" max="10" value={form.max_calls} onChange={e => set('max_calls', e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>Node ID (optional)</label>
              <input value={form.node_id} onChange={e => set('node_id', e.target.value)} placeholder="participant-id" />
            </div>
          </div>
          <div className={styles.actions}>
            <Button variant="success" onClick={register} loading={loading}>
              <UserPlus size={13} /> Register
            </Button>
            {result && (
              <div className={`${styles.result} ${result.ok ? styles.resultOk : styles.resultErr}`}>
                {result.msg}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className={styles.tableToolbar}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)' }}>
          {agents.length} agent{agents.length !== 1 ? 's' : ''} registered
        </span>
        <Button variant="ghost" size="sm" onClick={refreshAgents}>
          <RefreshCw size={11} /> Refresh
        </Button>
      </div>

      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr><th>Agent ID</th><th>Name</th><th>Skills</th><th>Active</th><th>Max</th><th>Available</th><th></th></tr>
          </thead>
          <tbody>
            {agents.length === 0 ? (
              <tr><td colSpan="7" className="empty-state">No agents</td></tr>
            ) : agents.map(a => (
              <tr key={a.agent_id}>
                <td className="mono" style={{ fontSize: 11 }}>{a.agent_id}</td>
                <td style={{ fontWeight: 500 }}>{a.name || '—'}</td>
                <td>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {(a.skills || []).map(s => <span key={s} className="chip chip-indigo" style={{ fontSize: 10 }}>{s}</span>)}
                  </div>
                </td>
                <td className="mono">{a.active_calls || 0}</td>
                <td className="mono">{a.max_calls || 2}</td>
                <td>{a.available ? <span className="chip chip-emerald">yes</span> : <span className="chip chip-muted">no</span>}</td>
                <td>
                  <Button variant="danger" size="sm" onClick={() => remove(a.agent_id)}>
                    <Trash2 size={11} />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
