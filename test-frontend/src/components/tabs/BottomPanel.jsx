import { useState } from 'react'
import { Calendar, List, GitBranch, Users, Play } from 'lucide-react'
import { Card } from '../ui/Card.jsx'
import { ScheduleTab } from './ScheduleTab.jsx'
import { JobsTab } from './JobsTab.jsx'
import { RoutingTab } from './RoutingTab.jsx'
import { AgentsTab } from './AgentsTab.jsx'
import { PipelineTab } from './PipelineTab.jsx'
import styles from './BottomPanel.module.css'

const TABS = [
  { id: 'schedule', label: 'Schedule Call',   Icon: Calendar   },
  { id: 'jobs',     label: 'Scheduled Jobs',  Icon: List       },
  { id: 'routing',  label: 'Routing Rules',   Icon: GitBranch  },
  { id: 'agents',   label: 'Agents',          Icon: Users      },
  { id: 'pipeline', label: 'Pipeline Test',   Icon: Play       },
]

export function BottomPanel() {
  const [active, setActive] = useState('schedule')
  const [jobsKey, setJobsKey] = useState(0)

  const Content = {
    schedule: <ScheduleTab onScheduled={() => setJobsKey(k => k+1)} />,
    jobs:     <JobsTab key={jobsKey} />,
    routing:  <RoutingTab />,
    agents:   <AgentsTab />,
    pipeline: <PipelineTab />,
  }[active]

  return (
    <Card>
      <div className={styles.tabs}>
        {TABS.map(t => (
          <button
            key={t.id}
            className={`${styles.tab} ${active === t.id ? styles.active : ''}`}
            onClick={() => setActive(t.id)}
          >
            <t.Icon size={13} />
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.content}>
        {Content}
      </div>
    </Card>
  )
}
