import { useEffect, useRef } from 'react'
import { ArrowRight, RotateCcw } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Card, CardHeader, CardBody } from './ui/Card.jsx'
import { Button } from './ui/Button.jsx'
import styles from './CallFlow.module.css'

const STEPS = [
  { id: 'sip',      label: 'SIP / PSTN',       activatedBy: ['call_started'] },
  { id: 'routing',  label: 'Routing Engine',    activatedBy: ['routing_decision'] },
  { id: 'kafka',    label: 'Kafka Queue',       activatedBy: ['queue_update'] },
  { id: 'ai',       label: 'AI Worker',         activatedBy: ['call_started'] },
  { id: 'escalate', label: 'Escalation?',       activatedBy: ['escalation'] },
  { id: 'human',    label: 'Human Agent',       activatedBy: ['escalation'] },
  { id: 'done',     label: 'Completed',         activatedBy: ['call_completed'] },
]

const FLOW_MAP = {
  call_started:     ['sip', 'routing', 'kafka', 'ai'],
  routing_decision: ['sip', 'routing'],
  queue_update:     ['kafka'],
  escalation:       ['ai', 'escalate', 'human'],
  call_completed:   ['done'],
  call_failed:      [],
}

export function CallFlow() {
  const { events, dispatch } = useDashboard()
  const lastEvent = events[0]
  const timerRef = useRef(null)

  const activeSteps = lastEvent ? (FLOW_MAP[lastEvent.type] ?? []) : []
  const isError = lastEvent?.type === 'call_failed' || lastEvent?.type === 'call_dlq'

  useEffect(() => {
    if (activeSteps.length > 0) {
      clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => dispatch({ type: 'RESET_FLOW' }), 6000)
    }
    return () => clearTimeout(timerRef.current)
  }, [lastEvent?.type])

  return (
    <Card glow>
      <CardHeader
        title="Live Call Flow"
        right={
          <Button variant="ghost" size="sm" onClick={() => dispatch({ type: 'RESET_FLOW' })}>
            <RotateCcw size={11} /> Reset
          </Button>
        }
      />
      <CardBody>
        <div className={styles.flow}>
          {STEPS.map((step, i) => {
            const active = activeSteps.includes(step.id)
            return (
              <div key={step.id} className={styles.stepWrap}>
                <div className={`${styles.step} ${active ? (isError ? styles.error : styles.active) : ''}`}>
                  <div className={styles.stepNum}>{i + 1}</div>
                  <div className={styles.stepLabel}>{step.label}</div>
                  {active && !isError && <div className={styles.pulse} />}
                </div>
                {i < STEPS.length - 1 && (
                  <ArrowRight
                    size={14}
                    className={`${styles.arrow} ${active ? styles.arrowActive : ''}`}
                  />
                )}
              </div>
            )
          })}
        </div>
        {lastEvent && (
          <div className={styles.statusBar}>
            <span className={`${styles.statusDot} ${isError ? styles.statusRed : styles.statusGreen}`} />
            <span className={styles.statusText}>
              {lastEvent.message ?? lastEvent.type?.replace(/_/g, ' ')}
            </span>
            {lastEvent.session_id && (
              <span className={styles.sessionId}>
                sess: {lastEvent.session_id?.slice(0, 12)}
              </span>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  )
}
