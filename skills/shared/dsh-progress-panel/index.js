/**
 * ch-progress-panel —— 服务端半端：payload + 收尾清场 API。
 * GET  /api/ch-progress/payload
 * POST /api/ch-progress/cleanup/preview  { branch }
 * POST /api/ch-progress/cleanup/apply    { branch, confirm } + x-ch-progress-action: apply
 *
 * 清场规则由 ../scripts/progress_cleanup.py 执行：done 票限定、确认指纹重验、
 * dev 软链不跟随、salvage 前置、合并目标判定、dev 过程文件保留引用。
 */
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const name = 'ch-progress-panel'

/** 沙箱 ctx 只暴露 inject 声明的服务——漏声明则 ctx.webServer 为 undefined，路由静默失败 */
export const inject = ['webServer']

const DEFAULT_SCRIPT = fileURLToPath(new URL('../scripts/progress.py', import.meta.url))

export function apply(ctx, config) {
  const cfg = {
    repo: (config && config.repo) || process.env.CH_PROGRESS_REPO || process.cwd(),
    script: (config && config.script) || DEFAULT_SCRIPT,
    stallMin: (config && config.stallMin) || 30,
  }
  const log = (m) => { try { ctx.logger?.info?.(`[ch-progress-panel] ${m}`) } catch { /* 日志失败不致命 */ } }

  let cache = null
  let cacheAt = 0
  let cleanupRunning = false

  function runProgress(args) {
    return new Promise((resolve) => {
      const p = spawn('python3', [cfg.script, ...args], {
        cwd: cfg.repo, timeout: 180000, killSignal: 'SIGTERM',
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
    try {
      return { ...JSON.parse(r.out), _exit: r.code }
    } catch {
      return { ok: false, error: (r.err || r.out || `progress.py exit ${r.code}`).slice(0, 500), _exit: r.code }
    }
  }

  async function payload() {
    const now = Date.now()
    if (cache && now - cacheAt < 20000) return cache
    const r = await runProgress(['payload', '--repo', cfg.repo, '--stall-min', String(cfg.stallMin)])
    if (r.code !== 0) {
      log(`payload 失败：${(r.err || `exit ${r.code}`).slice(0, 300)}`)
      return { ok: false, error: (r.err || `progress.py exit ${r.code}`).slice(0, 400) }
    }
    try {
      cache = JSON.parse(r.out)
      cacheAt = now
    } catch (e) {
      return { ok: false, error: 'payload JSON 解析失败：' + e.message }
    }
    return cache
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
    const branch = typeof body.branch === 'string' ? body.branch : ''
    if (!branch || branch.length > 300 || branch.includes('\0')) {
      return sendJson(res, 400, { ok: false, error: 'branch 参数无效' })
    }
    cleanupRunning = true
    try {
      const args = ['cleanup', '--repo', cfg.repo, '--branch', branch]
      if (mode === 'apply') {
        if (typeof body.confirm !== 'string' || !/^[a-f0-9]{64}$/.test(body.confirm)) {
          return sendJson(res, 400, { ok: false, error: '缺少有效的预检确认指纹' })
        }
        args.push('--apply', '--confirm', body.confirm)
      }
      const result = parseResult(await runProgress(args))
      delete result._exit
      if (mode === 'apply') {
        cache = null
        cacheAt = 0
        log(`清理 ${branch}：${JSON.stringify(result).slice(0, 350)}`)
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
        const path = new URL(req.url, 'http://127.0.0.1').pathname
        try {
          if (req.method === 'GET' && path === '/api/ch-progress/payload') {
            const data = await payload()
            return sendJson(res, data && data.ok === false ? 500 : 200, data)
          }
          if (req.method === 'POST' && path === '/api/ch-progress/cleanup/preview') {
            return await cleanup(req, res, 'preview')
          }
          if (req.method === 'POST' && path === '/api/ch-progress/cleanup/apply') {
            return await cleanup(req, res, 'apply')
          }
          return sendJson(res, 404, { ok: false, error: `unknown ${req.method} ${path}` })
        } catch (e) {
          return sendJson(res, 500, { ok: false, error: e?.message ?? String(e) })
        }
      },
    }), 'ch-progress-panel: /api/ch-progress 路由')
    log(`数据源就绪：repo=${cfg.repo}`)
  } catch (e) {
    log(`HTTP 路由没挂上：${e?.message ?? e}`)
  }
}
