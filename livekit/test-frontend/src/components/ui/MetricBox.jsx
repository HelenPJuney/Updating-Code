import styles from './MetricBox.module.css'

export function MetricBox({ value, label, color = 'indigo', size = 'md' }) {
  return (
    <div className={`${styles.box} ${styles[color]}`}>
      <div className={`${styles.value} ${styles[size]}`}>{value ?? '—'}</div>
      <div className={styles.label}>{label}</div>
    </div>
  )
}
