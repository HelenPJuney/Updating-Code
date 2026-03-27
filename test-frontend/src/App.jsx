import { DashboardProvider } from './context/DashboardContext.jsx'
import { Header } from './components/Header.jsx'
import { SystemStatus } from './components/SystemStatus.jsx'
import { CallFlow } from './components/CallFlow.jsx'
import { EventStream } from './components/EventStream.jsx'
import { QueueDepths } from './components/QueueDepths.jsx'
import { AgentList } from './components/AgentList.jsx'
import { RetryStats } from './components/RetryStats.jsx'
import { BottomPanel } from './components/tabs/BottomPanel.jsx'
import styles from './App.module.css'

function Dashboard() {
  return (
    <div className={styles.layout}>

      {/* LEFT SIDEBAR */}
      <aside className={styles.sidebar}>
        <SystemStatus />
        <QueueDepths />
        <RetryStats />
      </aside>

      {/* CENTER — call flow + event stream */}
      <main className={styles.center}>
        <CallFlow />
        <EventStream />
      </main>

      {/* RIGHT SIDEBAR */}
      <aside className={styles.sidebar}>
        <AgentList />
      </aside>

      {/* BOTTOM FULL WIDTH */}
      <div className={styles.bottom}>
        <BottomPanel />
      </div>

    </div>
  )
}

export default function App() {
  return (
    <DashboardProvider>
      <Header />
      <Dashboard />
    </DashboardProvider>
  )
}
