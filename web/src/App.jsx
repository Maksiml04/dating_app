import { useEffect, useRef, useState } from 'react'

// Векторные иконки (NFR-10: смысл не зависит от иконок — подписи текстом).
const HeartIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
)
const SendIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
)
const CheckIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
)
const ResetIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08a5.99 5.99 0 0 1-5.65 4c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
)
const SparkIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2zm7 12l.9 3.1L23 18l-3.1.9L19 22l-.9-3.1L15 18l3.1-.9L19 14z"/></svg>
)

// Кнопочные подписи приходят с эмодзи-маркерами из ядра; для web-UI
// оставляем только текст — иконки рисуем вектором (требование дизайна 2026).
const stripEmoji = (label) => label.replace(/^[^\p{L}\p{N}]+/u, '')

const actionIcon = (action) => {
  if (action.startsWith('like:')) return <HeartIcon />
  if (action === 'more') return <SparkIcon />
  return null
}

const CHAT_KEY = 'date-ideas-chat-id'

function newChatId() {
  let id = localStorage.getItem(CHAT_KEY)
  if (!id) {
    id = crypto.randomUUID().replace(/-/g, '').slice(0, 12)
    localStorage.setItem(CHAT_KEY, id)
  }
  return id
}

// Карточки приходят с HTML-разметкой из проверенного рендера ядра
// (все значения экранированы на бэкенде, см. app/core/rendering.py).
function RichText({ text }) {
  const html = text
    .replace(/&/g, '&amp;').replace(/&lt;/g, '&lt;').replace(/&gt;/g, '&gt;')
    .replace(/&lt;b&gt;/g, '<b>').replace(/&lt;\/b&gt;/g, '</b>')
    .replace(/&lt;i&gt;/g, '<i>').replace(/&lt;\/i&gt;/g, '</i>')
    .replace(/\n/g, '<br/>')
  return <span dangerouslySetInnerHTML={{ __html: html }} />
}

export default function App() {
  const [chatId] = useState(newChatId)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [typing, setTyping] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    fetch(`/api/chat/${chatId}/history`)
      .then(r => (r.ok ? r.json() : { messages: [] }))
      .then(data => setMessages(data.messages || []))
      .catch(() => {})
  }, [chatId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typing])

  async function post(path, body) {
    setTyping(true)
    try {
      const r = await fetch(`/api/chat/${chatId}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error('bad response')
      const data = await r.json()
      setMessages(prev => [...prev, ...(data.messages || [])])
    } catch {
      setMessages(prev => [...prev, {
        text: 'Агент временно недоступен. Попробуй ещё раз чуть позже.',
        buttons: [], delivered: false,
      }])
    } finally {
      setTyping(false)
    }
  }

  function send(text) {
    const value = (text ?? input).trim()
    if (!value || typing) return
    setMessages(prev => [...prev, { text: value, fromUser: true, buttons: [] }])
    setInput('')
    post('messages', { text: value })
  }

  function runAction(action) {
    setMessages(prev => prev.map(m => ({
      ...m,
      buttons: (m.buttons || []).map(row =>
        row.filter(b => b.action !== action)),
    })).filter(m => !m.fromUser))
    post('actions', { action })
  }

  async function reset() {
    await fetch(`/api/chat/${chatId}/reset`, { method: 'POST' }).catch(() => {})
    localStorage.removeItem(CHAT_KEY)
    window.location.reload()
  }

  const lastButtons = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      const rows = (m.buttons || []).filter(r => r.length)
      if (!m.fromUser && rows.length) return rows
    }
    return null
  })()

  return (
    <div className="app">
      <header className="header">
        <div className="brand-mark"><HeartIcon /></div>
        <div>
          <h1>Свидание-консъерж</h1>
          <div className="status">
            <span className="status-dot" /> онлайн · проверенный каталог Москвы
          </div>
        </div>
        <button className="reset" onClick={reset} aria-label="Начать заново" title="Начать заново">
          <ResetIcon />
        </button>
      </header>

      <main className="chat">
        {messages.length === 0 && !typing && (
          <div className="hello">
            <div className="hero-mark"><HeartIcon /></div>
            <h2>Куда сходить на свидание?</h2>
            <p>Опиши желание своими словами — я подберу варианты<br />по проверенному каталогу, с ценой и сеансами.</p>
            <div className="trust-badge"><CheckIcon /> только проверенные факты</div>
            <div className="examples">
              {['Гончарный мастер-класс на двоих', 'Выставка вечером в центре', 'Прогулка без бюджета'].map(x => (
                <button key={x} className="chip" onClick={() => send(x)}>
                  <HeartIcon /> {x}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`bubble-row ${m.fromUser ? 'user' : 'agent'}`}>
            {!m.fromUser && <div className="avatar"><HeartIcon /></div>}
            <div className={`bubble ${m.fromUser ? 'user' : 'agent'} ${m.delivered === false ? 'error' : ''}`}>
              {m.fromUser ? m.text : <RichText text={m.text} />}
            </div>
          </div>
        ))}

        {typing && (
          <div className="bubble-row agent">
            <div className="avatar"><HeartIcon /></div>
            <div className="bubble agent typing">
              <span></span><span></span><span></span>
            </div>
          </div>
        )}

        {lastButtons && !typing && (
          <div className="actions">
            {lastButtons.flat().map(b => (
              <button key={b.action} className="action" onClick={() => runAction(b.action)}>
                {actionIcon(b.action)}
                {stripEmoji(b.label)}
              </button>
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <footer className="composer">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
          placeholder="Куда бы сходить? Например: джаз вечером…"
          maxLength={500}
          aria-label="Сообщение агенту"
        />
        <button className="send" onClick={() => send()} disabled={!input.trim() || typing} aria-label="Отправить">
          <SendIcon />
        </button>
      </footer>
    </div>
  )
}
