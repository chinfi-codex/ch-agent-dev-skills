import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const name = 'ch-agent-session-title'
export const inject = ['sessionTitle']

const DEFAULT_SCRIPT = fileURLToPath(new URL('../scripts/session_title.py', import.meta.url))

function blocksToText(content = []) {
  return content.filter(block => block?.type === 'text').map(block => block.text).join('')
}

function currentDirectory(ctx, agent) {
  try {
    return ctx.workingDirectory?.get(agent.session) ?? process.cwd()
  } catch {
    return process.cwd()
  }
}

export function eventPayload(ctx, agent, eventName, details = {}) {
  const current = ctx.sessionTitle.get(agent.session)
  return {
    session_id: agent.session.header.id,
    cwd: currentDirectory(ctx, agent),
    hook_event_name: eventName,
    session_title: current?.title ?? null,
    title_source: current?.source?.kind ?? null,
    origin: agent.session.header.origin ?? null,
    ...details,
  }
}

export async function runPython(config, args, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(config.python, [config.script, ...args], {
      cwd: input?.cwd ?? process.cwd(),
      env: process.env,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => child.kill('SIGTERM'), config.timeoutMs)
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', error => {
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', code => {
      clearTimeout(timer)
      if (code !== 0) return reject(new Error(`session_title.py exited ${code}: ${stderr.trim()}`))
      if (!stdout.trim()) return resolve(null)
      try {
        resolve(JSON.parse(stdout.trim()))
      } catch (error) {
        reject(new Error(`session_title.py returned invalid JSON: ${stdout.trim()}`))
      }
    })
    if (input !== undefined) child.stdin.write(JSON.stringify(input))
    child.stdin.end()
  })
}

export async function synchronize(ctx, agent, eventName, details, config) {
  const payload = eventPayload(ctx, agent, eventName, details)
  if (payload.origin === 'subagent') return null
  const result = await runPython(config, ['hook', '--host', 'deepseek'], payload)
  if (result?.action !== 'rename') return result
  const accepted = ctx.sessionTitle.rename(agent.session, result.title)
  await runPython(config, [
    'ack', '--host', 'deepseek', '--session-id', payload.session_id, '--title', accepted.title,
  ])
  return accepted
}

function resolvedConfig(config = {}) {
  return {
    python: config.python ?? 'python3',
    script: config.script ?? DEFAULT_SCRIPT,
    timeoutMs: config.timeoutMs ?? 60000,
  }
}

export function apply(ctx, config = {}) {
  const resolved = resolvedConfig(config)
  const run = async (agent, eventName, details = {}) => {
    try {
      await synchronize(ctx, agent, eventName, details, resolved)
    } catch (error) {
      ctx.logger.warn(`ch-agent-session-title: ${eventName} skipped: ${String(error)}`)
    }
  }

  ctx.on('agent/created', async ({ agent, source }) => {
    await run(agent, 'SessionStart', { source })
  })
  ctx.on('agent/pre-step', async ({ agent, messages }, next) => {
    const content = messages.flatMap(message => message.content)
    await run(agent, 'UserPromptSubmit', { prompt: blocksToText(content) })
    return next()
  })
  ctx.on('tools/post-execute', async (exec, result, next) => {
    await run(exec.agent, 'PostToolUse', {
      tool_name: exec.name,
      tool_input: exec.arguments,
      tool_use_id: exec.callId,
      tool_response: blocksToText(result.content),
    })
    return next()
  })
  ctx.on('agent/turn-stopping', async ({ agent }) => {
    await run(agent, 'Stop', { stop_hook_active: false })
  })
}
