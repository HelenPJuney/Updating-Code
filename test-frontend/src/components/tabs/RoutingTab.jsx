import { useState, useEffect } from 'react'
import { RefreshCw, RotateCcw, Zap } from 'lucide-react'
import { api } from '../../lib/api.js'
import { Button } from '../ui/Button.jsx'
import styles from './Tabs.module.css'

export function RoutingTab() {
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(false)
  const [testForm, setTestForm] = useState({ lang: 'en', source: 'browser', caller_number: '+15551234567', priority: 0 })
  const [testResult, setTestResult] = useState(null)
  const [reloading, setReloading] = useState(false)

  const set = (k, v) => setTestForm(f => ({ ...f, [k]: v }))

  const load = async () => {
    setLoading(true)
    const res = await api.routingRules()
    setLoading(false)
    if (res.ok) setRules(res.data.rules || [])
  }

  const reload = async () => {
    setReloading(true)
    const res = await api.reloadRules()
    setReloading(false)
    if (res.ok) load()
  }

  const test = async () => {
    const res = await api.routingDecision({ ...testForm, priority: Number(testForm.priority) })
    setTestResult(res)
  }

  useEffect(() => { load() }, [])

  return (
    <div className={styles.panel}>
      {/* Test widget */}
      <div className={styles.testBox}>
        <div className={styles.testTitle}><Zap size={13} /> Test Routing Decision</div>
        <div className={styles.testForm}>
          <div className={styles.field}>
            <label>Lang</label>
            <select value={testForm.lang} onChange={e => set('lang', e.target.value)}>
              <option value="en">en</option>
              <option value="es">es</option>
              <option value="hi">hi</option>
            </select>
          </div>
          <div className={styles.field}>
            <label>Source</label>
            <select value={testForm.source} onChange={e => set('source', e.target.value)}>
              <option value="browser">browser</option>
              <option value="sip_inbound">sip_inbound</option>
              <option value="sip_outbound">sip_outbound</option>
            </select>
          </div>
          <div className={styles.field}>
            <label>Caller Number</label>
            <input value={testForm.caller_number} onChange={e => set('caller_number', e.target.value)} />
          </div>
          <div className={styles.field}>
            <label>Priority</label>
            <input type="number" min="0" max="10" value={testForm.priority} onChange={e => set('priority', e.target.value)} />
          </div>
          <Button variant="primary" size="sm" onClick={test}>Test</Button>
        </div>
        {testResult && (
          <div className={`${styles.testResult} ${testResult.ok ? styles.testOk : styles.testErr}`}>
            {testResult.ok
              ? `✓ Rule: ${testResult.data.rule_name} → queue=${testResult.data.queue_name}, fallback=${testResult.data.fallback_action}, skills=[${(testResult.data.required_skills||[]).join(', ')}]`
              : `✗ ${JSON.stringify(testResult.data?.detail ?? testResult.data)}`
            }
          </div>
        )}
      </div>

      <div className={styles.tableToolbar}>
        <Button variant="ghost" size="sm" onClick={load} loading={loading}>
          <RefreshCw size={11} /> Refresh
        </Button>
        <Button variant="warning" size="sm" onClick={reload} loading={reloading}>
          <RotateCcw size={11} /> Reload from File
        </Button>
      </div>

      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>Priority</th>
              <th>Name</th>
              <th>Queue</th>
              <th>Conditions</th>
              <th>Skills</th>
              <th>Fallback</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 ? (
              <tr><td colSpan="7" className="empty-state">No rules loaded</td></tr>
            ) : rules.map(r => {
              const conds = Object.entries(r.conditions || {}).map(([k,v]) => `${k}: ${JSON.stringify(v)}`).join(' | ')
              const skills = (r.target?.required_skills || []).join(', ')
              return (
                <tr key={r.name}>
                  <td className="mono" style={{ fontWeight: 700, color: 'var(--indigo)' }}>{r.priority}</td>
                  <td style={{ fontWeight: 600 }}>{r.name}</td>
                  <td><span className="chip chip-indigo">{r.target?.queue_name ?? '—'}</span></td>
                  <td style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 220 }} className={styles.condCell}>{conds || '—'}</td>
                  <td style={{ fontSize: 11 }}>{skills || '—'}</td>
                  <td><span className="chip chip-muted">{r.target?.fallback_action ?? '—'}</span></td>
                  <td>
                    {r.enabled
                      ? <span className="chip chip-emerald">on</span>
                      : <span className="chip chip-muted">off</span>
                    }
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
