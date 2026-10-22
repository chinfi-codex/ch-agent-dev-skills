#!/usr/bin/env python3
"""Remote tests using existing Pandu topology; never writes deployment configuration.

run --repo REPO --instance HOST/NAME --commands commands.json --out DIR [--push]
status|collect|cancel --out DIR
commands.json: [{"name":"regression", "argv":["{python}","tools/tests/example.py"]}]
{python} is the existing deployment runtime; {jobs} is the allocated worker count.
No shell expansion. For a shell pipeline explicitly use ["bash", "-lc", "..."]
with $JOBS and $PANDU_TEST_PYTHON. No credentials may appear in command arguments.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(argv, **kwargs):
    return subprocess.check_output(argv, text=True, **kwargs).strip()


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix('.part')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)


def commands_ok(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('commands must be a nonempty JSON list')
    names = set()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]+', row.get('name', '')):
            raise ValueError('invalid command name')
        if row['name'] in names:
            raise ValueError('duplicate command name')
        names.add(row['name'])
        if not isinstance(row.get('argv'), list) or not row['argv'] or not all(isinstance(v, str) and '\x00' not in v for v in row['argv']):
            raise ValueError('argv must be a nonempty string list')
    return rows


def ssh_target(value):
    # Existing topology uses user@host:port; bracketed IPv6 also accepted.
    m = re.fullmatch(r'([A-Za-z0-9_.-]+@)?(\[[0-9a-fA-F:]+\]|[A-Za-z0-9_.-]+)(?::([0-9]+))?', value)
    if not m:
        raise ValueError('unsupported topology SSH address')
    port = int(m[3] or 22)
    if not 0 < port < 65536:
        raise ValueError('invalid SSH port')
    return ['ssh', '-o', 'BatchMode=yes', '-p', str(port), (m[1] or '')+m[2].strip('[]')]


def remote(ssh, program, payload):
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    # SSH takes one remote command; shlex.join is required (JSON is not shell quoting).
    cmd = shlex.join(['python3', '-c', program, encoded])
    return json.loads(call(ssh+[cmd]))


RESOLVE = r'''
import base64,json,sys,hashlib
from pathlib import Path
p=json.loads(base64.b64decode(sys.argv[1]))
sys.path.insert(0,str(Path(p['controller'])/'tools'))
from pandu_runtime import parameters, topology
c=parameters.resolve(p['instance'])
i=c['instance'];d=c['document'];h=c['host_config']
if i['channel']=='prod': raise ValueError('production instance is not a test target')
if hashlib.sha256(Path(c['paths']['topology']).read_bytes()).hexdigest()!=p['topology_sha256']:
    raise ValueError('local and controller topology differ; resolve explicitly, do not rewrite configuration')
if d['hosts'][i['host']]['location']!='server': raise ValueError('target must be a server')
if d['remote']['url']!=p['url']: raise ValueError('remote URL differs')
if p['branch']:
    # Deployment branch allow/deny governs track, not isolated test refs.
    tracked=topology.track_ref(d,h,i['channel'],i['name'])
    # Isolated tests may use another allowed ticket branch; never change the instance track.
from pandu_runtime.installation import load_profile,runtime_identifier
runtime=runtime_identifier(load_profile(c['description']['runtime_profile'])['profile'],c['description']['requirements'])
python=Path(i['roots']['state'])/'runtimes'/runtime/'venv'/'bin'/'python'
if not python.is_file(): raise ValueError('configured runtime is missing; no installation fallback')
print(json.dumps({'instance':c['reference'],'state_root':i['roots']['state'],
 'source_root':i['roots']['source'],'python':str(python),'controller_python':h['python'],
 'fingerprints':c['fingerprints'],'runtime_id':runtime,'hostname':__import__('socket').gethostname()}))
'''


START = r'''
import base64,json,sys,subprocess
from pathlib import Path
p=json.loads(base64.b64decode(sys.argv[1]));folder=Path(p['folder'])
folder.mkdir(parents=True,exist_ok=False)
for name,encoded in p['files'].items(): (folder/name).write_bytes(base64.b64decode(encoded))
(folder/'job.json').write_text(json.dumps(p['job']))
log=(folder/'supervisor.log').open('ab')
proc=subprocess.Popen([p['job']['context']['controller_python'],str(folder/'remote_tests.py'),'_supervise',str(folder)],
 stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'pid':proc.pid,'folder':str(folder)}))
'''
CONTROL = r'''
import base64,json,sys
from pathlib import Path
p=json.loads(base64.b64decode(sys.argv[1]));folder=Path(p['folder'])
if p['action']=='cancel': (folder/'cancel').touch()
files={}
if p['action']=='collect':
 for f in folder.iterdir():
  if f.name in ('result.json','status.json','job.json','supervisor.log') or f.suffix=='.log':
   files[f.name]=base64.b64encode(f.read_bytes()).decode()
s=folder/'status.json'
print(json.dumps({'status':json.loads(s.read_text()) if s.exists() else {'phase':'starting'},'files':files}))
'''


def resolve_local(repo, instance, branch):
    path = repo/'configuration/deploy/topology.json'
    doc = json.loads(path.read_text())
    host, name = instance.split('/', 1)
    row = next(r for r in doc['instances'] if r['host']==host and r.get('name', r['channel'])==name)
    decl = doc['hosts'][host]
    if decl['location'] != 'server' or row['channel']=='prod':
        raise ValueError('select a declared non-production server instance')
    kind = decl['controller'].removesuffix('_root')
    if row.get('legacy'):
        controller = row['legacy']['roots'][kind]
    else:
        controller = doc['naming']['roots'].format(base=decl['base'],name=name,channel=row['channel'],root=kind)
    ssh = ssh_target(decl['ssh'])
    payload = {'controller':controller, 'instance':instance, 'branch':branch,
               'url':doc['remote']['url'], 'topology_sha256':digest(path)}
    return ssh, payload, doc['remote']


def start(args):
    repo = Path(args.repo).resolve()
    if call(['git','status','--porcelain','--untracked-files=normal'],cwd=repo):
        raise ValueError('commit test inputs first; worktree must be clean')
    branch = call(['git','symbolic-ref','--short','HEAD'],cwd=repo)
    sha = call(['git','rev-parse','HEAD'],cwd=repo)
    tree = call(['git','rev-parse','HEAD^{tree}'],cwd=repo)
    subprocess.run(['git','check-ref-format',f'refs/heads/{branch}'],check=True)
    rows = commands_ok(json.loads(Path(args.commands).read_text()))
    ssh, payload, git_remote = resolve_local(repo,args.instance,branch)
    context = remote(ssh,RESOLVE,payload)  # Resolve before any push.
    if call(['git','remote','get-url',git_remote['name']],cwd=repo)!=git_remote['url']:
        raise ValueError('Git remote differs from deployment topology')
    out = Path(args.out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    if (out/'remote-job.json').exists():
        raise ValueError('output already owns a run; use status/collect or a new output directory')
    if args.push and branch in ('main','master'):
        raise ValueError('main branch push is outside the development test authorization')
    if args.push:
        subprocess.run(['git','push',git_remote['name'],f'HEAD:refs/heads/{branch}'],cwd=repo,check=True)
    advertised = call(['git','ls-remote',git_remote['name'],f'refs/heads/{branch}'],cwd=repo).split()
    if not advertised or advertised[0]!=sha:
        raise ValueError('same-name remote branch is not the requested commit; authorized push required')
    run_id = time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:12]
    folder = str(Path(context['state_root'])/'dev-tests'/run_id)
    job = {'schema':'agent-dev.remote-test/v1','id':run_id,'commit':sha,'tree':tree,
           'branch':branch,'url':git_remote['url'],'context':context,'resolve':payload,
           'commands':rows,'want':args.want,'timeout':args.timeout}
    scripts = Path(__file__).parent
    files = {n:base64.b64encode((scripts/n).read_bytes()).decode() for n in ('remote_tests.py','test_slots.py')}
    # Save locator first: interrupted start must never silently launch another run.
    save(out/'commands.json',rows)
    save(out/'remote-job.json',{'ssh':ssh,'folder':folder,'job':job})
    response = remote(ssh,START,{'folder':folder,'job':job,'files':files})
    print(json.dumps(response))


def supervise(folder):
    folder = Path(folder); job = json.loads((folder/'job.json').read_text())
    save(folder/'status.json',{'phase':'queued','id':job['id']})
    # Machine budget must already exist; never let the wrapper create or modify it.
    budget = Path.home()/'.cache/agent-dev/slots/budget.json'
    if not budget.is_file():
        save(folder/'status.json',{'phase':'blocked','reason':'machine test budget missing'})
        return 2
    cmd = [job['context']['controller_python'],str(folder/'test_slots.py'),'run','--want',str(job['want']),
           '--label','remote-test/'+job['id'],'--',job['context']['controller_python'],str(folder/'remote_tests.py'),'_execute',str(folder)]
    env = dict(os.environ); env.pop('AGENT_DEV_SLOTS_DIR',None)
    p = subprocess.Popen(cmd,start_new_session=True,env=env)
    while p.poll() is None:
        if (folder/'cancel').exists():
            os.killpg(p.pid,signal.SIGTERM)
            try: p.wait(timeout=15)
            except subprocess.TimeoutExpired: pass
            # Also remove any still-running test descendants in our process group.
            try: os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            save(folder/'status.json',{'phase':'cancelled','id':job['id']})
            return 1
        time.sleep(.5)
    try: os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    if not (folder/'result.json').exists():
        save(folder/'status.json',{'phase':'failed','id':job['id'],'returncode':p.returncode})
    return p.returncode


def execute(folder):
    folder = Path(folder); job = json.loads((folder/'job.json').read_text()); ctx = job['context']
    result = {**job,'jobs':int(os.environ['JOBS']),'phase':'running','results':[],'started_at':time.time()}
    save(folder/'status.json',{'phase':'running','id':job['id']})
    code = folder/'code'; code.mkdir()
    try:
        refreshed = json.loads(call([ctx['controller_python'],'-c',RESOLVE,
                     base64.b64encode(json.dumps(job['resolve']).encode()).decode()]))
        if refreshed != ctx: raise ValueError('deployment parameters changed before execution')
        subprocess.run(['git','init','-q',str(code)],check=True)
        subprocess.run(['git','-C',str(code),'fetch','--quiet','--no-tags',job['url'],f'refs/heads/{job["branch"]}'],check=True)
        if call(['git','rev-parse','FETCH_HEAD'],cwd=code)!=job['commit']:
            raise ValueError('branch advanced before checkout; submit a new run')
        subprocess.run(['git','checkout','--quiet','--detach',job['commit']],cwd=code,check=True)
        if call(['git','rev-parse','HEAD^{tree}'],cwd=code)!=job['tree']: raise ValueError('tree mismatch')
        tmp=folder/'tmp'; tmp.mkdir()
        env=dict(os.environ,TMPDIR=str(tmp),PYTHONUNBUFFERED='1',PYTHONUTF8='1',PANDU_TEST_PYTHON=ctx['python'])
        env['PATH']=str(Path(ctx['python']).parent)+os.pathsep+env.get('PATH','')
        result['python_version']=call([ctx['python'],'--version'])
        result['packages_sha256']=hashlib.sha256(call([ctx['python'],'-m','pip','freeze','--all']).encode()).hexdigest()
        for row in job['commands']:
            argv=[v.replace('{python}',ctx['python']).replace('{jobs}',os.environ['JOBS']) for v in row['argv']]
            log=folder/(row['name']+'.log'); began=time.monotonic()
            with log.open('w') as stream:
                p=subprocess.Popen(argv,cwd=code,env=env,stdout=stream,stderr=subprocess.STDOUT)
                try: rc=p.wait(timeout=job['timeout'])
                except subprocess.TimeoutExpired:
                    # Stop this whole run rather than allowing leaked subprocesses into the next test.
                    raise TimeoutError('command timed out: '+row['name'])
            result['results'].append({'name':row['name'],'argv':argv,'returncode':rc,
                'seconds':round(time.monotonic()-began,3),'log':log.name,'log_sha256':digest(log)})
        result['dirty']=bool(call(['git','status','--porcelain','--untracked-files=no'],cwd=code))
        result['final_commit']=call(['git','rev-parse','HEAD'],cwd=code)
        if result['dirty'] or result['final_commit']!=job['commit']: raise ValueError('test input changed')
        refreshed=json.loads(call([ctx['controller_python'],'-c',RESOLVE,
                    base64.b64encode(json.dumps(job['resolve']).encode()).decode()]))
        if refreshed!=ctx: raise ValueError('deployment parameters changed during execution')
        result['phase']='completed'
    except Exception as exc:
        result['phase']='failed'; result['error']=str(exc)
        # Supervisor owns the process group and handles descendant cleanup.
        (folder/'cancel').touch()
    result['finished_at']=time.time()
    save(folder/'result.json',result)
    save(folder/'status.json',{'phase':result['phase'],'id':job['id'],'results':result['results']})
    return int(result['phase']!='completed' or any(r['returncode'] for r in result['results']))


def main():
    if len(sys.argv)>1 and sys.argv[1] in ('_supervise','_execute'):
        return (supervise if sys.argv[1]=='_supervise' else execute)(sys.argv[2])
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='action',required=True)
    run=sub.add_parser('run')
    for name in ('repo','instance','commands','out'): run.add_argument('--'+name,required=True)
    run.add_argument('--push',action='store_true',help='explicitly authorized normal same-branch push')
    run.add_argument('--want',type=int,default=4)
    run.add_argument('--timeout',type=int,default=3600,help='per-command seconds')
    for name in ('status','collect','cancel'):
        sub.add_parser(name).add_argument('--out',required=True)
    args=ap.parse_args()
    if args.action=='run':
        if args.want<1 or args.timeout<1: raise ValueError('want and timeout must be positive')
        start(args)
    else:
        out=Path(args.out); locator=json.loads((out/'remote-job.json').read_text())
        response=remote(locator['ssh'],CONTROL,{'folder':locator['folder'],'action':args.action})
        for name,data in response.pop('files').items():
            if Path(name).name!=name: raise ValueError('unsafe artifact name')
            (out/name).write_bytes(base64.b64decode(data))
        print(json.dumps(response,ensure_ascii=False))
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('remote-tests: '+str(exc),file=sys.stderr);sys.exit(2)
