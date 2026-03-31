import { createContext, useContext, useEffect, useReducer, useRef, useCallback } from 'react'
import { api } from '../lib/api.js'

const DashboardContext = createContext(null)

const initialState = {
  wsStatus: 'connecting',   // 'connecting' | 'connected' | 'disconnected'
  events: [],               // last 300 events
  system: {},               // health data
  queues: {},               // queue depths
  schedulingStats: {},      // by_status
  agents: [],               // agent list
  retries: 0,
  dlq: 0,
  flowStep: null,           // current call flow step
}

function reducer(state, action) {
  switch (action.type) {
    case 'WS_STATUS':
      return { ...state, wsStatus: action.payload }

    case 'EVENT': {
      const ev = action.payload
      const events = [ev, ...state.events].slice(0, 300)
      let retries = state.retries
      let dlq = state.dlq
      let queues = state.queues
      let system = state.system

      if (ev.type === 'call_dlq') dlq += 1
      if (ev.type === 'call_failed' && ev.retry_attempt > 0) retries += 1
      if (ev.type === 'queue_update' && ev.queues) queues = { ...ev.queues }
      if (ev.type === 'system_status') system = { ...state.system, ...ev }

      const flowMap = {
        call_started:     'ai',
        routing_decision: 'routing',
        queue_update:     'kafka',
        escalation:       'escalate',
        call_completed:   'done',
        call_failed:      'failed',
      }
      const flowStep = flowMap[ev.type] ?? state.flowStep

      return { ...state, events, retries, dlq, queues, system, flowStep }
    }

    case 'SYSTEM':
      return { ...state, system: { ...state.system, ...action.payload } }

    case 'SCHEDULING_STATS':
      return { ...state, schedulingStats: action.payload.by_status || {} }

    case 'AGENTS':
      return { ...state, agents: action.payload }

    case 'RESET_FLOW':
      return { ...state, flowStep: null }

    default:
      return state
  }
}

export function DashboardProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const wsRef = useRef(null)
  const retryRef = useRef(0)
  const retryTimerRef = useRef(null)

  const pushEvent = useCallback((ev) => {
    dispatch({ type: 'EVENT', payload: ev })
  }, [])

  const connect = useCallback(() => {
    if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
    dispatch({ type: 'WS_STATUS', payload: 'connecting' })

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws/events`

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        dispatch({ type: 'WS_STATUS', payload: 'connected' })
        retryRef.current = 0
        pushEvent({ type: 'system_status', message: 'WebSocket connected', ts: Date.now() / 1000 })
        ws.send(JSON.stringify({ action: 'subscribe' }))
      }

      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data)
          if (ev.type === '__ping__' || ev.type === 'pong') return
          pushEvent(ev)
        } catch {}
      }

      ws.onclose = () => {
        dispatch({ type: 'WS_STATUS', payload: 'disconnected' })
        const delay = Math.min(1000 * 2 ** retryRef.current, 30000)
        retryRef.current += 1
        retryTimerRef.current = setTimeout(connect, delay)
      }

      ws.onerror = () => ws.close()
    } catch {
      const delay = Math.min(1000 * 2 ** retryRef.current, 30000)
      retryRef.current += 1
      retryTimerRef.current = setTimeout(connect, delay)
    }
  }, [pushEvent])

  // Polling
  const poll = useCallback(async () => {
    const [healthRes, statsRes, agentsRes] = await Promise.allSettled([
      api.health(),
      api.schedulingStats(),
      api.agents(),
    ])
    if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
      dispatch({ type: 'SYSTEM', payload: healthRes.value.data })
    }
    if (statsRes.status === 'fulfilled' && statsRes.value.ok) {
      dispatch({ type: 'SCHEDULING_STATS', payload: statsRes.value.data })
    }
    if (agentsRes.status === 'fulfilled' && agentsRes.value.ok) {
      dispatch({ type: 'AGENTS', payload: agentsRes.value.data.agents || [] })
    }
  }, [])

  useEffect(() => {
    connect()
    poll()
    const timer = setInterval(poll, 30000)
    return () => {
      clearInterval(timer)
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect, poll])

  const value = {
    ...state,
    dispatch,
    refreshAgents: async () => {
      const res = await api.agents()
      if (res.ok) dispatch({ type: 'AGENTS', payload: res.data.agents || [] })
    },
    refreshStats: poll,
  }

  return (
    <DashboardContext.Provider value={value}>
      {children}
    </DashboardContext.Provider>
  )
}

export function useDashboard() {
  return useContext(DashboardContext)
}
