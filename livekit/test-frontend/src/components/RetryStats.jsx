import { RefreshCw } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Card, CardHeader, CardBody } from './ui/Card.jsx'
import { MetricBox } from './ui/MetricBox.jsx'
import styles from './RetryStats.module.css'

export function RetryStats() {
  const { retries, dlq, schedulingStats } = useDashboard()

  return (
    <Card>
      <CardHeader title="Retry / DLQ" icon={RefreshCw} />
      <CardBody>
        <div className={styles.grid}>
          <MetricBox value={schedulingStats.pending ?? 0}   label="Pending Jobs"   color="amber" />
          <MetricBox value={schedulingStats.completed ?? 0} label="Completed"      color="emerald" />
          <MetricBox value={schedulingStats.failed ?? 0}    label="Failed Jobs"    color="rose" />
          <MetricBox value={retries}                        label="Retried"        color="violet" />
          <MetricBox value={dlq}                            label="DLQ"            color="rose" />
          <MetricBox value={schedulingStats.running ?? 0}   label="Running Now"    color="cyan" />
        </div>
      </CardBody>
    </Card>
  )
}
