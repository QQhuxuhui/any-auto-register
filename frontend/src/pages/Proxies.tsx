import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Plus, Trash2, RefreshCw, ToggleLeft, ToggleRight, Globe2, ShieldCheck, CircleOff, Activity, Radar } from 'lucide-react'

const TYPE_META: Record<string, { label: string; cls: string }> = {
  residential: { label: '住宅', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  datacenter: { label: '机房', cls: 'bg-orange-500/15 text-orange-400 border-orange-500/30' },
  mobile: { label: '移动', cls: 'bg-sky-500/15 text-sky-400 border-sky-500/30' },
  unknown: { label: '未知', cls: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30' },
  '': { label: '未探测', cls: 'bg-zinc-500/10 text-[var(--text-muted)] border-[var(--border-soft)]' },
}

export default function Proxies() {
  const [proxies, setProxies] = useState<any[]>([])
  const [newProxy, setNewProxy] = useState('')
  const [region, setRegion] = useState('')
  const [checking, setChecking] = useState(false)
  const [probing, setProbing] = useState(false)
  const [probeState, setProbeState] = useState<{ running: boolean; total: number; done: number }>({ running: false, total: 0, done: 0 })
  const [filter, setFilter] = useState<string>('all')

  const load = () => apiFetch('/proxies').then(setProxies)

  useEffect(() => { load() }, [])

  const add = async () => {
    if (!newProxy.trim()) return
    const lines = newProxy.trim().split('\n').map(l => l.trim()).filter(Boolean)
    if (lines.length > 1) {
      await apiFetch('/proxies/bulk', { method: 'POST', body: JSON.stringify({ proxies: lines, region }) })
    } else {
      await apiFetch('/proxies', { method: 'POST', body: JSON.stringify({ url: lines[0], region }) })
    }
    setNewProxy('')
    load()
  }

  const del = async (id: number) => {
    await apiFetch(`/proxies/${id}`, { method: 'DELETE' })
    load()
  }

  const toggle = async (id: number) => {
    await apiFetch(`/proxies/${id}/toggle`, { method: 'PATCH' })
    load()
  }

  const check = async () => {
    setChecking(true)
    await apiFetch('/proxies/check', { method: 'POST' })
    setTimeout(() => { load(); setChecking(false) }, 3000)
  }

  // 探测 IP 类型 + 可用性，轮询进度
  const probe = async () => {
    setProbing(true)
    await apiFetch('/proxies/probe', { method: 'POST' })
    const poll = setInterval(async () => {
      const st = await apiFetch('/proxies/probe/status')
      setProbeState(st)
      if (!st.running) {
        clearInterval(poll)
        setProbing(false)
        load()
      } else {
        load()
      }
    }, 2000)
  }

  const delByType = async (types: string[]) => {
    const names = types.map(t => TYPE_META[t]?.label || t).join(' / ')
    if (!confirm(`确认删除所有【${names}】类型的代理？`)) return
    const r = await apiFetch('/proxies/delete-by-type', { method: 'POST', body: JSON.stringify({ types }) })
    alert(`已删除 ${r.deleted} 条`)
    load()
  }

  const delDead = async () => {
    if (!confirm('确认删除所有探测失联(不可用)的代理？')) return
    const r = await apiFetch('/proxies/delete-by-type', { method: 'POST', body: JSON.stringify({ types: [], only_dead: true }) })
    alert(`已删除 ${r.deleted} 条`)
    load()
  }

  const activeCount = proxies.filter((item) => item.is_active).length
  const counts = proxies.reduce((acc: Record<string, number>, p) => {
    const t = p.ip_type || (p.probe_status === 'fail' ? 'dead' : 'unprobed')
    acc[t] = (acc[t] || 0) + 1
    return acc
  }, {})
  const dead = proxies.filter(p => p.probe_status === 'fail').length

  const metricCards = [
    { label: '代理数', value: proxies.length, icon: Globe2, tone: 'text-[var(--accent)]' },
    { label: '住宅', value: counts['residential'] || 0, icon: ShieldCheck, tone: 'text-emerald-400' },
    { label: '机房', value: counts['datacenter'] || 0, icon: Activity, tone: 'text-orange-400' },
    { label: '失联', value: dead, icon: CircleOff, tone: 'text-red-400' },
  ]

  const shown = filter === 'all' ? proxies
    : filter === 'dead' ? proxies.filter(p => p.probe_status === 'fail')
    : proxies.filter(p => (p.ip_type || 'unknown') === filter)

  const filterTabs: { key: string; label: string }[] = [
    { key: 'all', label: `全部 ${proxies.length}` },
    { key: 'residential', label: `住宅 ${counts['residential'] || 0}` },
    { key: 'datacenter', label: `机房 ${counts['datacenter'] || 0}` },
    { key: 'mobile', label: `移动 ${counts['mobile'] || 0}` },
    { key: 'unknown', label: `未知 ${counts['unknown'] || 0}` },
    { key: 'dead', label: `失联 ${dead}` },
  ]

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden p-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm font-semibold text-[var(--text-primary)]">代理</div>
            <Badge variant="default">总量 {proxies.length}</Badge>
            <Badge variant="secondary">活跃 {activeCount}</Badge>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={probe} disabled={probing}>
              <Radar className={`h-4 w-4 mr-1.5 ${probing ? 'animate-spin' : ''}`} />
              {probing ? `探测中 ${probeState.done}/${probeState.total}` : '探测类型/可用性'}
            </Button>
            <Button variant="outline" size="sm" onClick={check} disabled={checking}>
              <RefreshCw className={`h-4 w-4 mr-1.5 ${checking ? 'animate-spin' : ''}`} />
              检测全部
            </Button>
          </div>
        </div>
      </Card>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {metricCards.map(({ label, value, icon: Icon, tone }) => (
          <Card key={label} className="bg-transparent">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">{label}</div>
                <div className="mt-1.5 text-xl font-semibold tracking-[-0.03em] text-[var(--text-primary)]">{value}</div>
              </div>
              <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border-soft)] bg-[var(--chip-bg)]">
                <Icon className={`h-5 w-5 ${tone}`} />
              </div>
            </div>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,330px)_minmax(0,1fr)]">
        <Card className="bg-[var(--bg-pane)]/60">
          <div className="space-y-4">
            <div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">新增</div>
              <div className="mt-1 text-sm font-medium text-[var(--text-primary)]">添加代理或批量导入</div>
            </div>
            <textarea
              value={newProxy}
              onChange={e => setNewProxy(e.target.value)}
              placeholder="http://user:pass@host:port"
              rows={6}
              className="control-surface control-surface-mono resize-none"
            />
            <input
              value={region}
              onChange={e => setRegion(e.target.value)}
              placeholder="地区标签 (如 US, SG)"
              className="control-surface"
            />
            <Button onClick={add} className="w-full">
              <Plus className="h-4 w-4 mr-1.5" />
              添加到代理池
            </Button>

            <div className="rounded-lg border border-[var(--border-soft)] bg-[var(--bg-pane)]/45 px-3.5 py-3 space-y-2.5">
              <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted)]">按类型批量清理</div>
              <div className="text-xs leading-5 text-[var(--text-secondary)]">先点上方「探测类型/可用性」，再按需批量删除。</div>
              <div className="flex flex-wrap gap-2">
                <button onClick={() => delByType(['datacenter'])} className="table-action-btn table-action-btn-danger">
                  <Trash2 className="mr-1.5 h-4 w-4" />删机房 {counts['datacenter'] || 0}
                </button>
                <button onClick={() => delByType(['unknown'])} className="table-action-btn table-action-btn-danger">
                  <Trash2 className="mr-1.5 h-4 w-4" />删未知 {counts['unknown'] || 0}
                </button>
                <button onClick={delDead} className="table-action-btn table-action-btn-danger">
                  <Trash2 className="mr-1.5 h-4 w-4" />删失联 {dead}
                </button>
                <button onClick={() => delByType(['datacenter', 'unknown'])} className="table-action-btn table-action-btn-danger">
                  <Trash2 className="mr-1.5 h-4 w-4" />只留住宅+移动
                </button>
              </div>
            </div>
          </div>
        </Card>

        <Card className="overflow-hidden p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-3">
            <div className="text-sm font-medium text-[var(--text-primary)]">代理列表</div>
            <div className="ml-auto flex flex-wrap gap-1.5">
              {filterTabs.map(t => (
                <button key={t.key} onClick={() => setFilter(t.key)}
                  className={`rounded-md border px-2.5 py-1 text-xs transition ${filter === t.key ? 'border-[var(--accent)]/50 bg-[var(--accent)]/12 text-[var(--accent)]' : 'border-[var(--border-soft)] text-[var(--text-muted)] hover:text-[var(--text-secondary)]'}`}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        <div className="glass-table-wrap">
        <table className="w-full min-w-[860px] text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-[var(--text-muted)]">
              <th className="px-4 py-2.5 text-left">代理地址</th>
              <th className="px-4 py-2.5 text-left">类型</th>
              <th className="px-4 py-2.5 text-left">出口 / 运营商</th>
              <th className="px-4 py-2.5 text-left">延迟</th>
              <th className="px-4 py-2.5 text-left">成功/失败</th>
              <th className="px-4 py-2.5 text-left">状态</th>
              <th className="px-4 py-2.5 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8">
                  <div className="empty-state-panel">{proxies.length === 0 ? '当前代理池为空，可以先从左侧输入一个或批量导入。' : '该筛选下没有代理。'}</div>
                </td>
              </tr>
            )}
            {shown.map(p => {
              const meta = TYPE_META[p.ip_type || ''] || TYPE_META['']
              return (
              <tr key={p.id} className="border-b border-[var(--border)]/40 hover:bg-[var(--bg-hover)]/70">
                <td className="px-4 py-2.5 font-mono text-xs text-[var(--text-secondary)]">{p.url}</td>
                <td className="px-4 py-2.5">
                  <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs ${meta.cls}`}>{meta.label}</span>
                </td>
                <td className="px-4 py-2.5 text-xs">
                  {p.probe_status === 'ok'
                    ? <span className="text-[var(--text-secondary)]">{p.country ? `${p.country} · ` : ''}<span className="text-[var(--text-muted)]">{p.egress_ip}</span><div className="text-[var(--text-muted)]">{p.isp}</div></span>
                    : p.probe_status === 'fail'
                      ? <span className="text-red-400">失联</span>
                      : <span className="text-[var(--text-muted)]">-</span>}
                </td>
                <td className="px-4 py-2.5 text-xs text-[var(--text-muted)]">{p.latency_ms ? `${p.latency_ms}ms` : '-'}</td>
                <td className="px-4 py-2.5">
                  <span className="text-emerald-400">{p.success_count}</span>
                  <span className="text-[var(--text-muted)]"> / </span>
                  <span className="text-red-400">{p.fail_count}</span>
                </td>
                <td className="px-4 py-2.5">
                  <Badge variant={p.is_active ? 'success' : 'danger'}>{p.is_active ? '活跃' : '禁用'}</Badge>
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <button onClick={() => toggle(p.id)} className="table-action-btn">
                      {p.is_active ? <ToggleRight className="mr-1.5 h-4 w-4" /> : <ToggleLeft className="mr-1.5 h-4 w-4" />}
                      {p.is_active ? '停用' : '启用'}
                    </button>
                    <button onClick={() => del(p.id)} className="table-action-btn table-action-btn-danger">
                      <Trash2 className="mr-1.5 h-4 w-4" />删除
                    </button>
                  </div>
                </td>
              </tr>
            )})}
          </tbody>
        </table>
        </div>
        </Card>
      </div>
    </div>
  )
}
