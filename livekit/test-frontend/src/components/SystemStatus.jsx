import { Activity } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Card, CardHeader, CardBody } from './ui/Card.jsx'
import styles from './SystemStatus.module.css'

const ROWS = [
  { key: 'kafka_active',    label: 'Kafka',          fmt: v => v ? 'active' : 'down',       ok: v => v },
  { key: 'scheduler',       label: 'Scheduler',      fmt: v => v ?? '—',                    ok: v => v === 'running' },
  { key: 'ws_subscribers',  label: 'WS Subscribers', fmt: v => `${v ?? 0} clients`,         ok: () => true },
  { key: 'offline_status',  label: 'Offline Mode',   fmt: v => v ?? '—',                    ok: v => v === 'ONLINE' || !v },
  { key: 'rules_loaded',    label: 'Routing Rules',  fmt: v => `${v ?? 0} rules loaded`,    ok: v => v > 0 },
]

export function SystemStatus() {
  const { system } = useDashboard()

  return (
    <Card>
      <CardHeader title="System Status" icon={Activity} />
      <CardBody>
        <div className={styles.rows}>
          {ROWS.map(row => {
            const raw = system[row.key]
            const hasVal = raw !== undefined
            const isOk = hasVal ? row.ok(raw) : null
            return (
              <div key={row.key} className={styles.row}>
                <span
                  className={`${styles.dot} ${
                    !hasVal ? styles.dotGrey
                    : isOk  ? styles.dotGreen
                    :          styles.dotRed
                  }`}
                />
                <span className={styles.label}>{row.label}</span>
                <span className={`${styles.value} ${hasVal && !isOk ? styles.bad : ''}`}>
                  {hasVal ? row.fmt(raw) : '—'}
                </span>
              </div>
            )
          })}
        </div>
      </CardBody>
    </Card>
  )
}
