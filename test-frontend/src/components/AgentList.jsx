import { Users } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Card, CardHeader, CardBody } from './ui/Card.jsx'
import styles from './AgentList.module.css'

export function AgentList() {
  const { agents } = useDashboard()
  const online = agents.filter(a => a.available).length

  return (
    <Card>
      <CardHeader
        title="Human Agents"
        icon={Users}
        right={
          <span className={`chip ${online > 0 ? 'chip-emerald' : 'chip-muted'}`}>
            {online} online
          </span>
        }
      />
      <CardBody>
        {agents.length === 0 ? (
          <div className="empty-state">
            <Users size={18} />
            No agents registered
          </div>
        ) : (
          <div className={styles.list}>
            {agents.map(a => (
              <div key={a.agent_id} className={styles.agent}>
                <div className={`${styles.dot} ${a.available ? styles.online : styles.offline}`} />
                <div className={styles.info}>
                  <div className={styles.name}>{a.name || a.agent_id}</div>
                  <div className={styles.skills}>
                    {(a.skills || []).slice(0, 3).map(s => (
                      <span key={s} className={`chip chip-indigo ${styles.skillChip}`}>{s}</span>
                    ))}
                  </div>
                </div>
                <div className={styles.slots}>
                  <span className={styles.slotsVal}>{a.active_calls || 0}</span>
                  <span className={styles.slotsSep}>/</span>
                  <span className={styles.slotsMax}>{a.max_calls || 2}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  )
}
