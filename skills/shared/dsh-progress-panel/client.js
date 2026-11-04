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
    const jpost = async (p, body, action = null) => asJson(await fetch(API + p, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'content-type': 'application/json', ...(action ? { 'x-ch-progress-action': action === true ? 'apply' : action } : {}) },
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
[data-cpp] .cpp-row.is-feat.is-muted{opacity:.6;}
[data-cpp] .cpp-row.is-feat.is-muted .cpp-fname{font-weight:400;}
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
[data-cpp] .cpp-empty{padding:18px 12px;color:var(--cpp-dim);font-size:11.5px;}
[data-cpp] .cpp-row.cpp-node-focused,[data-cpp] .cpp-node-focused > .cpp-row{background:rgba(79,143,247,.18);border-color:rgba(79,143,247,.52);box-shadow:inset 2px 0 0 #4f8ff7;}
[data-cpp] .cpp-node-focused{border-radius:8px;}
[data-cpp] .cpp-detail-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px;}
[data-cpp] .cpp-detail-actions .cpp-drill{margin-top:0;}
[data-cpp] .cpp-detail-actions .cpp-cleanup-detail{color:#f0a83c;}
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
[data-cpp] .cpp-reason{width:100%;margin-top:8px;padding:7px 9px;border-radius:8px;border:1px solid var(--cpp-line);background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.12));color:inherit;font:inherit;font-size:12px;}
[data-cpp] .cpp-modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px;}
[data-cpp] .cpp-btn{border:1px solid var(--cpp-line);background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.12));color:inherit;font:inherit;font-size:12px;border-radius:8px;padding:6px 13px;cursor:pointer;}
[data-cpp] .cpp-btn:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.2));}
[data-cpp] .cpp-btn.primary{background:#2f6fdd;border-color:#2f6fdd;color:#fff;}
[data-cpp] .cpp-btn.primary:hover{background:#4f8ff7;}
[data-cpp] .cpp-btn.danger{background:#c73740;border-color:#c73740;color:#fff;}
[data-cpp] .cpp-btn.danger:hover{background:#df4b54;}
[data-cpp] .cpp-btn:disabled{opacity:.45;cursor:default;}
[data-cpp] .cpp-result-ok{color:#3dd68c;font-size:13px;margin:8px 0;}
[data-cpp] .cpp-result-warn{color:#f0a83c;font-size:13px;margin:8px 0;}
[data-cpp] .cpp-path{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;color:var(--cpp-dim);overflow-wrap:anywhere;margin-top:8px;}
[data-cpp] .cpp-viewtabs{display:flex;gap:3px;padding:3px;background:var(--dsw-alias-bg-layer-3,rgba(128,128,128,.1));border-radius:8px;margin:0 4px 8px;width:max-content;}
[data-cpp] .cpp-viewtab{border:0;background:transparent;color:var(--cpp-dim);font:inherit;font-size:12px;border-radius:6px;padding:4px 12px;cursor:pointer;}
[data-cpp] .cpp-viewtab.is-on{background:var(--dsw-alias-bg-layer-1,#1b1d21);color:var(--dsw-alias-label-primary,#e8eaed);box-shadow:0 1px 3px rgba(0,0,0,.25);}
[data-cpp] .sw-metrics{display:flex;gap:14px;flex-wrap:wrap;padding:2px 6px 8px;color:var(--cpp-dim);font-size:11px;}
[data-cpp] .sw-metrics b{color:var(--dsw-alias-label-primary,#e8eaed);font-size:14px;font-weight:550;margin-right:3px;font-variant-numeric:tabular-nums;}
[data-cpp] .sw-sum{margin:0 4px 8px;padding:9px 11px 10px;border:1px solid var(--cpp-line);border-left:3px solid #5c9dff;border-radius:10px;background:var(--dsw-alias-bg-layer-3,rgba(128,128,128,.08));}
[data-cpp] .sw-sum-head{display:flex;align-items:center;gap:8px;margin-bottom:5px;color:var(--cpp-dim);font-size:10.5px;}
[data-cpp] .sw-sum-title{font-weight:550;color:var(--dsw-alias-label-primary,#e8eaed);font-size:12px;}
[data-cpp] .sw-sum-body{font-size:12px;line-height:1.55;white-space:pre-wrap;color:var(--dsw-alias-label-primary,#e8eaed);}
[data-cpp] .sw-sum-err{color:#f0a83c;font-size:11.5px;line-height:1.5;overflow-wrap:anywhere;}
[data-cpp] .sw-sum-x{margin-left:auto;border:0;background:transparent;color:var(--cpp-dim);font:inherit;font-size:10.5px;border-radius:6px;padding:2px 7px;cursor:pointer;white-space:nowrap;}
[data-cpp] .sw-sum-x:hover{color:#5c9dff;}
[data-cpp] .sw-toolbar{display:flex;align-items:center;gap:5px;flex-wrap:wrap;padding:0 6px 8px;}
[data-cpp] .sw-btn{border:1px solid var(--cpp-line);background:transparent;color:var(--cpp-dim);font:inherit;font-size:11px;border-radius:7px;padding:3px 8px;cursor:pointer;min-height:26px;}
[data-cpp] .sw-btn:hover{color:var(--dsw-alias-label-primary,#e8eaed);border-color:rgba(79,143,247,.5);}
[data-cpp] .sw-btn.is-on{color:#4f8ff7;border-color:rgba(79,143,247,.55);background:rgba(79,143,247,.12);}
[data-cpp] .sw-label{color:var(--cpp-dim);font-size:11px;margin-right:2px;}
[data-cpp] .sw-legend{display:flex;gap:10px;flex-wrap:wrap;padding:0 6px 8px;color:var(--cpp-dim);font-size:10.5px;}
[data-cpp] .sw-lsample{display:inline-block;width:16px;border-top:2px solid #4f8ff7;vertical-align:middle;margin-right:3px;}
[data-cpp] .sw-lsample.miss{border-top-color:#f2585e;border-top-style:dotted;}
[data-cpp] .sw-nsample{display:inline-block;width:12px;height:9px;border-radius:3px;border:1px solid #4f8ff7;background:rgba(79,143,247,.16);vertical-align:-1px;margin-right:3px;}
[data-cpp] .sw-nsample.rel{border-color:rgba(79,143,247,.45);background:rgba(79,143,247,.08);}
[data-cpp] .sw-viewport{overflow-x:auto;overflow-y:hidden;border:1px solid var(--cpp-line);border-radius:10px;background:var(--dsw-alias-bg-layer-3,rgba(128,128,128,.08));margin:0 4px;}
[data-cpp] .sw-canvas{position:relative;min-width:100%;}
[data-cpp] .sw-lane{--sw-bg:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.06));position:relative;background:var(--sw-bg);border-top:1px solid var(--cpp-line);border-bottom:1px solid var(--cpp-line);}
[data-cpp] .sw-lane + .sw-lane{margin-top:6px;}
[data-cpp] .sw-lane.is-ctx{--sw-bg:rgba(128,128,128,.04);}
[data-cpp] .sw-lane.is-sel{--sw-bg:rgba(79,143,247,.14);border-color:rgba(79,143,247,.45);}
[data-cpp] .sw-lane.is-rel{--sw-bg:rgba(79,143,247,.07);}
[data-cpp] .sw-hit{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent;cursor:pointer;padding:0;z-index:0;}
[data-cpp] .sw-labell{position:sticky;left:0;display:inline-block;vertical-align:top;height:100%;padding:7px 8px;background:var(--sw-bg);border-right:1px solid var(--cpp-line);z-index:4;pointer-events:none;box-sizing:border-box;overflow:hidden;}
[data-cpp] .sw-lrow{display:flex;align-items:baseline;gap:5px;min-width:0;}
[data-cpp] .sw-lrow .cpp-fname{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
[data-cpp] .sw-lcount{color:var(--cpp-dim);font-size:10.5px;font-variant-numeric:tabular-nums;flex:none;}
[data-cpp] .sw-lane.is-sel .sw-labell{box-shadow:inset 3px 0 0 #4f8ff7;}
[data-cpp] .sw-lmeta{color:var(--cpp-dim);font-size:10.5px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
[data-cpp] .sw-prog{display:flex;height:4px;border-radius:4px;overflow:hidden;background:rgba(128,128,128,.15);margin-top:7px;}
[data-cpp] .sw-prog i{display:block;height:100%;}
[data-cpp] .sw-prog .pd{background:#3dd68c;}
[data-cpp] .sw-prog .pa{background:#4f8ff7;}
[data-cpp] .sw-prog .pr{background:rgba(154,160,166,.4);}
[data-cpp] .sw-lfoot{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-top:5px;color:var(--cpp-dim);font-size:10.5px;}
[data-cpp] .sw-expand{pointer-events:auto;border:0;background:transparent;color:#4f8ff7;font:inherit;font-size:10.5px;padding:2px 0;cursor:pointer;white-space:nowrap;}
[data-cpp] .sw-edges{position:absolute;inset:0;z-index:1;pointer-events:none;overflow:visible;}
[data-cpp] .sw-edge{fill:none;stroke:rgba(154,160,166,.6);stroke-width:1.2;opacity:.5;}
[data-cpp] .sw-edge.ext{stroke:rgba(79,143,247,.8);stroke-width:1.4;opacity:.75;}
[data-cpp] .sw-edge.body{stroke:#b18cff;stroke-dasharray:5 4;opacity:.75;}
[data-cpp] .sw-edge.miss{stroke:#f2585e;stroke-dasharray:2 4;}
[data-cpp] .sw-edge.on{stroke:#4f8ff7;stroke-width:2.4;opacity:1;}
[data-cpp] .sw-edge.body.on{stroke:#b18cff;}
[data-cpp] .sw-edge.miss.on{stroke:#f2585e;}
[data-cpp] .sw-edge.off{opacity:.08;}
[data-cpp] .sw-issue{position:absolute;z-index:2;width:136px;height:52px;border:1px solid var(--cpp-line);border-left:3px solid var(--cpp-dim);border-radius:7px;background:var(--dsw-alias-bg-layer-4,rgba(128,128,128,.12));color:var(--dsw-alias-label-primary,#e8eaed);padding:4px 6px;text-align:left;font:inherit;font-size:11px;cursor:pointer;transition:opacity .12s;}
[data-cpp] .sw-issue:hover{border-color:rgba(79,143,247,.6);}
[data-cpp] .sw-issue.st-in-progress{border-left-color:#4f8ff7;background:rgba(79,143,247,.16);}
[data-cpp] .sw-issue.st-in-review{border-left-color:#b18cff;background:rgba(177,140,255,.16);}
[data-cpp] .sw-issue.st-mr-submitted{border-left-color:#5c9dff;background:rgba(92,157,255,.16);}
[data-cpp] .sw-issue.st-needs-human{border-left-color:#f2585e;background:rgba(242,88,94,.16);}
[data-cpp] .sw-issue.st-done{border-left-color:#3dd68c;background:rgba(61,214,140,.13);}
[data-cpp] .sw-issue.st-missing{border-left-color:#f2585e;}
[data-cpp] .sw-issue.is-miss{border-style:dashed;border-left-style:solid;background:rgba(242,88,94,.12);}
[data-cpp] .sw-issue.is-selfeat{background:rgba(79,143,247,.2);border-color:rgba(79,143,247,.55);}
[data-cpp] .sw-issue.is-rel{background:rgba(79,143,247,.1);border-color:rgba(79,143,247,.35);}
[data-cpp] .sw-issue.is-seliss{box-shadow:0 0 0 2px #4f8ff7;}
[data-cpp] .sw-issue.is-dim{opacity:.25;}
[data-cpp] .sw-issue:disabled{cursor:default;}
[data-cpp] .sw-iid{display:block;font-weight:550;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
[data-cpp] .sw-ititle{display:block;color:var(--cpp-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px;}
[data-cpp] .sw-ist{display:block;color:var(--cpp-dim);margin-top:1px;font-size:10.5px;}
[data-cpp] .sw-detail{padding:9px 6px 4px;border-top:1px solid var(--cpp-line);font-size:11.5px;margin-top:8px;}
[data-cpp] .sw-counts{display:flex;gap:12px;flex-wrap:wrap;color:var(--cpp-dim);margin-top:5px;}
[data-cpp] .sw-counts b{color:var(--dsw-alias-label-primary,#e8eaed);font-weight:550;font-variant-numeric:tabular-nums;}
@media (prefers-reduced-motion:reduce){[data-cpp] .sw-issue{transition:none;}}
`

    const LABEL = { 'in-progress': '在跑', 'in-review': '评审中', 'mr-submitted': '待合并', 'needs-human': '等人' }
    const FSTATE_LABEL = { closed: '已关闭', ready: '待派发', done: '已完结' }
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
    const canCleanup = (n) => n.kind === 'branch' || n.kind === 'ticket'

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

    function FeatureRow({ n, kids, folded, onFold, focused }) {
      const c = n.counts
      const inflight = (c.run || 0) + (c.review || 0) + (c.mr || 0) + (c.human || 0)
      const seg = (cls, v) => (v ? h('i', { key: cls, className: cls, style: { width: (100 * v / c.total) + '%' } }) : null)
      return h('div', { className: 'cpp-row is-feat' + (n.fstate ? ' is-muted' : '') + (focused ? ' cpp-node-focused' : ''), 'data-node-key': nodeKey(n) },
        h(Guide, { prefix: n.prefix }),
        kids
          ? h('button', { className: 'cpp-chev', 'aria-label': (folded ? '展开' : '折叠') + n.slug, onClick: onFold }, Chev({ open: !folded }))
          : h('span', { className: 'cpp-chev is-hidden' }, '›'),
        h('span', { className: 'cpp-fname' }, n.slug),
        n.fstate
          ? h('span', { className: 'cpp-bhint', title: '无在制票（与开发中视图同一判定）' }, FSTATE_LABEL[n.fstate] || '无在制')
          : (n.agents && n.agents.length
              ? h('span', { className: 'cpp-agents' }, n.agents.map((a) => h('span', { key: a, className: 'cpp-agent' }, a)))
              : null),
        h('span', { className: 'cpp-fright' },
          h('span', { className: 'cpp-bar', title: `已完结 ${c.done} · 在途 ${inflight} · 待派发 ${c.ready || 0}，共 ${c.total} 票` },
            seg('s-closed', c.done), seg('s-inflight', inflight), seg('s-ready', c.ready)),
          h('span', { className: 'cpp-fcount' }, c.done + '/' + c.total)))
    }

    function TicketRow({ n, open, onToggle, onCleanup, onCloseIssue, focused }) {
      const stalled = n.status === 'in-progress' && n.age_ts && (Date.now() - n.age_ts * 1000) >= (n.stallMin || 30) * 60000
      const bits = []
      if (n.ahead || n.behind) bits.push('↑' + n.ahead + '↓' + n.behind)
      if (n.dirty) bits.push('脏' + n.dirty)
      if (n.status === 'in-progress' && n.age_ts) bits.push(stalled ? '⚠ ' + ageText(n.age_ts, '无动静') : ageText(n.age_ts))
      return h('div', { className: focused ? 'cpp-node-focused' : '', 'data-node-key': nodeKey(n) },
        h('button', { className: 'cpp-row', 'aria-expanded': !!open, onClick: onToggle },
          h(Guide, { prefix: n.prefix }),
          h('span', { className: 'cpp-chev' + (open ? ' is-open' : ''), 'aria-hidden': true }, '›'),
          h('span', { className: 'cpp-dot ' + (stalled ? 'stall' : (DOT[n.status] || 'run')) }),
          h('span', { className: 'cpp-rid' }, n.id),
          h('span', { className: 'cpp-rtitle', title: n.title }, n.title),
          bits.length ? h('span', { className: 'cpp-rmeta' + (stalled ? ' is-warn' : '') }, bits.join(' · ')) : null,
          h('span', { className: 'cpp-status ' + (DOT[n.status] || '') }, LABEL[n.status] || n.status)),
        open ? Detail({ n, onCleanup, onCloseIssue }) : null)
    }

    function BranchRow({ n, kids, folded, onFold, onCleanup, focused }) {
      const stalled = n.status === 'in-progress' && n.ts && (Date.now() - n.ts * 1000) >= (n.stallMin || 30) * 60000
      const idx = n.iid ? n.name.indexOf(n.iid) : -1
      const meta = []
      if (n.ahead || n.behind) meta.push('↑' + n.ahead + '↓' + n.behind)
      if (n.dirty) meta.push('脏' + n.dirty)
      meta.push(ageText(n.ts))
      return h('div', { className: 'cpp-row' + (focused ? ' cpp-node-focused' : ''), title: n.name, 'data-node-key': nodeKey(n) },
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

    function Detail({ n, onCleanup, onCloseIssue }) {
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
        h('div', { className: 'cpp-detail-actions' },
          h('button', { className: 'cpp-drill', onClick: drill, title: '复制下钻提问，粘到会话即可' }, '复制下钻提问'),
          h('button', {
            className: 'cpp-drill cpp-cleanup-detail',
            onClick: () => onCleanup({ branch: n.branch?.name, issue: n.key }),
            title: '按开发收尾规则预检；是否可执行以预检为准',
          }, '清理'),
          n.status !== 'done' ? h('button', {
            className: 'cpp-drill',
            onClick: () => onCloseIssue(n),
            title: '仅把需求状态改为 deferred，不删除分支/worktree/dev',
          }, '关闭需求') : null))
    }

    function CloseIssueModal({ state, onClose, onApply, onReason }) {
      const result = state.result
      return h('div', { className: 'cpp-modal-backdrop', onMouseDown: (e) => { if (e.target === e.currentTarget && state.phase !== 'apply') onClose() } },
        h('section', { className: 'cpp-modal', role: 'dialog', 'aria-modal': true, 'aria-label': '关闭需求' },
          h('h2', null, '关闭需求'),
          h('div', { className: 'cpp-modal-sub' }, '只把票状态改为 deferred 并记录关闭事件；不删除分支、worktree 或 dev 文件。'),
          h('code', { className: 'cpp-branch' }, state.issue),
          state.phase === 'confirm' ? h(React.Fragment, null,
            h('dl', { className: 'cpp-facts' },
              h('dt', null, '标题'), h('dd', null, state.title),
              h('dt', null, '当前状态'), h('dd', null, state.status)),
            h('div', { className: 'cpp-warning' }, '关闭后该需求从在途列表消失；依赖它的其他票不会因此自动解锁。'),
            h('input', {
              className: 'cpp-reason', value: state.reason, autoFocus: true,
              onChange: (e) => onReason(e.target.value),
              placeholder: '关闭原因（写入 digest）',
            })) : null,
          state.phase === 'apply' ? h('div', { className: 'cpp-loading' }, '正在关闭需求并写入事件…') : null,
          state.phase === 'result' && result ? h(React.Fragment, null,
            result.error ? h('div', { className: 'cpp-err' }, result.error) : null,
            !result.error ? h('div', { className: 'cpp-result-ok' }, `已关闭：${result.previous_status} → ${result.status}`) : null,
            result.event?.note ? h('div', { className: 'cpp-dline' }, result.event.note) : null) : null,
          h('div', { className: 'cpp-modal-actions' },
            state.phase !== 'apply' ? h('button', { className: 'cpp-btn', onClick: onClose }, state.phase === 'result' ? '完成' : '取消') : null,
            state.phase === 'confirm' ? h('button', { className: 'cpp-btn primary', onClick: onApply }, '关闭需求') : null)))
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
          h('h2', null, state.phase === 'result' ? (result?.error ? '清理未完成' : '清理结果') : (plan?.mode === 'abandon' ? '选择清理方式' : '确认清理')),
          h('div', { className: 'cpp-modal-sub' }, '先保存完整分支补丁和未提交改动，再按你的选择执行。'),
          h('code', { className: 'cpp-branch' }, state.branch),
          state.phase === 'preview' ? h('div', { className: 'cpp-loading' }, '正在检查分支、合并目标、软链与待抢救文件…') : null,
          state.phase === 'error' ? h('div', { className: 'cpp-err' }, state.error || '该节点不可清理') : null,
          state.phase === 'confirm' && plan ? h(React.Fragment, null,
            plan.warnings?.map((w, i) => h('div', { key: i, className: 'cpp-warning' }, w)),
            h('dl', { className: 'cpp-facts' },
              h('dt', null, '票'), h('dd', null, `${plan.issue || '—'} · ${plan.title || ''}`),
              plan.mode !== 'dev-only' ? h(React.Fragment, null,
                h('dt', null, '合并目标'), h('dd', null, plan.merged_into || plan.merge_target || '未识别')) : null,
              h('dt', null, 'worktree'), h('dd', null, plan.worktree || '无'),
              plan.salvage?.files ? h(React.Fragment, null,
                h('dt', null, '抢救'), h('dd', null, `${plan.salvage.files} 个文件 / ${plan.salvage.unique_files} 份内容 · ${fmtBytes(plan.salvage.bytes)} · ${plan.salvage.method === 'hardlink' ? '同设备硬链接去重' : '跨设备复制去重'}`)) : null,
              plan.dev_cleanup?.files ? h(React.Fragment, null,
                h('dt', null, 'dev 清理'), h('dd', null, `${plan.dev_cleanup.files} 个过程文件 · ${fmtBytes(plan.dev_cleanup.bytes)}`)) : null),
            h('div', { className: 'cpp-plan' }, (plan.will || []).map((w, i) => h('div', { key: i, className: 'cpp-plan-item' }, w))),
            h('div', { className: 'cpp-safe' }, plan.mode === 'abandon'
              ? '丢弃开发：不合入，删除 worktree 和本地分支，并把需求关闭（deferred）；合并保留：先合入已提交内容再清理，票状态不变。未提交改动始终只保存在 salvage。'
              : '确认后自动重验并执行；有任何变化会停止。')) : null,
          state.phase === 'apply' ? h('div', { className: 'cpp-loading' }, state.action === 'merge' ? '正在保存补丁并尝试合并，请稍候…' : '正在保存补丁并清理，请稍候…') : null,
          state.phase === 'result' && result ? h(React.Fragment, null,
            result.error ? h('div', { className: 'cpp-err' }, result.error) : null,
            !result.error ? h('div', { className: result.ok ? 'cpp-result-ok' : 'cpp-result-warn' }, result.ok ? '清理完成' : '清理已执行，部分项目保留') : null,
            h('dl', { className: 'cpp-facts' },
              h('dt', null, '方式'), h('dd', null, result.action === 'merge' ? '合并保留' : result.action === 'discard' ? '丢弃开发' : result.action === 'dev-only' ? '仅 dev 清理' : '收尾清理'),
              result.merge_commit ? h(React.Fragment, null,
                h('dt', null, '合并提交'), h('dd', null, result.merged_into + ' @' + result.merge_commit.slice(0, 10))) : null,
              result.branch_patch_saved ? h(React.Fragment, null,
                h('dt', null, '分支补丁'), h('dd', null, '已保存 branch.patch')) : null,
              h('dt', null, 'worktree'), h('dd', null, result.worktree_removed ? '已移除' : '未移除'),
              h('dt', null, '本地分支'), h('dd', null, result.branch_deleted ? '已删除' : '已保留'),
              h('dt', null, '抢救文件'), h('dd', null, String(result.salvaged_files || 0)),
              h('dt', null, 'dev 过程文件'), h('dd', null, `清理 ${result.dev_files_removed || 0} 个 · ${fmtBytes(result.bytes_freed || 0)}`)),
            result.salvage_dir ? h('div', { className: 'cpp-path' }, 'salvage：' + result.salvage_dir) : null,
            result.warnings?.map((w, i) => h('div', { key: i, className: 'cpp-warning' }, w))) : null,
          h('div', { className: 'cpp-modal-actions' },
            state.phase === 'error' ? h('button', { className: 'cpp-btn', onClick: onRetry }, '重新预检') : null,
            state.phase !== 'apply' ? h('button', { className: 'cpp-btn', onClick: onClose, autoFocus: state.phase !== 'confirm' }, state.phase === 'result' ? '完成' : '取消') : null,
            state.phase === 'confirm' && plan.mode === 'abandon'
              ? h(React.Fragment, null,
                  h('button', { className: 'cpp-btn danger', onClick: () => onApply('discard') }, '丢弃开发'),
                  plan.merge_target ? h('button', { className: 'cpp-btn primary', onClick: () => onApply('merge'), autoFocus: true }, '合并保留') : null)
              : state.phase === 'confirm'
                ? h('button', { className: 'cpp-btn danger', onClick: () => onApply('close'), autoFocus: true }, '确认清理')
                : null)))
    }

    // ---------- 开发中泳道视图（横向泳道 + 跨需求依赖联动高亮） ----------

    const SW_ACTIVE = ['in-progress', 'in-review', 'mr-submitted', 'needs-human']
    // 未展开泳道的默认队列：在制票 + 已开票可派发（confirmed）的后续票；proposed（待确认草稿）不进
    const SW_QUEUE = SW_ACTIVE.concat(['confirmed'])
    const SW_STATUS = { 'in-progress': '在跑', 'in-review': '评审中', 'mr-submitted': '待合并', 'needs-human': '等人', done: '已完结', confirmed: '可派发', proposed: '待确认', '未知': '未知', missing: '目标缺失' }
    const swNorm = (v) => String(v || '').replace(/[\s/]+/g, '').toLowerCase()

    function swimRefFeature(data, edge) {
      if (edge.to_feature) return edge.to_feature
      const ref = String(edge.ref || '')
      let label = ''
      if (ref.includes('#')) label = ref.split('#')[0]
      else if (ref.includes('/')) label = ref.split('/').slice(0, -1).join('/')
      const key = swNorm(label)
      const hits = (data.features || []).filter((f) => swNorm(f.id) === key || swNorm(f.slug) === key)
      return hits.length === 1 ? hits[0].id : null
    }

    function swimRefIssue(edge) {
      const parts = String(edge.ref || '').split(/[#/]/)
      return parts[parts.length - 1] || '?'
    }

    function swimScene(data, opts) {
      const { filter, selected, showAllContext, extra, vw } = opts
      const issues = new Map()
      const features = new Map()
      ;(data.features || []).forEach((f) => {
        features.set(f.id, f)
        ;(f.issues || []).forEach((i) => issues.set(i.uid, i))
      })
      const trusted = (f) => new Set((f.agents || []).filter((a) => a.source !== 'branch').map((a) => a.host))
      const developing = (data.features || []).filter((f) => f.active)
      const primary = developing.filter((f) => filter === 'all'
        || (filter === 'unassigned' ? trusted(f).size === 0 : trusted(f).has(filter)))
      const ids = new Set(primary.map((f) => f.id))
      const ctx = new Set()
      ;(data.dependencies || []).forEach((e) => {
        if (e.kind !== 'external' && e.kind !== 'body') return
        const tf = swimRefFeature(data, e)
        const linkedPrimary = ids.has(e.from_feature) || (tf && ids.has(tf))
        const linkedSel = selected && (e.from_feature === selected || tf === selected)
        if ((showAllContext && (filter === 'all' || linkedPrimary)) || (!showAllContext && linkedSel)) {
          if (e.from_feature && !ids.has(e.from_feature)) ctx.add(e.from_feature)
          if (tf && !ids.has(tf)) ctx.add(tf)
        }
      })
      // 选中的需求始终可见：合并/关闭后脱离“开发中”时，以上下文泳道保持选中，不静默跳走
      if (selected && !ids.has(selected) && features.has(selected)) ctx.add(selected)
      const visible = [...primary, ...(data.features || []).filter((f) => ctx.has(f.id))]
      const visIds = new Set(visible.map((f) => f.id))
      const nodes = new Map()
      // 已完结票只保留最近 3 天（完结时间随 payload 下发 done_ts）；未知完结时间视同过期
      const doneCutoff = (data.generated || Date.now() / 1000) - 3 * 86400
      const addActual = (uid) => {
        const i = issues.get(uid)
        if (i && visIds.has(i.feature) && !(i.status === 'done' && (!i.done_ts || i.done_ts < doneCutoff))) nodes.set(uid, i)
      }
      visible.forEach((f) => {
        const expanded = f.id === selected || extra.has(f.id)
        if (expanded) { (f.issues || []).forEach((i) => addActual(i.uid)); return }
        const seeds = new Set()
        ;(f.issues || []).forEach((i) => { if (SW_QUEUE.includes(i.status)) seeds.add(i.uid) })
        ;(data.dependencies || []).forEach((e) => {
          if (e.kind === 'milestone') return
          if (e.from_feature === f.id && (e.kind === 'external' || e.kind === 'body')) seeds.add(e.from)
          const t = e.to && issues.get(e.to)
          if (t && t.feature === f.id && (e.kind === 'external' || e.kind === 'body')) seeds.add(e.to)
        })
        const include = new Set(seeds)
        ;(data.dependencies || []).forEach((e) => {
          if (e.kind !== 'internal' || e.from_feature !== f.id) return
          if (seeds.has(e.from) || seeds.has(e.to)) { include.add(e.from); if (e.to) include.add(e.to) }
        })
        include.forEach(addActual)
      })
      const edges = []
      ;(data.dependencies || []).forEach((e) => {
        if (e.kind === 'milestone' || !nodes.has(e.from)) return
        let source = e.to
        if (source && !nodes.has(source)) return
        if (!source) {
          const tf = e.kind === 'internal' ? e.from_feature : swimRefFeature(data, e)
          source = 'missing:' + (tf || e.from_feature) + '#' + swimRefIssue(e)
          if (!nodes.has(source)) nodes.set(source, { uid: source, id: swimRefIssue(e), key: source,
            feature: tf || e.from_feature, title: '目标 issue 不在当前票库', status: 'missing', missing: true })
        }
        const target = e.from
        edges.push({ source, target, kind: e.kind, missing: !e.resolved, ref: e.ref, origin: e.origin,
          cross: nodes.get(source).feature !== nodes.get(target).feature })
      })
      const levels = new Map([...nodes.keys()].map((uid) => [uid, 0]))
      for (let k = 0; k < nodes.size; k++) {
        let changed = false
        edges.forEach((e) => {
          const nx = (levels.get(e.source) || 0) + 1
          if (nx > (levels.get(e.target) || 0)) { levels.set(e.target, nx); changed = true }
        })
        if (!changed) break
      }
      const narrow = vw < 560
      const labelW = narrow ? 118 : 150
      const col = narrow ? 128 : 150
      const nodeW = col - 16
      const positions = new Map()
      const lanes = []
      let y = 8
      let maxLevel = 0
      visible.forEach((f) => {
        const own = [...nodes.values()].filter((n) => n.feature === f.id)
        if (!own.length) return
        const byLevel = new Map()
        own.forEach((n) => {
          const lv = levels.get(n.uid) || 0
          maxLevel = Math.max(maxLevel, lv)
          if (!byLevel.has(lv)) byLevel.set(lv, [])
          byLevel.get(lv).push(n)
        })
        const rows = [...byLevel.values()].map((g) => g.length)
        const laneH = Math.max(...rows) * 64 + 16
        byLevel.forEach((group, lv) => group.sort((a, b) => String(a.id).localeCompare(String(b.id)))
          .forEach((n, row) => positions.set(n.uid, { x: labelW + lv * col + 8, y: 8 + row * 64, laneY: y })))
        lanes.push({ feature: f, y, height: laneH,
          actual: own.filter((n) => !n.missing).length, missing: own.filter((n) => n.missing).length,
          expanded: f.id === selected || extra.has(f.id) })
        y += laneH + 6
      })
      return { nodes, edges, positions, lanes, labelW, nodeW, issues, features,
        width: labelW + (maxLevel + 1) * col + 18, height: y }
    }

    function swimRelated(data, issues, selected) {
      const related = new Set()
      const relatedVirt = new Set()
      const lanes = new Set()
      if (!selected) return { related, relatedVirt, lanes }
      ;(data.dependencies || []).forEach((e) => {
        if (e.kind !== 'external' && e.kind !== 'body') return
        const tf = swimRefFeature(data, e)
        if (e.from_feature === selected) {
          if (e.to) {
            related.add(e.to)
            const t = issues.get(e.to)
            if (t) lanes.add(t.feature)
          } else {
            relatedVirt.add('missing:' + (tf || e.from_feature) + '#' + swimRefIssue(e))
            if (tf) lanes.add(tf)
          }
        }
        const t = e.to && issues.get(e.to)
        if (t && t.feature === selected) { related.add(e.from); lanes.add(e.from_feature) }
      })
      return { related, relatedVirt, lanes }
    }

    // 开发摘要：宿主模型生成的项目管理总结（挂载时取一次，手动刷新旁路服务端缓存）
    function SwimSummary({ repo, refreshKey, onRefresh }) {
      const [state, setState] = React.useState({ phase: 'loading' })
      React.useEffect(() => {
        let alive = true
        setState({ phase: 'loading' })
        jget('/summary?repo=' + encodeURIComponent(repo) + (refreshKey > 0 ? '&force=1' : ''))
          .then((d) => {
            if (!alive) return
            setState(d.ok
              ? { phase: 'ok', text: d.summary, model: d.model, at: d.generated }
              : { phase: 'hint', error: d.error || '摘要生成失败' })
          })
          .catch((e) => { if (alive) setState({ phase: 'hint', error: String(e.message ?? e) }) })
        return () => { alive = false }
      }, [repo, refreshKey])
      const time = state.at ? new Date(state.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
      return h('div', { className: 'sw-sum' },
        h('div', { className: 'sw-sum-head' },
          h('span', { className: 'sw-sum-title' }, '开发摘要'),
          state.phase === 'ok' ? h('span', null, `${state.model} · ${time} 生成`) : null,
          state.phase === 'loading' ? h('span', null, '正在请宿主模型总结…') : null,
          h('button', { className: 'sw-sum-x', onClick: onRefresh },
            state.phase === 'loading' && refreshKey === 0 ? '稍后再说' : '重新总结')),
        state.phase === 'ok'
          ? h('div', { className: 'sw-sum-body' }, state.text)
          : state.phase === 'hint'
            ? h('div', { className: 'sw-sum-err' }, state.error)
            : null)
    }

    function SwimView({ data, repo, onCleanup, onCloseIssue, onRefresh }) {
      const [filter, setFilter] = React.useState('all')
      const [selected, setSelected] = React.useState(null)
      const [selectedIssue, setSelectedIssue] = React.useState('')
      const [showAll, setShowAll] = React.useState(false)
      const [extra, setExtra] = React.useState(() => new Set())
      const [summaryReload, setSummaryReload] = React.useState(0)
      const viewportRef = React.useRef(null)
      const [vw, setVw] = React.useState(360)
      React.useEffect(() => {
        const el = viewportRef.current
        if (!el || typeof ResizeObserver === 'undefined') return
        const ro = new ResizeObserver(() => setVw(el.clientWidth))
        ro.observe(el)
        setVw(el.clientWidth)
        return () => ro.disconnect()
      }, [])
      const developing = (data.features || []).filter((f) => f.active)
      const trusted = (f) => new Set((f.agents || []).filter((a) => a.source !== 'branch').map((a) => a.host))
      const filtered = developing.filter((f) => filter === 'all'
        || (filter === 'unassigned' ? trusted(f).size === 0 : trusted(f).has(filter)))
      // 选中态以“需求仍存在于数据中”为准（脱离开发中时场景函数会以上下文泳道保留它）；
      // 只有需求彻底消失才回退到第一个可见需求
      let effSelected = (data.features || []).some((f) => f.id === selected) ? selected : null
      if (selected && !effSelected) effSelected = filtered[0] ? filtered[0].id : null
      const ctxCount = (data.features || []).filter((f) => !f.active
        && (data.dependencies || []).some((e) => (e.kind === 'external' || e.kind === 'body')
          && (e.from_feature === f.id || swimRefFeature(data, e) === f.id))).length
      const scene = React.useMemo(
        () => swimScene(data, { filter, selected: effSelected, showAllContext: showAll, extra, vw }),
        [data, filter, effSelected, showAll, extra, vw])
      const rel = React.useMemo(() => swimRelated(data, scene.issues, effSelected), [data, scene, effSelected])
      const selectFeature = (id) => {
        if (effSelected === id) { clearSel(); return }  // 再次点击同一需求区域 = 取消选中
        setSelected(id); setSelectedIssue(''); setExtra(new Set())
      }
      const clearSel = () => { setSelected(null); setSelectedIssue(''); setExtra(new Set()) }

      if (!(data.features || []).length) {
        return h('div', { className: 'cpp-empty' }, '没有可展示的开发中需求')
      }
      const width = Math.max(vw - 2, scene.width)
      const edgeEls = scene.edges.map((e, i) => {
        const s = scene.positions.get(e.source)
        const t = scene.positions.get(e.target)
        if (!s || !t) return null
        const sNode = scene.nodes.get(e.source)
        const tNode = scene.nodes.get(e.target)
        const touches = effSelected && (sNode.feature === effSelected || tNode.feature === effSelected)
        const internal = effSelected && sNode.feature === effSelected && tNode.feature === effSelected
        const on = !!effSelected && (internal || (e.cross && touches))
        const x1 = s.x + scene.nodeW
        const y1 = s.laneY + s.y + 26
        const x2 = t.x
        const y2 = t.laneY + t.y + 26
        const bend = Math.max(32, Math.abs(x2 - x1) * .42)
        const cls = (e.kind === 'body' ? ' body' : e.kind === 'external' ? ' ext' : '')
          + (e.missing ? ' miss' : '') + (on ? ' on' : (effSelected ? ' off' : ''))
        return h('path', { key: i, className: 'sw-edge' + cls,
          d: `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`,
          markerEnd: `url(#sw-arrow-${on ? 'a' : e.missing ? 'm' : 'n'})` },
          h('title', null, `${e.origin} · ${e.ref}`))
      })
      const laneEls = scene.lanes.map((l) => {
        const f = l.feature
        const isSel = f.id === effSelected
        const isRel = rel.lanes.has(f.id) && !isSel
        const hidden = Math.max(0, (f.issues || []).length - l.actual)
        const activeN = (f.issues || []).filter((i) => SW_ACTIVE.includes(i.status)).length
        const total = f.counts.total || 1
        const cls = 'sw-lane' + (f.active ? '' : ' is-ctx') + (isSel ? ' is-sel' : '') + (isRel ? ' is-rel' : '')
        return h('section', { key: f.id, className: cls, style: { height: l.height + 'px' } },
          h('button', { className: 'sw-hit', 'aria-label': (effSelected === f.id ? '取消选择 ' : '选择需求 ') + f.slug, onClick: () => selectFeature(f.id) }),
          h('div', { className: 'sw-labell', style: { width: scene.labelW + 'px' }, title: `${f.module}/${f.slug}` },
            h('div', { className: 'sw-lrow' },
              h('span', { className: 'cpp-fname' }, f.slug),
              h('span', { className: 'sw-lcount' }, `${f.counts.done || 0}/${f.counts.total}`)),
            h('div', { className: 'sw-lmeta' }, `${f.module} · ${f.active ? '开发中' : (FSTATE_LABEL[f.fstate] || '依赖上下文')}`),
            h('div', { className: 'sw-prog', 'aria-label': `已完结 ${f.counts.done || 0}，在制 ${activeN}，共 ${f.counts.total} 票` },
              h('i', { className: 'pd', style: { width: (100 * (f.counts.done || 0) / total) + '%' } }),
              h('i', { className: 'pa', style: { width: (100 * activeN / total) + '%' } })),
            h('div', { className: 'sw-lfoot' },
              h('span', null, `${l.actual}/${(f.issues || []).length} issue` + (l.missing ? ` · ${l.missing} 缺失` : '')),
              isSel
                ? h('span', null, '全部高亮')
                : h('button', {
                    className: 'sw-expand', 'aria-label': (l.expanded ? '收起 ' : '展开 ') + f.slug,
                    onClick: (ev) => {
                      ev.stopPropagation()
                      setExtra((m) => { const nx = new Set(m); if (nx.has(f.id)) nx.delete(f.id); else nx.add(f.id); return nx })
                    },
                  }, l.expanded ? '收起' : hidden ? `展开 ${hidden} 票` : '已展开'))))
      })
      const nodeEls = [...scene.nodes.values()].map((n) => {
        const p = scene.positions.get(n.uid)
        if (!p) return null  // 目标缺失节点在不可见泳道时无坐标：跳过渲染，仅参与箭头判定（与原型一致）
        const isSelF = n.feature === effSelected
        const isRel = rel.related.has(n.uid) || rel.relatedVirt.has(n.uid)
        const dim = effSelected && !isSelF && !isRel
        return h('button', {
          key: n.uid,
          className: 'sw-issue st-' + n.status + (n.missing ? ' is-miss' : '')
            + (isSelF ? ' is-selfeat' : '') + (isRel ? ' is-rel' : '') + (dim ? ' is-dim' : '')
            + (selectedIssue === n.uid ? ' is-seliss' : ''),
          style: { left: p.x + 'px', top: (p.laneY + p.y) + 'px', width: scene.nodeW + 'px' },
          disabled: !!n.missing,
          'aria-label': `${n.feature} ${n.id} ${n.title}`,
          onClick: () => { if (n.missing) return; setSelected(n.feature); setSelectedIssue(n.uid); setExtra(new Set()) },
        },
          h('span', { className: 'sw-iid' }, n.id),
          h('span', { className: 'sw-ititle' }, n.title),
          h('span', { className: 'sw-ist' }, SW_STATUS[n.status] || n.status))
      })
      const f = scene.features.get(effSelected)
      const selIss = selectedIssue ? scene.issues.get(selectedIssue) : null
      let detail
      if (!f) {
        detail = [h('div', { key: 't', className: 'cpp-dtitle' }, '未选择需求'),
          h('div', { key: 'l', className: 'cpp-dline' }, '点击任意需求块，查看本需求与跨需求依赖的联动高亮。')]
      } else {
        const relCount = rel.related.size + rel.relatedVirt.size
        const selEdges = scene.edges.filter((e) => {
          const s = scene.nodes.get(e.source)
          const t = scene.nodes.get(e.target)
          return (s.feature === effSelected && t.feature === effSelected)
            || (e.cross && (s.feature === effSelected || t.feature === effSelected))
        }).length
        const drill = () => {
          const text = selIss
            ? `读 ${repo} 里 ${selIss.id} 的 impl-log、评审报告与票文件，用大白话讲这张票现在到哪一步、有没有卡住、下一步是什么`
            : `读 ${repo} 里需求「${f.slug}」的全部在制票 impl-log 与票文件，讲各票进度与卡点`
          navigator.clipboard?.writeText(text).catch(() => {})
        }
        const acts = []
        if (selIss) {
          acts.push(h('button', { key: 'd', className: 'cpp-drill', onClick: drill, title: '复制下钻提问，粘到会话即可' }, '复制下钻提问'))
          acts.push(h('button', { key: 'c', className: 'cpp-drill cpp-cleanup-detail',
            onClick: () => onCleanup({ branch: selIss.branch || null, issue: selIss.key }),
            title: '按开发收尾规则预检；是否可执行以预检为准' }, '清理'))
          if (selIss.status !== 'done') {
            acts.push(h('button', { key: 'x', className: 'cpp-drill',
              onClick: () => onCloseIssue({ key: selIss.key, title: selIss.title, status: selIss.status }),
              title: '仅把需求状态改为 deferred，不删除分支/worktree/dev' }, '关闭需求'))
          }
        }
        detail = [
          h('div', { key: 't', className: 'cpp-dtitle' }, selIss ? `${selIss.id} · ${selIss.title}` : `${f.slug} · 已选择整个需求`),
          h('div', { key: 's', className: 'cpp-dline' }, selIss
            ? `${selIss.feature} · ${SW_STATUS[selIss.status] || selIss.status}`
            : `${f.module} · 本需求全部 issue 已展开并高亮`),
          selIss ? h('div', { key: 'p', className: 'cpp-dline' }, selIss.issue_path) : null,
          h('div', { key: 'c', className: 'sw-counts' },
            h('span', null, h('b', null, (f.issues || []).length), ' 本需求 issue'),
            h('span', null, h('b', null, relCount), ' 跨需求直接关联'),
            h('span', null, h('b', null, selEdges), ' 高亮箭头')),
          acts.length ? h('div', { key: 'a', className: 'cpp-detail-actions' }, acts) : null]
      }
      const arrow = (id, fill) => h('marker', { key: id, id, viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 5, markerHeight: 5, orient: 'auto' },
        h('path', { d: 'M0 0L8 4L0 8Z', fill }))
      return h('div', null,
        h(SwimSummary, { repo, refreshKey: summaryReload, onRefresh: () => setSummaryReload((n) => n + 1) }),
        h('div', { className: 'sw-toolbar' },
          h('span', { className: 'sw-label' }, 'Agent 关联'),
          [['all', '全部'], ['claude', 'Claude'], ['codex', 'Codex'], ['zcode', 'ZCode'], ['unassigned', '未绑定']].map(([k, label]) =>
            h('button', { key: k, className: 'sw-btn' + (filter === k ? ' is-on' : ''),
              'aria-pressed': String(filter === k),
              onClick: () => { setFilter(k); setExtra(new Set()) } }, label)),
          h('span', { style: { flex: 1 } }),
          h('button', { className: 'sw-btn' + (showAll ? ' is-on' : ''), 'aria-pressed': String(showAll),
            onClick: () => setShowAll((v) => !v) }, showAll ? '收起非开发中依赖' : `显示非开发中依赖 (${ctxCount})`),
          h('button', { className: 'sw-btn', onClick: clearSel }, '清除选择'),
          h('button', { className: 'sw-btn', 'aria-label': '刷新', title: '刷新', onClick: onRefresh }, '↻')),
        h('div', { className: 'sw-legend' },
          h('span', null, h('i', { className: 'sw-nsample' }), '所选需求全部 issue'),
          h('span', null, h('i', { className: 'sw-nsample rel' }), '其他需求的直接关联 issue'),
          h('span', null, h('i', { className: 'sw-lsample' }), '联动高亮箭头'),
          h('span', null, h('i', { className: 'sw-lsample miss' }), '目标缺失')),
        h('div', { className: 'sw-viewport', ref: viewportRef },
          h('div', { className: 'sw-canvas', style: { width: width + 'px', height: scene.height + 'px' } },
            laneEls,
            h('svg', { className: 'sw-edges', width, height: scene.height, 'aria-hidden': true },
              h('defs', null,
                arrow('sw-arrow-n', 'rgba(154,160,166,.6)'),
                arrow('sw-arrow-a', '#4f8ff7'),
                arrow('sw-arrow-m', '#f2585e')),
              edgeEls),
            nodeEls)),
        h('div', { className: 'sw-detail', 'aria-live': 'polite' }, detail))
    }

    function Tree({ data, openMap, foldMap, onToggle, onFold, onCleanup, onRefresh, focusKey, onCloseIssue }) {
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
          rows.push(h(FeatureRow, { key: nodeKey(n), n: stall, kids, folded: !!foldMap[nodeKey(n)], onFold: () => onFold(nodeKey(n)), focused: focusKey === nodeKey(n) }))
        } else if (n.kind === 'ticket') {
          rows.push(h(TicketRow, { key: nodeKey(n), n: stall, open: !!openMap[n.key], onToggle: () => onToggle(n.key), onCleanup, onCloseIssue, focused: focusKey === nodeKey(n) }))
        } else {
          const kids = hasKids(list, i)
          rows.push(h(BranchRow, { key: nodeKey(n), n: stall, kids, folded: !!foldMap[nodeKey(n)], onFold: () => onFold(nodeKey(n)), onCleanup, focused: focusKey === nodeKey(n) }))
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

    function focusTarget(data, currentSession) {
      if (!currentSession?.id || !data?.tree) return null
      const binding = (data.sessions || []).find((b) =>
        b.session_id === currentSession.id || String(b.session_id).endsWith(':' + currentSession.id))
      const title = binding?.title || currentSession.title || ''
      const issue = (binding?.issue || title.match(/issue-\d+[a-z0-9]*/i)?.[0] || '').toLowerCase()
      const feature = binding?.feature || ''
      const matchesFeature = (n) => feature && (
        n.feature === feature || n.slug === feature ||
        (n.feature || '').endsWith('/' + feature) || (n.slug || '').endsWith('/' + feature))
      if (issue) {
        const tickets = data.tree.filter((n) => n.kind === 'ticket' && n.id === issue)
        const ticket = tickets.find(matchesFeature) || tickets[0]
        if (ticket) return nodeKey(ticket)
        const branches = data.tree.filter((n) => n.kind === 'branch' && n.iid === issue)
        const branch = branches.find(matchesFeature) || branches[0]
        if (branch) return nodeKey(branch)
      }
      const feat = data.tree.find((n) => n.kind === 'feature' && (
        matchesFeature(n) || (title && n.slug && title.includes(n.slug))))
      return feat ? nodeKey(feat) : null
    }

    function Panel({ repo, currentSession }) {
      const [data, setData] = React.useState(null)
      const [error, setError] = React.useState(null)
      const [openMap, setOpenMap] = React.useState({})
      const [foldMap, setFoldMap] = React.useState({})
      const [reload, setReload] = React.useState(0)
      const [cleanup, setCleanup] = React.useState(null)
      const [closeRequest, setCloseRequest] = React.useState(null)
      const [view, setView] = React.useState('tree')
      const [swim, setSwim] = React.useState(null)
      const [swimError, setSwimError] = React.useState(null)

      React.useEffect(() => {
        let alive = true
        setData(null)
        setError(null)
        const load = () => jget('/payload?repo=' + encodeURIComponent(repo))
          .then((d) => { if (alive) { setData(d); setError(null) } })
          .catch((e) => { if (alive) setError(String(e.message ?? e)) })
        load()
        const t = setInterval(load, 10000)
        return () => { alive = false; clearInterval(t) }
      }, [reload, repo])

      React.useEffect(() => {
        if (view !== 'swim') return
        let alive = true
        setSwim(null)
        setSwimError(null)
        const load = () => jget('/swim?repo=' + encodeURIComponent(repo))
          .then((d) => { if (alive) { setSwim(d); setSwimError(null) } })
          .catch((e) => { if (alive) setSwimError(String(e.message ?? e)) })
        load()
        const t = setInterval(load, 10000)
        return () => { alive = false; clearInterval(t) }
      }, [view, reload, repo])

      const focusKey = React.useMemo(() => focusTarget(data, currentSession), [data, currentSession])
      const dataReady = !!data
      React.useEffect(() => {
        if (!focusKey || !data?.tree) return
        const list = data.tree
        const index = list.findIndex((n) => nodeKey(n) === focusKey)
        if (index < 0) return
        const stack = []
        for (let i = 0; i < index; i++) {
          const depth = depthOf(list[i])
          stack[depth] = list[i]
          stack.length = depth + 1
        }
        const ancestors = stack.slice(0, depthOf(list[index]))
        if (ancestors.length) {
          setFoldMap((m) => {
            const next = { ...m }
            ancestors.forEach((n) => { next[nodeKey(n)] = false })
            return next
          })
        }
        const timer = setTimeout(() => {
          const escaped = window.CSS?.escape ? CSS.escape(focusKey) : focusKey.replace(/"/g, '\"')
          document.querySelector(`[data-node-key="${escaped}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
        }, 100)
        return () => clearTimeout(timer)
      }, [focusKey, dataReady])

      const beginCleanup = (target) => {
        const request = typeof target === 'string'
          ? { repo, branch: target }
          : target.branch ? { repo, branch: target.branch } : { repo, issue: target.issue }
        const label = request.branch || request.issue
        setCleanup({ branch: label, target, request, phase: 'preview' })
        jpost('/cleanup/preview', request)
          .then((plan) => {
            if (!plan.eligible) {
              setCleanup((s) => s?.request === request ? { ...s, phase: 'error', error: plan.reason || plan.error || '该节点不可清理' } : s)
            } else {
              setCleanup((s) => s?.request === request ? { ...s, phase: 'confirm', plan } : s)
            }
          })
          .catch((e) => setCleanup((s) => s?.request === request ? { ...s, phase: 'error', error: String(e.message ?? e) } : s))
      }
      const beginCloseIssue = (n) => {
        setCloseRequest({
          issue: n.key, title: n.title, status: n.status,
          reason: '中途搁置，用户确认关闭', phase: 'confirm',
        })
      }
      const applyCloseIssue = () => {
        if (!closeRequest) return
        const request = closeRequest
        setCloseRequest((s) => ({ ...s, phase: 'apply' }))
        jpost('/close', { repo, issue: request.issue, reason: request.reason }, 'close')
          .then((result) => {
            setCloseRequest((s) => s?.issue === request.issue ? { ...s, phase: 'result', result } : s)
            setReload((n) => n + 1)
          })
          .catch((e) => {
            setCloseRequest((s) => s?.issue === request.issue ? { ...s, phase: 'result', result: { ok: false, error: String(e.message ?? e) } } : s)
            setReload((n) => n + 1)
          })
      }

      const applyCleanup = (action) => {
        if (!cleanup?.plan) return
        const { request, plan } = cleanup
        setCleanup((s) => ({ ...s, phase: 'apply', action }))
        jpost('/cleanup/apply', { ...request, confirm: plan.confirm, action }, true)
          .then((result) => {
            setCleanup((s) => s?.request === request ? { ...s, phase: 'result', result } : s)
            setReload((n) => n + 1)
          })
          .catch((e) => {
            setCleanup((s) => s?.request === request ? { ...s, phase: 'result', result: { ok: false, error: String(e.message ?? e) } } : s)
            setReload((n) => n + 1)
          })
      }

      return h('div', { 'data-cpp': true },
        h('style', null, CSS),
        h('div', { className: 'cpp-body' },
          h('div', { className: 'cpp-viewtabs', role: 'tablist', 'aria-label': '进度视图' },
            h('button', { role: 'tab', 'aria-selected': view === 'tree',
              className: 'cpp-viewtab' + (view === 'tree' ? ' is-on' : ''),
              onClick: () => setView('tree') }, '分支树'),
            h('button', { role: 'tab', 'aria-selected': view === 'swim',
              className: 'cpp-viewtab' + (view === 'swim' ? ' is-on' : ''),
              onClick: () => setView('swim') }, '开发中')),
          view === 'tree'
            ? (error
                ? h('div', { className: 'cpp-err' }, '数据源不可达：' + error + '\n（当前工作区未登记或服务异常）')
                : data
                  ? h(Tree, {
                      data, openMap, foldMap, focusKey,
                      onToggle: (k) => setOpenMap((m) => ({ ...m, [k]: !m[k] })),
                      onFold: (k) => setFoldMap((m) => ({ ...m, [k]: !m[k] })),
                      onCleanup: beginCleanup,
                      onCloseIssue: beginCloseIssue,
                      onRefresh: () => setReload((n) => n + 1),
                    })
                  : h('div', { className: 'cpp-loading' }, '读取中…'))
            : (swimError
                ? h('div', { className: 'cpp-err' }, '数据源不可达：' + swimError + '\n（当前工作区未登记或服务异常）')
                : swim
                  ? h(SwimView, {
                      data: swim, repo,
                      onCleanup: beginCleanup,
                      onCloseIssue: beginCloseIssue,
                      onRefresh: () => setReload((n) => n + 1),
                    })
                  : h('div', { className: 'cpp-loading' }, '读取中…'))),
        cleanup ? h(CleanupModal, {
          state: cleanup,
          onClose: () => { if (cleanup.phase !== 'apply') setCleanup(null) },
          onApply: applyCleanup,
          onRetry: () => beginCleanup(cleanup.target),
        }) : null,
        closeRequest ? h(CloseIssueModal, {
          state: closeRequest,
          onClose: () => { if (closeRequest.phase !== 'apply') setCloseRequest(null) },
          onApply: applyCloseIssue,
          onReason: (reason) => setCloseRequest((s) => ({ ...s, reason })),
        }) : null)
    }

    function WorkspacePanel({ readWorkspace }) {
      const [current, setCurrent] = React.useState(() => readWorkspace())
      const signature = React.useRef(JSON.stringify(current))
      React.useEffect(() => {
        const timer = setInterval(() => {
          const next = readWorkspace()
          const sig = JSON.stringify(next)
          if (sig !== signature.current) {
            signature.current = sig
            setCurrent(next)
          }
        }, 400)
        return () => clearInterval(timer)
      }, [readWorkspace])
      if (current.pending) return h('div', { className: 'cpp-empty' }, '读取工作区…')
      if (!current.workspace) return h('div', { className: 'cpp-empty' }, '未选择工作区')
      return h(Panel, {
        key: current.workspace.path,
        repo: current.workspace.path,
        currentSession: current.session,
      })
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
      inject: ['slots', 'sidebarRightTabs', 'sidebarRight', 'uiWorkspace', 'workspaces', 'sessions'],
      apply(ctx) {
        const style = document.createElement('style')
        style.setAttribute('data-plugin-css', '@ch-agent/dsh-progress-panel')
        style.textContent = CSS
        document.head.appendChild(style)
        ctx.effect(() => () => style.remove(), 'ch-progress-panel: 样式')

        const slots = (typeof ctx.get === 'function' ? ctx.get('slots') : undefined) || ctx.slots
        const KEY = 'ch-progress'
        const readWorkspace = () => {
          const selection = ctx.uiWorkspace.selection.getSnapshot()
          const sessionId = selection?.sessionId
          if (!sessionId) return { workspace: null, session: null }
          const session = ctx.sessions.list.getSnapshot().byId?.[sessionId]
          const list = ctx.workspaces.list.getSnapshot()
          const workspace = (list.items || []).find((w) => w.sessionIds?.includes(sessionId))
          if (!workspace) return { workspace: null, session: null, pending: list.phase !== 'ready' }
          return {
            workspace: { id: workspace.workspaceId, path: workspace.path, title: workspace.title },
            session: { id: sessionId, title: session?.title || session?.customTitle || '' },
          }
        }
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
          () => h(WorkspacePanel, { readWorkspace }),
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
