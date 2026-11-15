import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import http from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { apply } from './index.js'

const ROOT = await mkdtemp(join(tmpdir(), 'dsh-progress-http-'))
const SCRIPT = join(ROOT, 'fake-progress.py')
const LOG = join(ROOT, 'args.jsonl')
await writeFile(SCRIPT, `import json,sys
with open(${JSON.stringify(LOG)},'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
a=sys.argv[1:]
if 'reconcile' in a:
 print(json.dumps({'ok':True,'applied':'--apply' in a,'operations':[{'ok':True,'state':'completed','operation_id':'op-1'}]}))
elif '--apply' in a and 'close-and-clean' in a:
 if 'd'*64 in a:
  import subprocess,signal,time
  subprocess.Popen([sys.executable,'-c',"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(0.75)"])
  signal.signal(signal.SIGTERM,signal.SIG_IGN)
  time.sleep(0.75)
 elif 'e'*64 in a:
  import time
  time.sleep(0.05)
  print(json.dumps({'ok':True,'state':'completed','operation_id':'op-1'}))
 elif 'f'*64 in a: print(json.dumps({'ok':False,'code':'stale','stale':True,'error':'stale'}))
 else: print(json.dumps({'ok':True,'state':'completed','operation_id':'op-1'}))
elif '--issue' in a and 'main' in a:
 print(json.dumps({'ok':False,'eligible':False,'protected':True,'code':'protected','error':'protected'}))
else:
 print(json.dumps({'ok':True,'eligible':True,'intent':'close','operation_id':'op-1','confirm':'a'*64}))
`)

test('v2 HTTP, protected/stale status and legacy 404', async (t) => {
  t.after(async () => rm(ROOT, { recursive: true, force: true }))
  let handler
  const ctx = {
    effect: (fn) => fn(),
    logger: { info: () => {} },
    webServer: { register: (route) => { handler = route.handler } },
  }
  apply(ctx, { repo: ROOT, script: SCRIPT, operationTimeoutMs: 500, processKillGraceMs: 1000, processExitWaitMs: 100 })
  const server = http.createServer((req, res) => handler(req, res))
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => server.close(resolve)))
  const base = `http://127.0.0.1:${server.address().port}/api/ch-progress`
  const post = (path, body, confirmed = false) => fetch(base + path, {
    method: 'POST', headers: { 'content-type': 'application/json', ...(confirmed ? { 'x-ch-progress-action': 'apply' } : {}) },
    body: JSON.stringify({ repo: ROOT, ...body }),
  })

  for (const path of ['/close', '/cleanup/preview', '/cleanup/apply', '/decision/answer']) {
    const response = await post(path, { issue: 'issue-001' })
    assert.equal(response.status, 404, path)
  }
  let response = await post('/close-clean/preview', { issue: 'issue-001', intent: 'close' })
  assert.equal(response.status, 200)
  const plan = await response.json()
  assert.equal(plan.operation_id, 'op-1')

  response = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'a'.repeat(64) })
  assert.equal(response.status, 403)
  response = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'a'.repeat(64) }, true)
  assert.equal(response.status, 200)
  assert.equal((await response.json()).state, 'completed')

  response = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'f'.repeat(64) }, true)
  assert.equal(response.status, 409)
  response = await post('/close-clean/preview', { issue: 'main', intent: 'close' })
  assert.equal(response.status, 403)

  response = await post('/reconcile', { operation_id: 'op-1' })
  assert.equal(response.status, 200)
  assert.equal((await response.json()).applied, false)
  response = await post('/reconcile/apply', { operation_id: 'op-1' }, true)
  assert.equal(response.status, 200)
  assert.equal((await response.json()).applied, true)

  // Re-probing historical groups must not clear a normal in-flight apply.
  const inFlight = post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'e'.repeat(64) }, true)
  const overlapping = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'e'.repeat(64) }, true)
  assert.equal(overlapping.status, 409, await overlapping.text())
  const completed = await inFlight
  assert.equal(completed.status, 200, await completed.text())

  // Parent and grandchild both ignore SIGTERM. The first apply must keep the
  // operation occupied; a second apply cannot overlap the surviving tree.
  response = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'd'.repeat(64) }, true)
  assert.equal(response.status, 409)
  assert.equal((await response.json()).occupied, true)
  response = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'd'.repeat(64) }, true)
  assert.equal(response.status, 409)
  // The group exits after the first timeout response. A later request re-probes
  // it; reconciliation may now succeed and is what releases the occupied gate.
  await new Promise(resolve => setTimeout(resolve, 500))
  response = await post('/reconcile', { operation_id: 'op-1' })
  assert.equal(response.status, 200)
  response = await post('/reconcile/apply', { operation_id: 'op-1' }, true)
  assert.equal(response.status, 200)
  response = await post('/close-clean/apply', { issue: 'issue-001', operation_id: 'op-1', confirm: 'a'.repeat(64) }, true)
  assert.equal(response.status, 200)

  const calls = (await readFile(LOG, 'utf8')).trim().split('\n').map(JSON.parse)
  assert.equal(calls.filter(args => args.includes('d'.repeat(64))).length, 1)
  assert.equal(calls.filter(args => args.includes('e'.repeat(64))).length, 1)
  assert.ok(calls.every(args => !['cleanup', 'close'].includes(args[0]) && !args.includes('merge')))
  assert.ok(calls.some(args => args.includes('close-and-clean') && args.includes('--operation-id')))
  assert.ok(calls.some(args => args.includes('reconcile') && args.includes('--apply')))
  const client = await readFile(new URL('./client.js', import.meta.url), 'utf8')
  assert.ok(!client.includes('合并保留'))
  assert.ok(!client.includes("onApply('merge')"))
  assert.ok(!client.includes('/cleanup/'))
  assert.ok(!client.includes('/decision/answer'))
  assert.ok(client.includes('!n.protected'))
})
