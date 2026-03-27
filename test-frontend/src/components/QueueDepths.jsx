import { Layers } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Card, CardHeader, CardBody } from './ui/Card.jsx'
import styles from './QueueDepths.module.css'

const QUEUES = [
  { key: 'default',  label: 'Default',  color: 'indigo' },
  { key: 'spanish',  label: 'Spanish',  color: 'violet' },
  { key: 'vip',      label: 'VIP',      color: 'amber'  },
  { key: 'outbound', label: 'Outbound', color: 'orange' },
  { key: 'hindi',    label: 'Hindi',    color: 'cyan'   },
]

export function QueueDepths() {
  const { queues } = useDashboard()
  const max = Math.max(10, ...Object.values(queues).map(Number))

  return (
    <Card>
      <CardHeader title="Queue Depths" icon={Layers} />
      <CardBody>
        {QUEUES.map(q => {
          const cnt = queues[q.key] || 0
          const pct = Math.round((cnt / max) * 100)
          return (
            <div key={q.key} className={styles.row}>
              <div className={styles.meta}>
                <span className={styles.name}>{q.label}</span>
                <span className={`${styles.count} ${styles[q.color]}`}>{cnt}</span>
              </div>
              <div className={styles.track}>
                <div
                  className={`${styles.fill} ${styles[q.color + 'Fill']}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )
        })}
      </CardBody>
    </Card>
  )
}
