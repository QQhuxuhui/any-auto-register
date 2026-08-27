import { useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'

import { API_BASE, apiFetch } from '@/lib/utils'
import { getTaskStatusText, isTerminalTaskStatus } from '@/lib/tasks'

type AccountSlot = {
  index: number
  email?: string
  proxy?: string
  stage?: string
  stage_label?: string
  percent?: number
  status?: string
  error?: string
}

type LogLine = { id: number; line: string; level?: string; accountIndex: number | null }

const STATUS_STYLE: Record<string, string> = {
  running: 'border-sky-400/40 bg-sky-400/10 text-sky-200',
  success: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200',
  failed: 'border-red-400/40 bg-red-400/10 text-red-200',
}

const STATUS_TEXT: Record<string, string> = {
  running: '进行中',
  success: '成功 · 已入池',
  failed: '失败',
}

function StatusBadge({ status }: { status?: string }) {
  const key = status || 'running'
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${STATUS_STYLE[key] || STATUS_STYLE.running}`}>
      {STATUS_TEXT[key] || key}
    </span>
  )
}

export function RegisterProgressPanel({
  taskId,
  onDone,
}: {
  taskId: string
  onDone: (status: string) => void
}) {
  const [task, setTask] = useState<any | null>(null)
  const [accounts, setAccounts] = useState<AccountSlot[]>([])
  const [logs, setLogs] = useState<LogLine[]>([])
  const [doneStatus, setDoneStatus] = useState<string | null>(null)
  const [openLog, setOpenLog] = useState<number | 'task' | null>(null)

  const seenRef = useRef<Set<number>>(new Set())
  const cursorRef = useRef(0)
  const doneRef = useRef(false)
  const onDoneRef = useRef(onDone)
  const sseHealthyRef = useRef(false)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => { onDoneRef.current = onDone }, [onDone])

  useEffect(() => {
    if (!taskId) return
    seenRef.current = new Set()
    cursorRef.current = 0
    doneRef.current = false
    sseHealthyRef.current = false
    setTask(null); setAccounts([]); setLogs([]); setDoneStatus(null); setOpenLog(null)

    const pushEvent = (payload: any) => {
      const eventId = Number(payload?.id || 0)
      if (eventId && seenRef.current.has(eventId)) return
      if (eventId) {
        seenRef.current.add(eventId)
        cursorRef.current = Math.max(cursorRef.current, eventId)
      }
      if (payload?.line) {
        const ai = payload?.detail?.account_index
        setLogs(prev => [...prev, {
          id: eventId,
          line: payload.line,
          level: payload.level,
          accountIndex: typeof ai === 'number' ? ai : null,
        }])
      }
      if (payload?.done && !doneRef.current) {
        doneRef.current = true
        sseHealthyRef.current = false
        esRef.current?.close(); esRef.current = null
        const s = payload.status || 'succeeded'
        setDoneStatus(s)
        onDoneRef.current(s)
      }
    }

    const syncTask = async () => {
      const latest = await apiFetch(`/tasks/${taskId}`)
      setTask(latest)
      if (Array.isArray(latest.accounts)) setAccounts(latest.accounts)
      if (isTerminalTaskStatus(latest.status) && !doneRef.current) {
        pushEvent({ done: true, status: latest.status })
      }
    }

    const es = new EventSource(`${API_BASE}/tasks/${taskId}/logs/stream`)
    esRef.current = es
    es.onopen = () => { sseHealthyRef.current = true }
    es.onmessage = (e) => { sseHealthyRef.current = true; pushEvent(JSON.parse(e.data)) }
    es.onerror = () => {
      if (doneRef.current) { es.close(); if (esRef.current === es) esRef.current = null; return }
      sseHealthyRef.current = false
    }

    syncTask().catch(() => {})

    const poll = window.setInterval(async () => {
      // 账号状态始终轮询（result.accounts 是权威来源，SSE 只带日志）
      try {
        if (!doneRef.current && !sseHealthyRef.current) {
          const data = await apiFetch(`/tasks/${taskId}/events?since=${cursorRef.current}`)
          for (const item of data.items || []) pushEvent(item)
        }
        await syncTask()
      } catch { /* passive */ }
    }, 1000)

    return () => {
      sseHealthyRef.current = false
      esRef.current?.close(); esRef.current = null
      window.clearInterval(poll)
    }
  }, [taskId])

  const currentStatus = doneStatus || task?.status || 'running'
  const total = accounts.length || Number(task?.progress_detail?.total || 0)
  const successCount = accounts.filter(a => a.status === 'success').length
  const failedCount = accounts.filter(a => a.status === 'failed').length
  const runningCount = Math.max(0, accounts.length - successCount - failedCount)

  const logsFor = useMemo(() => {
    const map = new Map<number | 'task', LogLine[]>()
    for (const l of logs) {
      const key = l.accountIndex === null ? 'task' : l.accountIndex
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(l)
    }
    return map
  }, [logs])

  const openLogLines = openLog === null ? [] : (logsFor.get(openLog) || [])
  const openLogTitle = openLog === 'task' ? '任务日志' :
    openLog !== null ? `账号 #${(openLog as number) + 1} 日志${accounts[openLog as number]?.email ? ` · ${accounts[openLog as number]?.email}` : ''}` : ''

  return (
    <div className="flex h-full flex-col gap-4">
      {/* 概览 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ['总数', total, 'text-[var(--text-primary)]'],
          ['成功', successCount, 'text-emerald-300'],
          ['失败', failedCount, 'text-red-300'],
          ['进行中', runningCount, 'text-sky-300'],
        ].map(([label, val, tone]) => (
          <div key={label as string} className="rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.16em] text-[var(--text-muted)]">{label}</div>
            <div className={`mt-1 text-lg font-semibold ${tone}`}>{val as number}</div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <div className="text-sm text-[var(--text-secondary)]">
          任务状态：<span className="font-medium text-[var(--text-primary)]">{getTaskStatusText(currentStatus)}</span>
        </div>
        <button
          type="button"
          onClick={() => setOpenLog('task')}
          className="rounded-full border border-[var(--border)] bg-[var(--bg-hover)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          任务日志
        </button>
      </div>

      {/* 账号列表 */}
      <div className="flex-1 overflow-y-auto rounded-xl border border-[var(--border)]">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-[var(--bg-pane)] text-[11px] uppercase tracking-[0.14em] text-[var(--text-muted)]">
            <tr>
              <th className="px-3 py-2 text-left font-medium">#</th>
              <th className="px-3 py-2 text-left font-medium">邮箱</th>
              <th className="px-3 py-2 text-left font-medium">IP</th>
              <th className="px-3 py-2 text-left font-medium">阶段</th>
              <th className="px-3 py-2 text-left font-medium">状态</th>
              <th className="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-10 text-center text-[var(--text-muted)]">
                  正在准备账号…
                </td>
              </tr>
            )}
            {accounts.map((a) => {
              const pct = Math.max(0, Math.min(100, Number(a.percent || 0)))
              const barColor = a.status === 'failed' ? 'bg-red-400' : a.status === 'success' ? 'bg-emerald-400' : 'bg-sky-400'
              return (
                <tr key={a.index} className="border-t border-[var(--border)] hover:bg-[var(--bg-hover)]/50">
                  <td className="px-3 py-2 text-[var(--text-muted)]">{a.index + 1}</td>
                  <td className="px-3 py-2 font-mono text-xs text-[var(--text-primary)]">{a.email || '—'}</td>
                  <td className="px-3 py-2 font-mono text-[11px] text-[var(--text-secondary)]">{a.proxy || '直连'}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-[var(--bg-hover)] ring-1 ring-[var(--border)]">
                        <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-xs text-[var(--text-secondary)]">{a.stage_label || '排队中'}</span>
                    </div>
                    {a.status === 'failed' && a.error ? (
                      <div className="mt-1 max-w-[280px] truncate text-[11px] text-red-300/80" title={a.error}>{a.error}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2"><StatusBadge status={a.status} /></td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => setOpenLog(a.index)}
                      className="rounded-full border border-[var(--border)] bg-[var(--bg-hover)] px-3 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    >
                      日志
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* 单账号日志抽屉 */}
      {openLog !== null && (
        <div className="dialog-backdrop" onClick={() => setOpenLog(null)}>
          <div
            className="dialog-panel flex w-[min(760px,calc(100vw-32px))] flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}
            style={{ maxHeight: '80vh' }}
          >
            <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3">
              <h3 className="truncate text-sm font-semibold text-[var(--text-primary)]">{openLogTitle}</h3>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => navigator.clipboard?.writeText(openLogLines.map(l => l.line).join('\n')).catch(() => {})}
                  className="rounded-full border border-[var(--border)] bg-[var(--bg-hover)] px-3 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  复制
                </button>
                <button onClick={() => setOpenLog(null)} className="rounded-full border border-[var(--border)] bg-[var(--bg-hover)] p-1.5 text-[var(--text-muted)] hover:text-[var(--text-primary)]">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto bg-[var(--bg-input)] p-4 font-mono text-xs">
              {openLogLines.length === 0 ? (
                <div className="flex h-full min-h-[120px] items-center justify-center text-[var(--text-muted)]">暂无日志</div>
              ) : (
                <div className="space-y-1.5">
                  {openLogLines.map((l, i) => (
                    <div
                      key={l.id || i}
                      className={`rounded-lg border border-white/5 bg-white/[0.025] px-3 py-1.5 leading-5 ${
                        l.line.includes('✓') || l.line.includes('成功') ? 'text-emerald-400' :
                        l.line.includes('✗') || l.line.includes('失败') || l.level === 'error' ? 'text-red-400' :
                        l.level === 'warning' ? 'text-amber-300' :
                        'text-[var(--text-secondary)]'
                      }`}
                    >
                      {l.line}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
