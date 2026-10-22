import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { eventPayload, synchronize } from './index.js'

const SCRIPT = new URL('../scripts/session_title.py', import.meta.url).pathname

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'dsh-title-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const issue = join(root, 'dev/features/m/f/issues/issue-001-x.md')
  await mkdir(join(issue, '..'), { recursive: true })
  await writeFile(issue, '---\nissue-id: issue-001\ntitle: 测试标题\nstatus: confirmed\n---\n')
  const state = join(root, 'state.json')
  const previous = process.env.SESSION_TITLE_STATE
  process.env.SESSION_TITLE_STATE = state
  t.after(() => {
    if (previous === undefined) delete process.env.SESSION_TITLE_STATE
    else process.env.SESSION_TITLE_STATE = previous
  })
  let current = { title: '旧标题', source: { kind: 'fallback' } }
  let renames = 0
  const agent = { session: { header: { id: 'session-1', origin: 'interactive', cwd: root } } }
  const ctx = {
    workingDirectory: { get: () => root },
    sessionTitle: {
      get: () => current,
      rename: (_session, title) => {
        renames += 1
        current = { title, source: { kind: 'user' } }
        return current
      },
    },
  }
  return { root, state, agent, ctx, current: () => current, renames: () => renames }
}

const CONFIG = { python: 'python3', script: SCRIPT, timeoutMs: 15000 }

test('payload carries native title source and origin', async (t) => {
  const f = await fixture(t)
  const payload = eventPayload(f.ctx, f.agent, 'SessionStart', { source: 'startup' })
  assert.equal(payload.session_title, '旧标题')
  assert.equal(payload.title_source, 'fallback')
  assert.equal(payload.origin, 'interactive')
})

test('plugin renames natively then acknowledges accepted title', async (t) => {
  const f = await fixture(t)
  const accepted = await synchronize(f.ctx, f.agent, 'UserPromptSubmit', { prompt: '/implement issue-001' }, CONFIG)
  assert.equal(accepted.title, '[待执行] f · issue-001 测试标题')
  assert.equal(f.renames(), 1)
  const state = JSON.parse(await readFile(f.state, 'utf8'))
  assert.equal(state.sessions['deepseek:session-1'].last_title, accepted.title)

  await synchronize(f.ctx, f.agent, 'UserPromptSubmit', { prompt: '/implement issue-001' }, CONFIG)
  assert.equal(f.renames(), 1, 'unchanged title must not rename again')
})

test('manual title and subagent are protected', async (t) => {
  const f = await fixture(t)
  await synchronize(f.ctx, f.agent, 'UserPromptSubmit', { prompt: '/implement issue-001' }, CONFIG)
  f.ctx.sessionTitle.get = () => ({ title: '手工标题', source: { kind: 'user' } })
  await synchronize(f.ctx, f.agent, 'UserPromptSubmit', { prompt: '/implement issue-001' }, CONFIG)
  assert.equal(f.renames(), 1)
  const state = JSON.parse(await readFile(f.state, 'utf8'))
  assert.equal(state.sessions['deepseek:session-1'].paused, true)

  f.agent.session.header.origin = 'subagent'
  await synchronize(f.ctx, f.agent, 'Stop', {}, CONFIG)
  assert.equal(f.renames(), 1)
})
