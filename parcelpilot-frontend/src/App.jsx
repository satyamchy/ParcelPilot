import { useState, useRef, useEffect } from 'react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const ACCOUNTS = [
  { id: 'ACCT-001', name: 'Northstar Logistics' },
  { id: 'ACCT-002', name: 'LumenWorks' },
  
]

function newThreadId() {
  return 'thread-' + Math.random().toString(36).slice(2, 10)
}

export default function App() {
  const [role, setRole] = useState('customer')
  const [accountId, setAccountId] = useState('ACCT-001')
  const [threadId, setThreadId] = useState(newThreadId())
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [pendingAction, setPendingAction] = useState(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  function resetConversation() {
    setThreadId(newThreadId())
    setMessages([])
    setPendingAction(null)
  }

  async function sendMessage(text) {
    if (!text.trim() || loading) return
    const userCtx = { user_id: 'demo-user', role, account_id: role === 'customer' ? accountId : null }

    setMessages((m) => [...m, { role: 'user', content: text }])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, thread_id: threadId, user: userCtx }),
      })
      const data = await res.json()
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content: data.reply,
          toolTrace: data.tool_trace || [],
          citedSources: data.cited_sources || [],
          confidence: data.confidence,
        },
      ])
      setPendingAction(data.pending_action || null)
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `Connection error: ${err.message}`, error: true }])
    } finally {
      setLoading(false)
    }
  }

  async function confirmAction(confirmed) {
    setLoading(true)
    try {
      const res = await fetch(`${API_URL}/chat/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, confirmed }),
      })
      const data = await res.json()
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: data.reply, toolTrace: data.tool_trace || [] },
      ])
      setPendingAction(null)
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `Connection error: ${err.message}`, error: true }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">PP</span>
          <div>
            <div className="brand-title">ParcelPilot Support</div>
            <div className="brand-sub">Agent console — thread {threadId}</div>
          </div>
        </div>

        <div className="controls">
          <label>
            Role
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="customer">Customer</option>
              <option value="internal_support">Internal Support</option>
              <option value="internal_admin">Internal Admin</option>
            </select>
          </label>

          {role === 'customer' && (
            <label>
              Account
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {ACCOUNTS.map((a) => (
                  <option key={a.id} value={a.id}>{a.name} ({a.id})</option>
                ))}
              </select>
            </label>
          )}

          <button className="reset-btn" onClick={resetConversation}>New conversation</button>
        </div>
      </header>

      <main className="chat">
        {messages.length === 0 && (
          <div className="empty-state">
            <p>Ask about an order, a cancellation, a service credit, or a policy.</p>
            <p className="empty-hint">e.g. "Can Northstar cancel ORD-1001 without a cancellation fee?"</p>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg-row ${m.role}`}>
            <div className={`bubble ${m.role} ${m.error ? 'error' : ''}`}>
              {m.content}
            </div>

            {m.role === 'assistant' && m.toolTrace && m.toolTrace.length > 0 && (
              <div className="trace-strip">
                {m.toolTrace.map((t, j) => (
                  <div key={j} className="trace-chip" title={`${t.input_summary} → ${t.output_summary}`}>
                    <span className="trace-tool">{t.tool}</span>
                    <span className="trace-out">{t.output_summary}</span>
                  </div>
                ))}
              </div>
            )}

            {m.role === 'assistant' && m.citedSources && m.citedSources.length > 0 && (
              <details className="sources">
                <summary>Sources ({m.citedSources.length})</summary>
                <ul>
                  {m.citedSources.map((s, j) => <li key={j}>{s}</li>)}
                </ul>
              </details>
            )}
          </div>
        ))}

        {loading && (
          <div className="msg-row assistant">
            <div className="bubble assistant loading">
              <span className="dot" /><span className="dot" /><span className="dot" />
            </div>
          </div>
        )}

        <div ref={scrollRef} />
      </main>

      {pendingAction && (
        <div className="confirm-card">
          <div className="confirm-label">Action awaiting confirmation</div>
          <div className="confirm-body">
            <div><strong>{pendingAction.action_type}</strong> · priority: {pendingAction.priority}</div>
            {pendingAction.order_id && <div>Order: {pendingAction.order_id}</div>}
            {pendingAction.ticket_id && <div>Ticket: {pendingAction.ticket_id}</div>}
            <div className="confirm-reason">{pendingAction.reason}</div>
          </div>
          <div className="confirm-actions">
            <button className="confirm-btn cancel" onClick={() => confirmAction(false)} disabled={loading}>Cancel</button>
            <button className="confirm-btn confirm" onClick={() => confirmAction(true)} disabled={loading}>Confirm</button>
          </div>
        </div>
      )}

      <form
        className="composer"
        onSubmit={(e) => { e.preventDefault(); sendMessage(input) }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message…"
          disabled={loading || !!pendingAction}
        />
        <button type="submit" disabled={loading || !!pendingAction || !input.trim()}>Send</button>
      </form>
    </div>
  )
}
