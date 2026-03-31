import { useState } from 'react'
import { Play, Activity } from 'lucide-react'
import { api } from '../../lib/api.js'
import { Button } from '../ui/Button.jsx'
import styles from './Tabs.module.css'

export function PipelineTab() {
  const [form, setForm] = useState({ lang: 'en', source: 'browser', caller_number: '+15551234567', priority: 0 })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const run = async () => {
    setLoading(true)
    const [r1, r2] = await Promise.all([
      api.routingDecision({ ...form, priority: Number(form.priority) }),
      api.health(),
    ])
    setLoading(false)
    setResult({ routing: r1, health: r2 })
  }

  return (
    <div className={styles.panel}>
      <div className={styles.formGrid}>
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}><Play size={13} /> Pipeline Test</h3>
          <div className={styles.row2}>
            <div className={styles.field}>
              <label>Language</label>
              <select value={form.lang} onChange={e => set('lang', e.target.value)}>
                <option value="en">English</option>
                <option value="es">Spanish</option>
                <option value="hi">Hindi</option>
              </select>
            </div>
            <div className={styles.field}>
              <label>Source</label>
              <select value={form.source} onChange={e => set('source', e.target.value)}>
                <option value="browser">browser</option>
                <option value="sip_inbound">sip_inbound</option>
                <option value="sip_outbound">sip_outbound</option>
              </select>
            </div>
          </div>
          <div className={styles.row2}>
            <div className={styles.field}>
              <label>Caller Number</label>
              <input value={form.caller_number} onChange={e => set('caller_number', e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>Priority</label>
              <input type="number" min="0" max="10" value={form.priority} onChange={e => set('priority', e.target.value)} />
            </div>
          </div>
          <Button variant="primary" onClick={run} loading={loading}>
            <Play size={13} /> Run Pipeline Test
          </Button>
        </div>
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}><Activity size={13} /> Result</h3>
          {!result ? (
            <div style={{ color: 'var(--muted)', fontSize: 13, padding: '8px 0' }}>
              Click "Run Pipeline Test" to see the routing decision and system health.
            </div>
          ) : (
            <div className={styles.pipelineResult}>
              {result.routing.ok ? (
                <>
                  <div className={styles.resultSection}>
                    <div className={styles.resultLabel}>Routing Decision</div>
                    <Row label="Rule"     value={result.routing.data.rule_name} color="indigo" />
                    <Row label="Queue"    value={result.routing.data.queue_name} color="cyan" />
                    <Row label="Fallback" value={result.routing.data.fallback_action} />
                    <Row label="Skills"   value={(result.routing.data.required_skills||[]).join(', ') || 'none'} />
                    <Row label="AI Mode"  value={result.routing.data.ai_config?.mode ?? 'none'} />
                  </div>
                  <div className={styles.resultSection}>
                    <div className={styles.resultLabel}>System Health</div>
                    <Row label="Kafka"     value={result.health.data.kafka_active ? '✓ active' : '✗ down'} color={result.health.data.kafka_active ? 'emerald' : 'rose'} />
                    <Row label="Scheduler" value={result.health.data.scheduler ?? '—'} />
                    <Row label="WS Subs"   value={result.health.data.ws_subscribers ?? '—'} />
                    <Row label="Rules"     value={`${result.health.data.rules_loaded ?? 0} loaded`} />
                  </div>
                </>
              ) : (
                <div style={{ color: 'var(--rose)', fontSize: 12 }}>
                  Error: {JSON.stringify(result.routing.data)}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ label, value, color }) {
  const colorMap = { indigo: 'var(--indigo)', emerald: 'var(--emerald)', rose: 'var(--rose)', cyan: 'var(--cyan)' }
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: '1px solid rgba(99,102,241,0.06)', fontSize: 12 }}>
      <span style={{ color: 'var(--muted)' }}>{label}</span>
      <span className="mono" style={{ color: colorMap[color] ?? 'var(--text)', fontWeight: 600 }}>{value}</span>
    </div>
  )
}
