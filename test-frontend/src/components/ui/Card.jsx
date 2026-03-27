import styles from './Card.module.css'

export function Card({ children, className = '', glow = false, style }) {
  return (
    <div
      className={`${styles.card} ${glow ? styles.glow : ''} ${className}`}
      style={style}
    >
      {children}
    </div>
  )
}

export function CardHeader({ title, right, icon: Icon }) {
  return (
    <div className={styles.header}>
      <div className={styles.titleRow}>
        {Icon && <Icon size={14} className={styles.icon} />}
        <span className={styles.title}>{title}</span>
      </div>
      {right && <div className={styles.right}>{right}</div>}
    </div>
  )
}

export function CardBody({ children, padding = true, className = '' }) {
  return (
    <div className={`${styles.body} ${!padding ? styles.noPad : ''} ${className}`}>
      {children}
    </div>
  )
}
