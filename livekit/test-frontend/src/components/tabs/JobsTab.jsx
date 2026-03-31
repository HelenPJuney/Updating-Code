import { useState, useEffect } from 'react'
import { RefreshCw } from 'lucide-react'
import { api } from '../../lib/api.js'
import { Button } from '../ui/Button.jsx'
import styles from './Tabs.module.css'

const STATUS_CHIP = {
  pending:   'chip chip-amber',
  running:   'chip chip-cyan',
  completed: 'chip chip-emerald',
  failed:    'chip chip-rose',
  cancelled: 'chip chip-muted',
}

export function JobsTab() {
  const [jobs, setJobs] = useState([])
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    const res = await api.schedulingJobs(statusFilter)
    setLoading(false)
    if (res.ok) setJobs(res.data.jobs || [])
  }

  useEffect(() => { load() }, [statusFilter])

  const cancel = async (id) => {
    await api.cancelJob(id)
    load()
  }

  return (
    <div className={styles.panel}>
      <div className={styles.tableToolbar}>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={{ width: 160 }}
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <Button variant="ghost" size="sm" onClick={load} loading={loading}>
          <RefreshCw size={11} /> Refresh
        </Button>
      </div>
      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Phone</th>
              <th>Scheduled</th>
              <th>Status</th>
              <th>Retries</th>
              <th>Label</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr><td colSpan="7" className="empty-state">No jobs found</td></tr>
            ) : jobs.map(j => (
              <tr key={j.job_id}>
                <td className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>
                  {j.job_id?.slice(0, 14)}…
                </td>
                <td>{j.phone_number}</td>
                <td style={{ fontSize: 12, color: 'var(--muted)' }}>
                  {new Date(j.scheduled_at * 1000).toLocaleString()}
                </td>
                <td><span className={STATUS_CHIP[j.status] ?? 'chip chip-muted'}>{j.status}</span></td>
                <td className="mono">{j.retry_count}/{j.max_retries}</td>
                <td style={{ color: 'var(--muted)', fontSize: 12 }}>{j.label || '—'}</td>
                <td>
                  {j.status === 'pending' && (
                    <Button variant="danger" size="sm" onClick={() => cancel(j.job_id)}>
                      Cancel
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
