/**
 * ch-progress-panel — read-only progress plus authorized close/discard cleanup.
 * GET  /api/ch-progress/payload?repo=<registered>
 * GET  /api/ch-progress/swim?repo=<registered>
 * POST /api/ch-progress/close-clean/preview {repo, issue|feature_dir, intent}
 * POST /api/ch-progress/close-clean/apply   {..., operation_id, confirm} + x-ch-progress-action: apply
 * POST /api/ch-progress/reconcile          {repo, operation_id?}
 * POST /api/ch-progress/reconcile/apply    {repo, operation_id} + x-ch-progress-action: apply
 * Legacy close/cleanup/merge routes intentionally return 404. No route merges code.
 */
import { spawn } from 'node:child_process'
import { realpathSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'ch-progress-panel'
export const inject = ['webServer']

const DEFAULT_SCRIPT = fileURLToPath(new URL('../scripts/progress.py', import.meta.url))

export function apply(ctx, config) {
  const fallbackRepo = (config && config.repo) || process.env.CH_PROGRESS_REPO || process.cwd()
  const summaryCfg = (config && config.summary) || {}
  const cfg = {
    repo: fallbackRepo,
    repos: [...new Set([fallbackRepo, ...((config && Array.isArray(config.repos) ? config.repos : []) || [])])],
    script: (config && config.script) || DEFAULT_SCRIPT,
    stallMin: (config && config.stallMin) || 30,
    summaryBase: summaryCfg.baseUrl || null,
    summaryModel: summaryCfg.model || null,
    summaryKey: summaryCfg.apiKey || null,
  }
  const log = (m) => { try { ctx.logger?.info?.(`[ch-progress-panel] ${m}`) } catch { /* non-fatal */ } }
  const canonical = (p) => { try { return realpathSync(p) } catch { return resolvePath(p) } }
  const allowedRepos = new Set(cfg.repos.map(canonical))
  const resolveRepo = (input) => {
    const repo = canonical(input || cfg.repo)
    return allowedRepos.has(repo) ? repo : null
  }
  const cache = new Map()
  let operationRunning = false
  let operationNeedsReconcile = false
  let activeOperation = null
  const operationTimeoutMs = Number(config?.operationTimeoutMs || 180000)
  const processKillGraceMs = Number(config?.processKillGraceMs || 1000)
  const processExitWaitMs = Number(config?.processExitWaitMs || 5000)
  const occupiedGroups = new Set()
  const groupAlive = (pid) => {
    try { process.kill(-pid, 0); return true } catch (e) { return e?.code !== 'ESRCH' }
  }
  const probeOccupiedGroups = () => {
    for (const pid of occupiedGroups) {
      if (!groupAlive(pid)) occupiedGroups.delete(pid)
    }
    if (!occupiedGroups.size && activeOperation?.unknown) {
      activeOperation = null
      operationRunning = false
    }
  }
  const invalidate = (repo) => { cache.delete(repo); cache.delete('swim:' + repo) }

  function runProgress(repo, args) {
    return new Promise((resolve) => {
      if (process.platform === 'win32') {
        return resolve({ code: -1, out: '', err: '清理/恢复仅支持 POSIX（macOS/Linux）；Windows 不支持，未执行' })
      }
      // Node's built-in timeout kills only the direct child. A detached POSIX
      // child instead becomes a process-group leader so the whole tree is gated.
      const child = spawn('python3', [cfg.script, ...args], { cwd: repo, detached: true })
      let out = ''
      let err = ''
      let childCode = null
      let settled = false
      let timedOut = false
      let timeout = null
      let killTimer = null
      let waitTimer = null
      const killGroup = (signal) => {
        if (!child.pid) return
        try { process.kill(-child.pid, signal) } catch { /* ESRCH means already gone */ }
      }
      const clearTimers = () => {
        if (timeout) clearTimeout(timeout)
        if (killTimer) clearTimeout(killTimer)
        if (waitTimer) clearTimeout(waitTimer)
      }
      const finish = (unknown = false, groupGone = true) => {
        if (settled) return
        settled = true
        clearTimers()
        if (unknown && !groupGone && child.pid) occupiedGroups.add(child.pid)
        resolve({ code: childCode ?? -1, out, err, unknown, groupGone, groupId: child.pid })
      }
      const waitForGroup = (deadline) => {
        if (!groupAlive(child.pid)) return finish(timedOut, true)
        if (Date.now() >= deadline) {
          err += '\n进程树退出状态无法证明；保持占用并要求对账'
          return finish(true, false)
        }
        waitTimer = setTimeout(() => waitForGroup(deadline), 25)
      }
      child.stdout.on('data', (d) => { out += d })
      child.stderr.on('data', (d) => { err += d })
      child.on('error', (e) => { err += String(e); finish(false, true) })
      child.on('close', (code) => {
        childCode = code
        waitForGroup(Date.now() + processExitWaitMs)
      })
      timeout = setTimeout(() => {
        timedOut = true
        err += '\n操作超时，正在终止整个进程组'
        killGroup('SIGTERM')
        killTimer = setTimeout(() => killGroup('SIGKILL'), processKillGraceMs)
        if (childCode !== null) waitForGroup(Date.now() + processExitWaitMs)
      }, operationTimeoutMs)
    })
  }

  function parseResult(r) {
    try { return JSON.parse(r.out) } catch {
      return { ok: false, error: (r.err || r.out || `progress.py exit ${r.code}`).slice(0, 500) }
    }
  }

  async function payload(repo) {
    const now = Date.now()
    const cached = cache.get(repo)
    if (cached && now - cached.at < 20000) return cached.data
    const r = await runProgress(repo, ['payload', '--repo', repo, '--stall-min', String(cfg.stallMin)])
    if (r.code !== 0) return { ok: false, error: (r.err || `progress.py exit ${r.code}`).slice(0, 400) }
    try {
      const data = JSON.parse(r.out)
      cache.set(repo, { at: now, data })
      return data
    } catch (e) { return { ok: false, error: 'payload JSON 解析失败：' + e.message } }
  }

  async function swim(repo) {
    const now = Date.now()
    const key = 'swim:' + repo
    const cached = cache.get(key)
    if (cached && now - cached.at < 20000) return cached.data
    const r = await runProgress(repo, ['swim', '--repo', repo])
    if (r.code !== 0) return { ok: false, error: (r.err || `progress.py exit ${r.code}`).slice(0, 400) }
    try {
      const data = JSON.parse(r.out)
      cache.set(key, { at: now, data })
      return data
    } catch (e) { return { ok: false, error: 'swim JSON 解析失败：' + e.message } }
  }

  async function summary(repo, force) {
    const now = Date.now()
    const key = 'sum:' + repo
    const cached = cache.get(key)
    if (!force && cached && now - cached.at < 300000) return cached.data
    const args = ['summary', '--repo', repo]
    if (cfg.summaryBase) args.push('--base-url', cfg.summaryBase)
    if (cfg.summaryModel) args.push('--model', cfg.summaryModel)
    const env = cfg.summaryKey ? { ...process.env, CH_PROGRESS_SUMMARY_API_KEY: cfg.summaryKey } : process.env
    const r = await new Promise((resolve) => {
      const p = spawn('python3', [cfg.script, ...args], { cwd: repo, timeout: 180000, killSignal: 'SIGTERM', env })
      let out = ''
      let err = ''
      p.stdout.on('data', (d) => { out += d })
      p.stderr.on('data', (d) => { err += d })
      p.on('error', (e) => resolve({ code: -1, out, err: String(e) }))
      p.on('close', (code) => resolve({ code, out, err }))
    })
    let data
    try { data = JSON.parse(r.out) } catch { data = { ok: false, error: (r.err || `progress.py exit ${r.code}`).slice(0, 400) } }
    if (data && data.ok) cache.set(key, { at: now, data })
    return data
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      let body = ''
      req.on('data', (chunk) => {
        body += chunk
        if (body.length > 16384) { reject(new Error('请求体过大')); req.destroy() }
      })
      req.on('end', () => { try { resolve(JSON.parse(body || '{}')) } catch { reject(new Error('请求不是有效 JSON')) } })
      req.on('error', reject)
    })
  }
  const sendJson = (res, code, obj) => {
    res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(obj))
  }
  const statusFor = (result) => {
    if (result?.protected || result?.code === 'protected') return 403
    if (result?.stale || ['stale', 'conflict', 'reconcile-required'].includes(result?.code)) return 409
    return 200
  }

  function targetArgs(body) {
    const issue = typeof body.issue === 'string' ? body.issue : ''
    const featureDir = typeof (body.feature_dir ?? body.featureDir) === 'string' ? (body.feature_dir ?? body.featureDir) : ''
    if ((!issue && !featureDir) || (issue && featureDir)) throw Object.assign(new Error('issue 与 feature_dir 必须且只能提供一个'), { status: 400 })
    if (issue.length > 300 || featureDir.length > 1000 || issue.includes('\0') || featureDir.includes('\0')) {
      throw Object.assign(new Error('清理对象参数无效'), { status: 400 })
    }
    const args = []
    if (issue) args.push('--issue', issue)
    else args.push('--feature-dir', featureDir)
    return args
  }

  async function closeClean(req, res, mode) {
    probeOccupiedGroups()
    if (mode === 'apply' && (operationRunning || operationNeedsReconcile)) {
      return sendJson(res, 409, { ok: false, code: 'reconcile-required', occupied: true,
        error: operationNeedsReconcile ? '上次清理结果不明，保持占用；请先对账，不能第二次 apply' : '另一项清理/恢复正在执行' })
    }
    if (!String(req.headers['content-type'] || '').startsWith('application/json')) return sendJson(res, 415, { ok: false, error: '只接受 application/json' })
    if (mode === 'apply' && req.headers['x-ch-progress-action'] !== 'apply') return sendJson(res, 403, { ok: false, error: '缺少清理动作确认头' })
    let body
    try { body = await readBody(req) } catch (e) { return sendJson(res, 400, { ok: false, error: e.message }) }
    const repo = resolveRepo(body.repo)
    if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
    const intent = body.intent ?? 'close'
    if (!['close', 'discard'].includes(intent)) return sendJson(res, 400, { ok: false, error: 'intent 必须是 close/discard' })
    let args
    try { args = ['close-and-clean', '--repo', repo, ...targetArgs(body), '--intent', intent] }
    catch (e) { return sendJson(res, e.status || 400, { ok: false, error: e.message }) }
    if (mode === 'apply') {
      const operationId = body.operation_id ?? body.operationId
      if (typeof operationId !== 'string' || !operationId || typeof body.confirm !== 'string' || !/^[a-f0-9]{64}$/.test(body.confirm)) {
        return sendJson(res, 400, { ok: false, error: 'apply 需要 operation_id 和有效 confirm' })
      }
      args.push('--apply', '--operation-id', operationId, '--confirm', body.confirm)
      // readBody() yields to other requests. Recheck immediately before claiming
      // the operation so two concurrent applies cannot both pass the first gate.
      probeOccupiedGroups()
      if (operationRunning || operationNeedsReconcile) {
        return sendJson(res, 409, { ok: false, code: operationNeedsReconcile ? 'reconcile-required' : 'conflict',
          occupied: true, error: operationNeedsReconcile ? '上次清理结果不明；请先对账' : '当前 operation 正在执行' })
      }
      activeOperation = { operationId, kind: 'close-clean', unknown: false, groupId: null }
      operationRunning = true
    }
    let raw
    let operationToken = activeOperation
    try {
      raw = await runProgress(repo, args)
      const result = raw.unknown
        ? { ok: false, code: 'reconcile-required', occupied: true, state: 'needs-attention',
            error: (raw.err || '清理结果不明；保持占用并要求对账').slice(-800) }
        : parseResult(raw)
      if (raw.unknown) {
        operationNeedsReconcile = true
        if (operationToken && raw.groupGone === false) {
          operationToken.unknown = true
          operationToken.groupId = raw.groupId
        }
      }
      if (mode === 'apply') { invalidate(repo); log(`close-clean：${JSON.stringify(result).slice(0, 350)}`) }
      return sendJson(res, statusFor(result), result)
    } finally {
      if (mode === 'apply' && activeOperation === operationToken && (!raw || raw.groupGone !== false)) {
        activeOperation = null
        operationRunning = false
      }
    }
  }

  async function reconcile(req, res, shouldApply) {
    probeOccupiedGroups()
    if (shouldApply && operationRunning) return sendJson(res, 409, { ok: false, code: 'conflict', error: '进程树仍在运行，暂不能恢复' })
    if (!String(req.headers['content-type'] || '').startsWith('application/json')) return sendJson(res, 415, { ok: false, error: '只接受 application/json' })
    if (shouldApply && req.headers['x-ch-progress-action'] !== 'apply') return sendJson(res, 403, { ok: false, error: '缺少恢复动作确认头' })
    let body
    try { body = await readBody(req) } catch (e) { return sendJson(res, 400, { ok: false, error: e.message }) }
    const repo = resolveRepo(body.repo)
    if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
    const operationId = body.operation_id ?? body.operationId
    if (shouldApply && (typeof operationId !== 'string' || !operationId)) return sendJson(res, 400, { ok: false, error: '恢复需要 operation_id' })
    const args = ['reconcile', '--repo', repo]
    if (operationId) args.push('--operation-id', operationId)
    if (shouldApply) args.push('--apply', '--kind', 'cleanup')
    if (shouldApply) {
      probeOccupiedGroups()
      if (operationRunning) return sendJson(res, 409, { ok: false, code: 'conflict', error: '当前 operation/进程树仍在运行' })
      activeOperation = { operationId, kind: 'reconcile', unknown: false, groupId: null }
      operationRunning = true
    }
    let raw
    let operationToken = activeOperation
    try {
      raw = await runProgress(repo, args)
      const result = raw.unknown
        ? { ok: false, code: 'reconcile-required', occupied: true, state: 'needs-attention', error: raw.err || '恢复结果不明' }
        : parseResult(raw)
      if (raw.unknown) {
        operationNeedsReconcile = true
        if (operationToken && raw.groupGone === false) {
          operationToken.unknown = true
          operationToken.groupId = raw.groupId
        }
      } else if (shouldApply && result.ok) operationNeedsReconcile = false
      if (shouldApply) invalidate(repo)
      return sendJson(res, statusFor(result), result)
    } finally {
      if (shouldApply && activeOperation === operationToken && (!raw || raw.groupGone !== false)) {
        activeOperation = null
        operationRunning = false
      }
    }
  }

  try {
    ctx.effect(() => ctx.webServer.register({
      kind: 'prefix', path: '/api/ch-progress',
      handler: async (req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1')
        try {
          if (req.method === 'GET' && url.pathname === '/api/ch-progress/payload') {
            const repo = resolveRepo(url.searchParams.get('repo'))
            if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
            const data = await payload(repo)
            return sendJson(res, data && data.ok === false ? 500 : 200, data)
          }
          if (req.method === 'GET' && url.pathname === '/api/ch-progress/swim') {
            const repo = resolveRepo(url.searchParams.get('repo'))
            if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
            const data = await swim(repo)
            return sendJson(res, data && data.ok === false ? 500 : 200, data)
          }
          if (req.method === 'GET' && url.pathname === '/api/ch-progress/summary') {
            const repo = resolveRepo(url.searchParams.get('repo'))
            if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
            const data = await summary(repo, url.searchParams.get('force') === '1')
            return sendJson(res, data && data.ok === false ? 502 : 200, data)
          }
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/close-clean/preview') return await closeClean(req, res, 'preview')
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/close-clean/apply') return await closeClean(req, res, 'apply')
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/reconcile') return await reconcile(req, res, false)
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/reconcile/apply') return await reconcile(req, res, true)
          return sendJson(res, 404, { ok: false, error: `unknown ${req.method} ${url.pathname}` })
        } catch (e) { return sendJson(res, 500, { ok: false, error: e?.message ?? String(e) }) }
      },
    }), 'ch-progress-panel: /api/ch-progress 路由')
    log(`数据源就绪：${[...allowedRepos].join(', ')}`)
  } catch (e) { log(`HTTP 路由没挂上：${e?.message ?? e}`) }
}
