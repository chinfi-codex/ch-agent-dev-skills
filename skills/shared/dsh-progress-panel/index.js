/**
 * ch-progress-panel —— 服务端半端：按当前工作区提供 payload 与收尾清场。
 * GET  /api/ch-progress/payload?repo=<已登记仓库>
 * POST /api/ch-progress/close          { repo, issue, reason } + x-ch-progress-action: close
 * POST /api/ch-progress/cleanup/preview  { repo, branch | issue }
 * POST /api/ch-progress/cleanup/apply    { repo, branch | issue, confirm } + x-ch-progress-action: apply
 *
 * repo 仅接受 profile config.repo / config.repos 中登记的根目录，拒绝客户端任意路径。
 * 清理支持 done 收尾、中途搁置 discard / merge，以及无分支 issue-only dev 清理。
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
  const cfg = {
    repo: fallbackRepo,
    repos: [...new Set([fallbackRepo, ...((config && Array.isArray(config.repos) ? config.repos : []) || [])])],
    script: (config && config.script) || DEFAULT_SCRIPT,
    stallMin: (config && config.stallMin) || 30,
  }
  const log = (m) => { try { ctx.logger?.info?.(`[ch-progress-panel] ${m}`) } catch { /* 日志失败不致命 */ } }
  const canonical = (p) => { try { return realpathSync(p) } catch { return resolvePath(p) } }
  const allowedRepos = new Set(cfg.repos.map(canonical))
  const resolveRepo = (input) => {
    const repo = canonical(input || cfg.repo)
    return allowedRepos.has(repo) ? repo : null
  }

  const cache = new Map()  // repo -> { at, data }
  let cleanupRunning = false

  function runProgress(repo, args) {
    return new Promise((resolve) => {
      const p = spawn('python3', [cfg.script, ...args], {
        cwd: repo, timeout: 180000, killSignal: 'SIGTERM',
      })
      let out = ''
      let err = ''
      p.stdout.on('data', (d) => { out += d })
      p.stderr.on('data', (d) => { err += d })
      p.on('error', (e) => resolve({ code: -1, out, err: String(e) }))
      p.on('close', (code) => resolve({ code, out, err }))
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
    if (r.code !== 0) {
      log(`payload 失败：${(r.err || `exit ${r.code}`).slice(0, 300)}`)
      return { ok: false, error: (r.err || `progress.py exit ${r.code}`).slice(0, 400) }
    }
    try {
      const data = JSON.parse(r.out)
      cache.set(repo, { at: now, data })
      return data
    } catch (e) {
      return { ok: false, error: 'payload JSON 解析失败：' + e.message }
    }
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      let body = ''
      req.on('data', (chunk) => {
        body += chunk
        if (body.length > 16384) {
          reject(new Error('请求体过大'))
          req.destroy()
        }
      })
      req.on('end', () => {
        try { resolve(JSON.parse(body || '{}')) } catch { reject(new Error('请求不是有效 JSON')) }
      })
      req.on('error', reject)
    })
  }

  const sendJson = (res, code, obj) => {
    res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(obj))
  }

  async function closeIssue(req, res) {
    if (cleanupRunning) return sendJson(res, 409, { ok: false, error: '另一项清理正在执行，请稍候' })
    if (!String(req.headers['content-type'] || '').startsWith('application/json')) {
      return sendJson(res, 415, { ok: false, error: '关闭需求只接受 application/json' })
    }
    if (req.headers['x-ch-progress-action'] !== 'close') {
      return sendJson(res, 403, { ok: false, error: '缺少关闭需求动作确认头' })
    }
    let body
    try { body = await readBody(req) } catch (e) { return sendJson(res, 400, { ok: false, error: e.message }) }
    const repo = resolveRepo(body.repo)
    if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
    const issue = typeof body.issue === 'string' ? body.issue : ''
    if (!issue || issue.length > 300) return sendJson(res, 400, { ok: false, error: 'issue 参数无效' })
    const reason = typeof body.reason === 'string' ? body.reason.slice(0, 400) : ''
    cleanupRunning = true
    try {
      const result = parseResult(await runProgress(repo, ['close', '--repo', repo, '--issue', issue, '--reason', reason]))
      if (result.ok) cache.delete(repo)
      return sendJson(res, 200, result)
    } finally {
      cleanupRunning = false
    }
  }

  async function cleanup(req, res, mode) {
    if (cleanupRunning) return sendJson(res, 409, { ok: false, error: '另一项清理正在执行，请稍候' })
    if (!String(req.headers['content-type'] || '').startsWith('application/json')) {
      return sendJson(res, 415, { ok: false, error: '清理接口只接受 application/json' })
    }
    if (mode === 'apply' && req.headers['x-ch-progress-action'] !== 'apply') {
      return sendJson(res, 403, { ok: false, error: '缺少清理动作确认头' })
    }
    let body
    try { body = await readBody(req) } catch (e) { return sendJson(res, 400, { ok: false, error: e.message }) }
    const repo = resolveRepo(body.repo)
    if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
    const branch = typeof body.branch === 'string' ? body.branch : ''
    const issue = typeof body.issue === 'string' ? body.issue : ''
    if ((!branch && !issue) || (branch && issue)) {
      return sendJson(res, 400, { ok: false, error: 'branch 与 issue 必须且只能提供一个' })
    }
    if ((branch && (branch.length > 300 || branch.includes('\0'))) || (issue && issue.length > 300)) {
      return sendJson(res, 400, { ok: false, error: '清理对象参数无效' })
    }
    cleanupRunning = true
    try {
      const args = ['cleanup', '--repo', repo]
      if (branch) args.push('--branch', branch)
      else args.push('--issue', issue)
      if (mode === 'apply') {
        if (typeof body.confirm !== 'string' || !/^[a-f0-9]{64}$/.test(body.confirm)) {
          return sendJson(res, 400, { ok: false, error: '缺少有效的预检确认指纹' })
        }
        if (body.action !== undefined && !['close', 'discard', 'merge', 'dev-only'].includes(body.action)) {
          return sendJson(res, 400, { ok: false, error: 'action 必须是 close / discard / merge / dev-only' })
        }
        args.push('--apply', '--confirm', body.confirm)
        if (body.action) args.push('--action', body.action)
      }
      const result = parseResult(await runProgress(repo, args))
      if (mode === 'apply') {
        cache.delete(repo)
        log(`清理 ${result.branch || branch || issue}：${JSON.stringify(result).slice(0, 350)}`)
      }
      return sendJson(res, 200, result)
    } finally {
      cleanupRunning = false
    }
  }

  try {
    ctx.effect(() => ctx.webServer.register({
      kind: 'prefix',
      path: '/api/ch-progress',
      handler: async (req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1')
        try {
          if (req.method === 'GET' && url.pathname === '/api/ch-progress/payload') {
            const repo = resolveRepo(url.searchParams.get('repo'))
            if (!repo) return sendJson(res, 403, { ok: false, error: '当前工作区未登记为进度数据源' })
            const data = await payload(repo)
            return sendJson(res, data && data.ok === false ? 500 : 200, data)
          }
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/close') {
            return await closeIssue(req, res)
          }
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/cleanup/preview') {
            return await cleanup(req, res, 'preview')
          }
          if (req.method === 'POST' && url.pathname === '/api/ch-progress/cleanup/apply') {
            return await cleanup(req, res, 'apply')
          }
          return sendJson(res, 404, { ok: false, error: `unknown ${req.method} ${url.pathname}` })
        } catch (e) {
          return sendJson(res, 500, { ok: false, error: e?.message ?? String(e) })
        }
      },
    }), 'ch-progress-panel: /api/ch-progress 路由')
    log(`数据源就绪：${[...allowedRepos].join(', ')}`)
  } catch (e) {
    log(`HTTP 路由没挂上：${e?.message ?? e}`)
  }
}
