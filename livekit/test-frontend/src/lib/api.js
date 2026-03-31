const BASE = ''

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(BASE + path, opts)
  const data = await res.json().catch(() => ({}))
  return { ok: res.ok, status: res.status, data }
}

export const api = {
  get:    (path)        => request('GET',    path),
  post:   (path, body)  => request('POST',   path, body),
  delete: (path)        => request('DELETE', path),

  health:           () => api.get('/health'),
  schedulingStats:  () => api.get('/scheduling/stats'),
  schedulingJobs:   (status, limit = 50, offset = 0) =>
    api.get(`/scheduling/jobs?${status ? `status=${status}&` : ''}limit=${limit}&offset=${offset}`),
  scheduleJob:      (body) => api.post('/scheduling/jobs', body),
  cancelJob:        (id)   => api.delete(`/scheduling/jobs/${id}`),

  routingRules:     () => api.get('/routing/rules'),
  reloadRules:      () => api.post('/routing/rules/reload'),
  routingDecision:  (body) => api.post('/routing/decision', body),
  agents:           () => api.get('/routing/agents'),
  registerAgent:    (body) => api.post('/routing/agents', body),
  deregisterAgent:  (id)   => api.delete(`/routing/agents/${id}`),

  wsHistory:        () => api.get('/ws/history'),
  wsStats:          () => api.get('/ws/stats'),
  wsPublish:        (body) => api.post('/ws/publish', body),
}
