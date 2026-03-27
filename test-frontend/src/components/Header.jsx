import { Phone, Radio, AlertCircle } from 'lucide-react'
import { useDashboard } from '../context/DashboardContext.jsx'
import styles from './Header.module.css'

const WS_CONFIG = {
  connected:    { label: 'Live',         className: styles.live,    DotClass: styles.dotGreen },
  connecting:   { label: 'Connecting…',  className: styles.warn,    DotClass: styles.dotAmber },
  disconnected: { label: 'Disconnected', className: styles.offline,  DotClass: styles.dotRed },
}

export function Header() {
  const { wsStatus, system } = useDashboard()
  const ws = WS_CONFIG[wsStatus] ?? WS_CONFIG.disconnected

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <div className={styles.logo}>
          <Phone size={18} />
        </div>
        <div>
          <div className={styles.title}>LiveKit Call Center</div>
          <div className={styles.subtitle}>Production Dashboard</div>
        </div>
      </div>

      <div className={styles.pills}>
        {system.kafka_active !== undefined && (
          <Pill
            ok={system.kafka_active}
            label={system.kafka_active ? 'Kafka Active' : 'Kafka Down'}
          />
        )}
        {system.rules_loaded !== undefined && (
          <span className={`${styles.pill} ${styles.info}`}>
            {system.rules_loaded} routing rules
          </span>
        )}
      </div>

      <div className={`${styles.wsBadge} ${ws.className}`}>
        <span className={`${styles.dot} ${ws.DotClass}`} />
        {ws.label}
      </div>
    </header>
  )
}

function Pill({ ok, label }) {
  return (
    <span className={`${styles.pill} ${ok ? styles.pillGreen : styles.pillRed}`}>
      {label}
    </span>
  )
}
