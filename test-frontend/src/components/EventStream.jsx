import { useState, useRef, useEffect } from 'react'
import { Radio, Trash2 } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Card, CardHeader, CardBody } from './ui/Card.jsx'
import { Button } from './ui/Button.jsx'
import styles from './EventStream.module.css'

const FILTERS = [
  { id: 'all',             label: 'All' },
  { id: 'call',            label: 'Calls',     match: ['call_started','call_completed','call_failed','call_dlq'] },
  { id: 'routing_decision',label: 'Routing' },
  { id: 'escalation',      label: 'Escalation' },
  { id: 'scheduled_job',   label: 'Scheduled' },
  { id: 'queue_update',    label: 'Queue' },
  { id: 'system_status',   label: 'System' },
]

const TYPE_COLORS = {
  call_started:     styles.green,
  call_completed:   styles.blue,
  call_failed:      styles.red,
  call_dlq:         styles.red,
  queue_update:     styles.amber,
  routing_decision: styles.violet,
  escalation:       styles.orange,
  scheduled_job:    styles.cyan,
  system_status:    styles.muted,
}

function fmtBody(ev) {
  const d = { ...ev }
  delete d.type; delete d.ts
  const entries = Object.entries(d).slice(0, 5)
  return entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join('  ')
}

function fmtTime(ts) {
  if (!ts) return new Date().toLocaleTimeString('en', { hour12: false })
  return new Date(ts * 1000).toLocaleTimeString('en', { hour12: false })
}

export function EventStream() {
  const { events } = useDashboard()
  const [filter, setFilter] = useState('all')
  const [autoScroll, setAutoScroll] = useState(true)
  const [localEvents, setLocalEvents] = useState([])
  const feedRef = useRef(null)

  useEffect(() => {
    setLocalEvents(events.slice(0, 300))
  }, [events])

  useEffect(() => {
    if (autoScroll && feedRef.current) {
      feedRef.current.scrollTop = 0  // newest on top
    }
  }, [localEvents, autoScroll])

  const filtered = localEvents.filter(ev => {
    if (filter === 'all') return true
    const f = FILTERS.find(f => f.id === filter)
    if (!f) return true
    if (f.match) return f.match.includes(ev.type)
    return ev.type === filter
  })

  return (
    <Card style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <CardHeader
        title="Live Events"
        icon={Radio}
        right={
          <div className={styles.headerRight}>
            <label className={styles.autoScrollLabel}>
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={e => setAutoScroll(e.target.checked)}
                className={styles.checkbox}
              />
              Auto-scroll
            </label>
            <Button variant="ghost" size="sm" onClick={() => setLocalEvents([])}>
              <Trash2 size={11} /> Clear
            </Button>
          </div>
        }
      />
      <div className={styles.filters}>
        {FILTERS.map(f => (
          <button
            key={f.id}
            className={`${styles.chip} ${filter === f.id ? styles.chipActive : ''}`}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className={styles.feed} ref={feedRef}>
        {filtered.length === 0 ? (
          <div className="empty-state">
            <Radio size={20} />
            Waiting for events…
          </div>
        ) : (
          filtered.map((ev, i) => (
            <div key={i} className={styles.item}>
              <span className={styles.time}>{fmtTime(ev.ts)}</span>
              <span className={`${styles.type} ${TYPE_COLORS[ev.type] ?? styles.muted}`}>
                {ev.type}
              </span>
              <span className={styles.body}>{fmtBody(ev)}</span>
            </div>
          ))
        )}
      </div>
    </Card>
  )
}
