import { useState } from 'react'
import { Calendar, Clock } from 'lucide-react'
import { api } from '../../lib/api.js'
import { Button } from '../ui/Button.jsx'
import styles from './Tabs.module.css'

export function ScheduleTab({ onScheduled }) {
  const [form, setForm] = useState({
    phone_number: '+15551234567',
    scheduled_at: '',
    lang: 'en',
    label: '',
    priority: 0,
    max_retries: 3,
  })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const setNow = () => {
    const d = new Date(Date.now() + 5 * 60 * 1000)
    set('scheduled_at', d.toISOString().slice(0, 16))
  }

  const submit = async () => {
    if (!form.phone_number || !form.scheduled_at) {
      setResult({ ok: false, msg: 'Phone and time required.' })
      return
    }
    setLoading(true)
    const res = await api.scheduleJob({
      ...form,
      scheduled_at: form.scheduled_at,
      priority: Number(form.priority),
      max_retries: Number(form.max_retries),
    })
    setLoading(false)
    if (res.ok) {
      setResult({ ok: true, msg: `Scheduled — Job ID: ${res.data.job_id}` })
      onScheduled?.()
    } else {
      setResult({ ok: false, msg: JSON.stringify(res.data.detail ?? res.data) })
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.formGrid}>
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}><Calendar size={13} /> Call Details</h3>
          <div className={styles.field}>
            <label>Phone Number</label>
            <input value={form.phone_number} onChange={e => set('phone_number', e.target.value)} placeholder="+15551234567" />
          </div>
          <div className={styles.field}>
            <label>Language</label>
            <select value={form.lang} onChange={e => set('lang', e.target.value)}>
              <option value="en">English</option>
              <option value="es">Spanish</option>
              <option value="hi">Hindi</option>
            </select>
          </div>
          <div className={styles.field}>
            <label>Label (optional)</label>
            <input value={form.label} onChange={e => set('label', e.target.value)} placeholder="Follow-up call" />
          </div>
        </div>
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}><Clock size={13} /> Timing & Retry</h3>
          <div className={styles.field}>
            <label>Schedule Time</label>
            <input
              type="datetime-local"
              value={form.scheduled_at}
              onChange={e => set('scheduled_at', e.target.value)}
            />
          </div>
          <div className={styles.row2}>
            <div className={styles.field}>
              <label>Priority</label>
              <input type="number" min="0" max="10" value={form.priority} onChange={e => set('priority', e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>Max Retries</label>
              <input type="number" min="0" max="10" value={form.max_retries} onChange={e => set('max_retries', e.target.value)} />
            </div>
          </div>
        </div>
      </div>
      <div className={styles.actions}>
        <Button variant="primary" onClick={submit} loading={loading}>
          Schedule Call
        </Button>
        <Button variant="ghost" onClick={setNow}>
          Now + 5 min
        </Button>
        {result && (
          <div className={`${styles.result} ${result.ok ? styles.resultOk : styles.resultErr}`}>
            {result.msg}
          </div>
        )}
      </div>
    </div>
  )
}
