/**
 * ch-progress-panel —— 浏览器半端：右侧栏页签「进度」。
 * client bundle 是自注册脚本；三件套注册 tab 类型 / body / title。
 * 清理：仅 done 票残留分支显示入口，POST 预检 → 弹窗确认 → confirm 指纹执行。
 */
window.__ModuleLoader__.load({
  id: '@ch-agent/dsh-progress-panel',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement

    const API = '/api/ch-progress'
    const asJson = async (res) => {
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status))
      return data
    }
    const jget = async (p) => asJson(await fetch(API + p, { credentials: 'same-origin' }))
    const jpost = async (p, body, apply = false) => asJson(await fetch(API + p, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'content-type': 'application/json', ...(apply ? { 'x-ch-progress-action': 'apply' } : {}) },
      body: JSON.stringify(body),
    }))

    const CSS = `
[data-cpp]{font-size:12.5px;line-height:1.45;color:var(--dsw-alias-label-primary,#e8eaed);--cpp-line:rgba(128,128,128,.18);--cpp-dim:var(--dsw-alias-label-tertiary,#9aa0a6);height:100%;min-height:0;min-width:0;display:flex;flex-direction:column;flex:1 1 auto;}
[data-cpp] *{box-sizing:border-box;}
[data-cpp] .cpp-body{flex:1 1 auto;min-height:0;height:auto;width:100%;overflow-y:auto;overscroll-behavior:contain;padding:4px 3px 8px;}
[data-cpp] .cpp-row{display:flex;align-items:center;gap:5px;padding:4px 6px 4px 2px;border-radius:8px;min-height:29px;border:1px solid transparent;}
[data-cpp] .cpp-row.is-main{margin:0 4px 7px;padding:7px 8px;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.1));border-color:var(--cpp-line);}
[data-cpp] .cpp-row.is-feat{background:var(--dsw-alias-bg-layer-3,rgba(128,128,128,.1));margin-top:6px;font-size:13px;}
[data-cpp] button.cpp-row{width:100%;text-align:left;border:1px solid transparent;background:transparent;color:inherit;font:inherit;font-size:12.5px;cursor:pointer;}
[data-cpp] button.cpp-row:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.14));border-color:var(--cpp-line);}
[data-cpp] .cpp-row.is-feat:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.18));}
[data-cpp] .cpp-g{width:12px;align-self:stretch;flex:none;position:relative;}
[data-cpp] .cpp-g.is-v::before,[data-cpp] .cpp-g.is-b::before{content:"";position:absolute;left:6px;top:0;bottom:0;width:1px;background:var(--cpp-line);}
[data-cpp] .cpp-g.is-b::after{content:"";position:absolute;left:6px;width:6px;top:50%;height:1px;background:var(--cpp-line);}
[data-cpp] .cpp-g.is-b.is-last::before{bottom:50%;}
[data-cpp] .cpp-chev{width:14px;height:14px;flex:none;color:var(--cpp-dim);transition:transform .15s ease;display:inline-flex;align-items:center;justify-content:center;font-size:15px;}
[data-cpp] .cpp-chev.is-open{transform:rotate(90deg);}
[data-cpp] .cpp-chev.is-hidden{visibility:hidden;}
[data-cpp] button.cpp-chev{background:transparent;border:0;padding:0;cursor:pointer;}
@media (prefers-reduced-motion:reduce){[data-cpp] .cpp-chev{transition:none;}}
[data-cpp] .cpp-fname{font-weight:550;font-size:12.5px;letter-spacing:-.01em;}
[data-cpp] .cpp-main-name{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;font-weight:500;}
[data-cpp] .cpp-agents{display:inline-flex;gap:3px;margin-left:2px;}
[data-cpp] .cpp-agent{font-size:10px;padding:1px 5px;border-radius:999px;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.14));color:var(--cpp-dim);}
[data-cpp] .cpp-fright{margin-left:auto;display:inline-flex;gap:6px;align-items:center;flex:none;}
[data-cpp] .cpp-bar{width:54px;height:5px;border-radius:99px;overflow:hidden;display:inline-flex;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.15));}
[data-cpp] .cpp-bar i{display:block;height:100%;}
[data-cpp] .cpp-bar .s-closed{background:#30a46c;}
[data-cpp] .cpp-bar .s-inflight{background:#4f8ff7;}
[data-cpp] .cpp-bar .s-ready{background:#8a8f98;opacity:.4;}
[data-cpp] .cpp-fcount{font-size:10.5px;color:var(--cpp-dim);font-variant-numeric:tabular-nums;}
[data-cpp] .cpp-dot{width:7px;height:7px;border-radius:50%;flex:none;}
[data-cpp] .cpp-dot.run{background:#3dd68c;box-shadow:0 0 0 3px rgba(61,214,140,.09);animation:cpp-pulse 2.4s ease-out infinite;}
[data-cpp] .cpp-dot.stall{background:#f0a83c;}
[data-cpp] .cpp-dot.mr{background:#5c9dff;}
[data-cpp] .cpp-dot.review{background:#b18cff;}
[data-cpp] .cpp-dot.human{background:#f2585e;}
@keyframes cpp-pulse{0%{box-shadow:0 0 0 0 rgba(61,214,140,.28);}70%{box-shadow:0 0 0 5px transparent;}100%{box-shadow:0 0 0 0 transparent;}}
@media (prefers-reduced-motion:reduce){[data-cpp] .cpp-dot.run{animation:none;}}
[data-cpp] .cpp-rid{font-weight:550;flex:none;font-size:11.5px;font-variant-numeric:tabular-nums;}
[data-cpp] .cpp-rtitle{color:var(--cpp-dim);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0;}
[data-cpp] .cpp-rmeta{color:var(--cpp-dim);font-size:10.5px;white-space:nowrap;flex:none;font-variant-numeric:tabular-nums;}
[data-cpp] .cpp-rmeta.is-warn{color:#f0a83c;}
[data-cpp] .cpp-status{font-size:10px;padding:1px 6px;border-radius:999px;flex:none;background:rgba(138,143,152,.12);color:var(--cpp-dim);}
[data-cpp] .cpp-status.human{background:rgba(242,88,94,.12);color:#f2585e;}
[data-cpp] .cpp-status.review{background:rgba(177,140,255,.12);color:#b18cff;}
[data-cpp] .cpp-status.mr{background:rgba(92,157,255,.12);color:#5c9dff;}
[data-cpp] .cpp-bname{font-size:11.5px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
[data-cpp] .cpp-bname b{font-weight:550;}
[data-cpp] .cpp-bhint{font-size:10px;padding:1px 5px;border-radius:999px;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.14));color:var(--cpp-dim);flex:none;}
[data-cpp] .cpp-cleanup{flex:none;border:1px solid var(--cpp-line);background:transparent;color:var(--cpp-dim);font:inherit;font-size:10.5px;border-radius:6px;padding:1px 6px;cursor:pointer;opacity:1;pointer-events:auto;transition:color .12s,border-color .12s;}
[data-cpp] .cpp-cleanup:hover{color:#f0a83c;border-color:rgba(240,168,60,.45);}
[data-cpp] .cpp-iconbtn{border:0;background:transparent;color:var(--cpp-dim);font:inherit;font-size:14px;line-height:1;border-radius:5px;padding:2px 4px;cursor:pointer;}
[data-cpp] .cpp-iconbtn:hover{color:var(--dsw-alias-label-primary,#e8eaed);background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.18));}
[data-cpp] .cpp-detail{margin:2px 4px 7px 0;padding:9px 11px;border-radius:10px;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.12));border:1px solid var(--cpp-line);font-size:11.5px;}
[data-cpp] .cpp-dtitle{font-weight:550;margin-bottom:4px;}
[data-cpp] .cpp-dline{color:var(--cpp-dim);margin-top:3px;overflow-wrap:anywhere;}
[data-cpp] .cpp-drill{display:inline-flex;align-items:center;gap:6px;margin-top:9px;border:1px solid var(--cpp-line);background:transparent;color:inherit;font:inherit;font-size:11px;border-radius:7px;padding:4px 9px;cursor:pointer;}
[data-cpp] .cpp-drill:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.14));}
[data-cpp] .cpp-err{margin:10px;padding:10px;border-radius:10px;background:rgba(242,88,94,.1);color:#f2585e;font-size:11.5px;white-space:pre-wrap;}
[data-cpp] .cpp-loading{padding:18px 12px;color:var(--cpp-dim);font-size:11.5px;}
[data-cpp] .cpp-modal-backdrop{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.52);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:24px;}
[data-cpp] .cpp-modal{width:min(480px,100%);max-height:82vh;overflow-y:auto;border-radius:16px;background:var(--dsw-alias-bg-layer-1,#1b1d21);border:1px solid var(--cpp-line);box-shadow:0 28px 80px rgba(0,0,0,.5);padding:18px;color:var(--dsw-alias-label-primary,#e8eaed);}
[data-cpp] .cpp-modal h2{font-size:16px;font-weight:550;margin:0 0 5px;}
[data-cpp] .cpp-modal-sub{font-size:11.5px;color:var(--cpp-dim);margin-bottom:14px;overflow-wrap:anywhere;}
[data-cpp] .cpp-branch{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;padding:8px 10px;border-radius:8px;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.12));overflow-wrap:anywhere;margin-bottom:12px;}
[data-cpp] .cpp-plan{display:flex;flex-direction:column;gap:7px;margin:10px 0 12px;}
[data-cpp] .cpp-plan-item{display:flex;gap:8px;align-items:flex-start;font-size:12px;}
[data-cpp] .cpp-plan-item::before{content:"";width:5px;height:5px;border-radius:50%;background:#5c9dff;flex:none;margin-top:7px;}
[data-cpp] .cpp-warning{padding:8px 10px;border-radius:8px;background:rgba(240,168,60,.09);color:#f0a83c;font-size:11px;margin:6px 0;overflow-wrap:anywhere;}
[data-cpp] .cpp-facts{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:11px;margin:10px 0 12px;}
[data-cpp] .cpp-facts dt{color:var(--cpp-dim);}
[data-cpp] .cpp-facts dd{margin:0;overflow-wrap:anywhere;}
[data-cpp] .cpp-safe{font-size:10.5px;color:var(--cpp-dim);padding:8px 0;border-top:1px solid var(--cpp-line);}
[data-cpp] .cpp-modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px;}
[data-cpp] .cpp-btn{border:1px solid var(--cpp-line);background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.12));color:inherit;font:inherit;font-size:12px;border-radius:8px;padding:6px 13px;cursor:pointer;}
[data-cpp] .cpp-btn:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.2));}
[data-cpp] .cpp-btn.danger{background:#c73740;border-color:#c73740;color:#fff;}
[data-cpp] .cpp-btn.danger:hover{background:#df4b54;}
[data-cpp] .cpp-btn:disabled{opacity:.45;cursor:default;}
[data-cpp] .cpp-result-ok{color:#3dd68c;font-size:13px;margin:8px 0;}
[data-cpp] .cpp-result-warn{color:#f0a83c;font-size:13px;margin:8px 0;}
[data-cpp] .cpp-path{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;color:var(--cpp-dim);overflow-wrap:anywhere;margin-top:8px;}
`

    const LABEL = { 'in-progress': '在跑', 'in-review': '评审中', 'mr-submitted': '待合并', 'needs-human': '等人' }
    const DOT = { 'in-progress': 'run', 'in-review': 'review', 'mr-submitted': 'mr', 'needs-human': 'human' }

    function fmtRel(ms) {
      const m = ms / 60000
      if (m < 1) return '刚刚'
      if (m < 60) return Math.round(m) + ' 分钟'
      if (m < 1440) { const hh = m / 60; return (hh >= 10 ? Math.round(hh) : Math.round(hh * 10) / 10) + ' 小时' }
      return Math.round(m / 1440) + ' 天'
    }
    const ageText = (ts, suffix) => (ts ? fmtRel(Date.now() - ts * 1000) + (suffix || '前') : '')
    const fmtBytes = (n) => {
      if (!n) return '0 B'
      if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + ' KB'
      if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + ' MB'
      return (n / 1024 ** 3).toFixed(2) + ' GB'
    }
    const nodeKey = (n) => n.kind + ':' + (n.slug || n.key || n.name)
    const depthOf = (n) => Math.round(n.prefix.length / 3)
    const hasKids = (list, i) => i + 1 < list.length && depthOf(list[i + 1]) > depthOf(list[i])
    const canCleanup = (n) => n.kind === 'branch' && n.status === 'done'

    function Guide({ prefix }) {
      const segs = []
      for (let i = 0; i < prefix.length; i += 3) {
        const seg = prefix.substr(i, 3)
        const cls = seg === '│  ' ? 'is-v' : seg === '├─ ' ? 'is-b' : seg === '└─ ' ? 'is-b is-last' : ''
        segs.push(h('span', { key: i, className: 'cpp-g ' + cls }))
      }
      return h('span', { style: { display: 'contents' } }, segs)
    }
    const Chev = ({ open, hidden }) =>
      h('span', { className: 'cpp-chev' + (open ? ' is-open' : '') + (hidden ? ' is-hidden' : ''), 'aria-hidden': true }, '›')

    function FeatureRow({ n, kids, folded, onFold }) {
      const c = n.counts
      const inflight = (c.run || 0) + (c.review || 0) + (c.mr || 0) + (c.human || 0)
      const seg = (cls, v) => (v ? h('i', { key: cls, className: cls, style: { width: (100 * v / c.total) + '%' } }) : null)
      return h('div', { className: 'cpp-row is-feat' },
        h(Guide, { prefix: n.prefix }),
        kids
          ? h('button', { className: 'cpp-chev', 'aria-label': (folded ? '展开' : '折叠') + n.slug, onClick: onFold }, Chev({ open: !folded }))
          : h('span', { className: 'cpp-chev is-hidden' }, '›'),
        h('span', { className: 'cpp-fname' }, n.slug),
        n.agents && n.agents.length
          ? h('span', { className: 'cpp-agents' }, n.agents.map((a) => h('span', { key: a, className: 'cpp-agent' }, a)))
          : null,
        h('span', { className: 'cpp-fright' },
          h('span', { className: 'cpp-bar', title: `已完结 ${c.done} · 在途 ${inflight} · 待派发 ${c.ready || 0}，共 ${c.total} 票` },
            seg('s-closed', c.done), seg('s-inflight', inflight), seg('s-ready', c.ready)),
          h('span', { className: 'cpp-fcount' }, c.done + '/' + c.total)))
    }

    function TicketRow({ n, open, onToggle }) {
      const stalled = n.status === 'in-progress' && n.age_ts && (Date.now() - n.age_ts * 1000) >= (n.stallMin || 30) * 60000
      const bits = []
      if (n.ahead || n.behind) bits.push('↑' + n.ahead + '↓' + n.behind)
      if (n.dirty) bits.push('脏' + n.dirty)
      if (n.status === 'in-progress' && n.age_ts) bits.push(stalled ? '⚠ ' + ageText(n.age_ts, '无动静') : ageText(n.age_ts))
      return h('div', {},
        h('button', { className: 'cpp-row', 'aria-expanded': !!open, onClick: onToggle },
          h(Guide, { prefix: n.prefix }),
          h('span', { className: 'cpp-chev' + (open ? ' is-open' : ''), 'aria-hidden': true }, '›'),
          h('span', { className: 'cpp-dot ' + (stalled ? 'stall' : (DOT[n.status] || 'run')) }),
          h('span', { className: 'cpp-rid' }, n.id),
          h('span', { className: 'cpp-rtitle', title: n.title }, n.title),
          bits.length ? h('span', { className: 'cpp-rmeta' + (stalled ? ' is-warn' : '') }, bits.join(' · ')) : null,
          h('span', { className: 'cpp-status ' + (DOT[n.status] || '') }, LABEL[n.status] || n.status)),
        open ? Detail({ n }) : null)
    }

    function BranchRow({ n, kids, folded, onFold, onCleanup }) {
      const stalled = n.status === 'in-progress' && n.ts && (Date.now() - n.ts * 1000) >= (n.stallMin || 30) * 60000
      const idx = n.iid ? n.name.indexOf(n.iid) : -1
      const meta = []
      if (n.ahead || n.behind) meta.push('↑' + n.ahead + '↓' + n.behind)
      if (n.dirty) meta.push('脏' + n.dirty)
      meta.push(ageText(n.ts))
      return h('div', { className: 'cpp-row', title: n.name },
        h(Guide, { prefix: n.prefix }),
        kids
          ? h('button', { className: 'cpp-chev', 'aria-label': (folded ? '展开' : '折叠') + n.name, onClick: onFold }, Chev({ open: !folded }))
          : h('span', { className: 'cpp-chev is-hidden' }, '›'),
        h('span', { className: 'cpp-bname' },
          idx >= 0
            ? [n.name.slice(0, idx), h('b', { key: 'i' }, n.iid), n.name.slice(idx + n.iid.length)]
            : n.name),
        n.hint ? h('span', { className: 'cpp-bhint', title: n.hint === '残留可删' ? '已完成票的残留 worktree/分支' : n.hint }, n.hint === '残留可删' ? '残留' : n.hint) : null,
        h('span', { className: 'cpp-rmeta' + (stalled ? ' is-warn' : '') }, meta.filter(Boolean).join(' · ')),
        canCleanup(n)
          ? h('button', {
              className: 'cpp-cleanup', 'aria-label': '清理 ' + n.name,
              title: '按开发收尾规则预检并清理',
              onClick: (e) => { e.stopPropagation(); onCleanup(n.name) },
            }, '清理')
          : null)
    }

    function Detail({ n }) {
      const lines = []
      if (n.branch) {
        const b = n.branch
        lines.push('分支 ' + b.name + ' · ↑' + b.ahead + '↓' + b.behind + (b.dirty ? ' · 脏 ' + b.dirty + ' 文件' : '') + ' · ' + ageText(b.ts))
      }
      if (n.status === 'in-progress') {
        if (n.round) lines.push('impl-log 最后一轮：' + n.round)
        if (n.verify_fails) lines.push('复跑打回 ' + n.verify_fails + ' 次')
        if (!n.round && !n.verify_fails) lines.push('归属与轮次未登记（派发契约未落地）')
      } else if (n.status === 'needs-human') {
        if (n.human_note) lines.push(n.human_note)
        if (n.review_rounds) lines.push('评审第 ' + n.review_rounds + ' 轮')
      } else if (n.status === 'mr-submitted') {
        lines.push('等监督收口')
      } else if (n.status === 'in-review') {
        if (n.review_rounds) lines.push('评审第 ' + n.review_rounds + ' 轮')
        lines.push('评审对话进行中')
      }
      const drill = () => {
        const text = `读 ${n.repo || '当前仓库'} 里 ${n.id} 的 impl-log、评审报告与票文件，用大白话讲这张票现在到哪一步、有没有卡住、下一步是什么`
        navigator.clipboard?.writeText(text).catch(() => {})
      }
      return h('div', { className: 'cpp-detail' },
        h('div', { className: 'cpp-dtitle' }, n.title),
        lines.map((t, i) => h('div', { key: i, className: 'cpp-dline' }, t)),
        h('button', { className: 'cpp-drill', onClick: drill, title: '复制下钻提问，粘到会话即可' }, '复制下钻提问'))
    }

    function CleanupModal({ state, onClose, onApply, onRetry }) {
      const busy = state.phase === 'preview' || state.phase === 'apply'
      React.useEffect(() => {
        const key = (e) => { if (e.key === 'Escape' && state.phase !== 'apply') onClose() }
        document.addEventListener('keydown', key)
        return () => document.removeEventListener('keydown', key)
      }, [state.phase])
      const plan = state.plan
      const result = state.result
      return h('div', { className: 'cpp-modal-backdrop', onMouseDown: (e) => { if (e.target === e.currentTarget && state.phase !== 'apply') onClose() } },
        h('section', { className: 'cpp-modal', role: 'dialog', 'aria-modal': true, 'aria-label': '确认清理' },
          h('h2', null, state.phase === 'result' ? (result?.error ? '清理未完成' : '清理结果') : '确认清理'),
          h('div', { className: 'cpp-modal-sub' }, '按开发收尾规则执行；先预检，再确认，执行前再次核对。'),
          h('code', { className: 'cpp-branch' }, state.branch),
          state.phase === 'preview' ? h('div', { className: 'cpp-loading' }, '正在检查分支、合并目标、软链与待抢救文件…') : null,
          state.phase === 'error' ? h('div', { className: 'cpp-err' }, state.error || '该节点不可清理') : null,
          state.phase === 'confirm' && plan ? h(React.Fragment, null,
            plan.warnings?.map((w, i) => h('div', { key: i, className: 'cpp-warning' }, w)),
            h('dl', { className: 'cpp-facts' },
              h('dt', null, '票'), h('dd', null, `${plan.issue || '—'} · ${plan.title || ''}`),
              h('dt', null, '合并目标'), h('dd', null, plan.merged_into || '未并入授权目标（保留分支）'),
              h('dt', null, 'worktree'), h('dd', null, plan.worktree || '无'),
              plan.salvage?.files ? h(React.Fragment, null,
                h('dt', null, '抢救'), h('dd', null, `${plan.salvage.files} 个文件 / ${plan.salvage.unique_files} 份内容 · ${fmtBytes(plan.salvage.bytes)} · ${plan.salvage.method === 'hardlink' ? '同设备硬链接去重' : '跨设备复制去重'}`)) : null,
              plan.dev_cleanup?.files ? h(React.Fragment, null,
                h('dt', null, 'dev 清理'), h('dd', null, `${plan.dev_cleanup.files} 个过程文件 · ${fmtBytes(plan.dev_cleanup.bytes)}`)) : null),
            h('div', { className: 'cpp-plan' }, (plan.will || []).map((w, i) => h('div', { key: i, className: 'cpp-plan-item' }, w))),
            h('div', { className: 'cpp-safe' }, '确认后按当前 HEAD、文件内容与 worktree 状态重验；有任何变化会停止，不强行删除。')) : null,
          state.phase === 'apply' ? h('div', { className: 'cpp-loading' }, '正在抢救并清理，请稍候；期间不要关闭 DSH…') : null,
          state.phase === 'result' && result ? h(React.Fragment, null,
            result.error ? h('div', { className: 'cpp-err' }, result.error) : null,
            !result.error ? h('div', { className: result.ok ? 'cpp-result-ok' : 'cpp-result-warn' }, result.ok ? '清理完成' : '清理已执行，部分项目保留') : null,
            h('dl', { className: 'cpp-facts' },
              h('dt', null, 'worktree'), h('dd', null, result.worktree_removed ? '已移除' : '未移除'),
              h('dt', null, '本地分支'), h('dd', null, result.branch_deleted ? '已删除' : '已保留'),
              h('dt', null, '抢救文件'), h('dd', null, String(result.salvaged_files || 0)),
              h('dt', null, 'dev 过程文件'), h('dd', null, `清理 ${result.dev_files_removed || 0} 个 · ${fmtBytes(result.bytes_freed || 0)}`)),
            result.salvage_dir ? h('div', { className: 'cpp-path' }, 'salvage：' + result.salvage_dir) : null,
            result.warnings?.map((w, i) => h('div', { key: i, className: 'cpp-warning' }, w))) : null,
          h('div', { className: 'cpp-modal-actions' },
            state.phase === 'error' ? h('button', { className: 'cpp-btn', onClick: onRetry }, '重新预检') : null,
            state.phase !== 'apply' ? h('button', { className: 'cpp-btn', onClick: onClose, autoFocus: state.phase !== 'confirm' }, state.phase === 'result' ? '完成' : '取消') : null,
            state.phase === 'confirm' ? h('button', { className: 'cpp-btn danger', onClick: onApply, autoFocus: true }, '确认清理') : null)))
    }

    function Tree({ data, openMap, foldMap, onToggle, onFold, onCleanup, onRefresh }) {
      const list = data.tree || []
      const rows = []
      const main = data.main || {}
      rows.push(h('div', { key: 'main', className: 'cpp-row is-main' },
        h('span', { className: 'cpp-dot run' }),
        h('span', { className: 'cpp-main-name' }, main.name + ' @' + (main.sha || '')),
        h('span', { className: 'cpp-fright' },
          h('span', { className: 'cpp-rmeta' }, ageText(main.ts) + '推进'),
          h('button', { className: 'cpp-iconbtn', 'aria-label': '刷新进度', title: '刷新', onClick: onRefresh }, '↻'))))
      let i = 0
      while (i < list.length) {
        const n = list[i]
        const stall = { ...n, stallMin: data.stall_min }
        if (n.kind === 'feature') {
          const kids = hasKids(list, i)
          rows.push(h(FeatureRow, { key: nodeKey(n), n: stall, kids, folded: !!foldMap[nodeKey(n)], onFold: () => onFold(nodeKey(n)) }))
        } else if (n.kind === 'ticket') {
          rows.push(h(TicketRow, { key: nodeKey(n), n: stall, open: !!openMap[n.key], onToggle: () => onToggle(n.key) }))
        } else {
          const kids = hasKids(list, i)
          rows.push(h(BranchRow, { key: nodeKey(n), n: stall, kids, folded: !!foldMap[nodeKey(n)], onFold: () => onFold(nodeKey(n)), onCleanup }))
        }
        const collapsible = n.kind !== 'ticket' && hasKids(list, i)
        if (collapsible && foldMap[nodeKey(n)]) {
          const d = depthOf(n)
          i++
          while (i < list.length && depthOf(list[i]) > d) i++
        } else {
          i++
        }
      }
      return h('div', {}, rows)
    }

    function Panel() {
      const [data, setData] = React.useState(null)
      const [error, setError] = React.useState(null)
      const [openMap, setOpenMap] = React.useState({})
      const [foldMap, setFoldMap] = React.useState({})
      const [reload, setReload] = React.useState(0)
      const [cleanup, setCleanup] = React.useState(null)

      React.useEffect(() => {
        let alive = true
        const load = () => jget('/payload')
          .then((d) => { if (alive) { setData(d); setError(null) } })
          .catch((e) => { if (alive) setError(String(e.message ?? e)) })
        load()
        const t = setInterval(load, 10000)
        return () => { alive = false; clearInterval(t) }
      }, [reload])

      const beginCleanup = (branch) => {
        setCleanup({ branch, phase: 'preview' })
        jpost('/cleanup/preview', { branch })
          .then((plan) => {
            if (!plan.eligible) {
              setCleanup((s) => s?.branch === branch ? { ...s, phase: 'error', error: plan.reason || plan.error || '该节点不可清理' } : s)
            } else {
              setCleanup((s) => s?.branch === branch ? { ...s, phase: 'confirm', plan } : s)
            }
          })
          .catch((e) => setCleanup((s) => s?.branch === branch ? { ...s, phase: 'error', error: String(e.message ?? e) } : s))
      }
      const applyCleanup = () => {
        if (!cleanup?.plan) return
        const { branch, plan } = cleanup
        setCleanup((s) => ({ ...s, phase: 'apply' }))
        jpost('/cleanup/apply', { branch, confirm: plan.confirm }, true)
          .then((result) => {
            setCleanup((s) => s?.branch === branch ? { ...s, phase: 'result', result } : s)
            setReload((n) => n + 1)
          })
          .catch((e) => {
            setCleanup((s) => s?.branch === branch ? { ...s, phase: 'result', result: { ok: false, error: String(e.message ?? e) } } : s)
            setReload((n) => n + 1)
          })
      }

      return h('div', { 'data-cpp': true },
        h('style', null, CSS),
        h('div', { className: 'cpp-body' },
          error
            ? h('div', { className: 'cpp-err' }, '数据源不可达：' + error + '\n（服务端半端未启动或 repo 配置有误）')
            : data
              ? h(Tree, {
                  data, openMap, foldMap,
                  onToggle: (k) => setOpenMap((m) => ({ ...m, [k]: !m[k] })),
                  onFold: (k) => setFoldMap((m) => ({ ...m, [k]: !m[k] })),
                  onCleanup: beginCleanup,
                  onRefresh: () => setReload((n) => n + 1),
                })
              : h('div', { className: 'cpp-loading' }, '读取中…')),
        cleanup ? h(CleanupModal, {
          state: cleanup,
          onClose: () => { if (cleanup.phase !== 'apply') setCleanup(null) },
          onApply: applyCleanup,
          onRetry: () => beginCleanup(cleanup.branch),
        }) : null)
    }

    function ProgressGuideArtwork({ size = 26 }) {
      return h('svg', {
        width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
        stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round',
        'aria-hidden': true,
      },
        h('circle', { cx: 6, cy: 5, r: 2.2 }),
        h('circle', { cx: 6, cy: 19, r: 2.2 }),
        h('circle', { cx: 18, cy: 12, r: 2.2 }),
        h('path', { d: 'M6 7.2v9.6M6 12h12' }))
    }

    return {
      inject: ['slots', 'sidebarRightTabs', 'sidebarRight'],
      apply(ctx) {
        const style = document.createElement('style')
        style.setAttribute('data-plugin-css', '@ch-agent/dsh-progress-panel')
        style.textContent = CSS
        document.head.appendChild(style)
        ctx.effect(() => () => style.remove(), 'ch-progress-panel: 样式')

        const slots = (typeof ctx.get === 'function' ? ctx.get('slots') : undefined) || ctx.slots
        const KEY = 'ch-progress'
        ctx.effect(() => ctx.sidebarRightTabs.register({
          id: KEY, kind: KEY, priority: 'builtin', title: () => '进度',
          guide: [{
            id: 'progress', order: 15,
            title: () => '开发进度',
            description: () => '查看需求、分支与各 agent 的实时状态',
            icon: ProgressGuideArtwork,
          }],
        }), 'ch-progress-panel: tab type')
        slots.inject('sidebar.right.pane.tab', () => slots.register(
          { name: 'sidebar.right.pane.tab', key: KEY },
          () => h(Panel),
        ))
        slots.inject('sidebar.right.pane.tab.title', () => slots.register(
          { name: 'sidebar.right.pane.tab.title', key: KEY },
          () => h('span', null, '进度'),
        ))
        const open = () => { try { ctx.sidebarRight.openTab(KEY, { params: {} }); return true } catch { return false } }
        if (!open()) {
          const t1 = setTimeout(() => { try { open() } catch { } }, 3000)
          ctx.effect(() => () => clearTimeout(t1), 'ch-progress-panel: 延迟打开')
        }

        // 会话切换只继承「之前已展开」的状态；用户主动折叠后不强制重开。
        let previousSession = ctx.sidebarRight.mounted?.getSnapshot?.()
        let wasExpanded = ctx.sidebarRight.screen()?.surface.layout.expanded ?? false
        const timer = setInterval(() => {
          try {
            const current = ctx.sidebarRight.mounted.getSnapshot()
            const expandedNow = ctx.sidebarRight.screen()?.surface.layout.expanded ?? false
            if (current === previousSession) {
              wasExpanded = expandedNow
              return
            }
            if (!current) {  // 全局面板等无对话页面：保留上次的展开意图
              previousSession = current
              return
            }
            const carry = wasExpanded || expandedNow
            if (!carry) {
              previousSession = current
              wasExpanded = false
              return
            }
            if (!ctx.sidebarRight.tabsIn(current).some((tab) => tab.kind === KEY)) {
              ctx.sidebarRight.openTab(KEY, { params: {} })
            } else {
              ctx.sidebarRight.actionsFor(current)?.setExpanded(current, true)
            }
            previousSession = current
            wasExpanded = true
          } catch {
            // 新会话的 store 尚未接管时下个周期重试，保留展开意图
          }
        }, 400)
        ctx.effect(() => () => clearInterval(timer), 'ch-progress-panel: 跨对话保持右侧栏')
      },
    }
  },
})
