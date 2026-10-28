import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
rt=load('remote_tests');pc=load('process_check')

class RemoteTests(unittest.TestCase):
    def test_ssh_quoting_and_ports(self):
        self.assertEqual(rt.ssh_target('pandu@192.168.100.116:2222')[-3:],['-p','2222','pandu@192.168.100.116'])
        for s in ('x;touch /tmp/x','-oProxyCommand=evil','u@host:99999','u@host:abc'):
            with self.assertRaises(ValueError):rt.ssh_target(s)

    def test_command_validation(self):
        for rows in ([],[{'name':'../x','argv':['true']}],[{'name':'x','argv':[]}],
                     [{'name':'x','argv':['true']},{'name':'x','argv':['false']}]):
            with self.assertRaises(ValueError):rt.commands_ok(rows)
        self.assertEqual(rt.commands_ok([{'name':'x','argv':['echo','a;$(whoami)']}])[0]['argv'][1],'a;$(whoami)')

    def test_full_commands_must_use_ci(self):
        for name in ('full', 'FULL', ' full '):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'full.*CI'):
                rt.commands_ok([{'name':name,'argv':['{python}','tools/check.py']}])

    def test_resolution_reuses_declared_legacy_and_no_config_write(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'configuration/deploy/topology.json';p.parent.mkdir(parents=True)
            doc={'hosts':{'judge':{'location':'server','controller':'source_root','ssh':'u@host:2222'}},
                 'instances':[{'host':'judge','channel':'test','legacy':{'roots':{'source':'/existing/source'}}}],
                 'remote':{'name':'origin','url':'https://example.test/repo.git'}}
            p.write_text(json.dumps(doc));before=p.read_bytes()
            ssh,payload,_=rt.resolve_local(root,'judge/test','feat/topic')
            self.assertEqual(payload['controller'],'/existing/source')
            self.assertEqual(p.read_bytes(),before)
            doc['instances'][0]['channel']='prod';p.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):rt.resolve_local(root,'judge/prod','main')

    def test_status_does_not_start_another_run(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);rt.save(p/'remote-job.json',{'ssh':['ssh','host'],'folder':'/existing/run'})
            with patch.object(sys,'argv',['remote_tests.py','status','--out',td]), patch.object(rt,'remote',return_value={'status':{'phase':'running'},'files':{}}) as remote:
                self.assertEqual(rt.main(),0)
                self.assertEqual(remote.call_args.args[1],rt.CONTROL)
                self.assertEqual(remote.call_args.args[2]['action'],'status')

    def fixture(self,root):
        source=root/'origin';source.mkdir()
        def git(*args):return rt.call(['git',*args],cwd=source)
        git('init','-q','-b','feat/topic');git('config','user.email','test@example.test');git('config','user.name','Test')
        (source/'file').write_text('original');git('add','.');git('commit','-qm','base')
        folder=root/'run';folder.mkdir()
        ctx={'controller_python':sys.executable,'python':sys.executable,'instance':'judge/test','hostname':'fixture',
             'fingerprints':{'test':'abc'},'runtime_id':'fixture'}
        job={'schema':'agent-dev.remote-test/v1','id':'fixture','commit':git('rev-parse','HEAD'),
             'tree':git('rev-parse','HEAD^{tree}'),'branch':'feat/topic','url':str(source),'context':ctx,'resolve':{},
             'commands':[{'name':'ok','argv':['{python}','-c','print("OK")']}],'timeout':5}
        rt.save(folder/'job.json',job)
        return source,folder,job

    def execute(self,folder,job):
        resolver='import json;print('+repr(json.dumps(job['context']))+')'
        with patch.object(rt,'RESOLVE',resolver), patch.dict(os.environ,{'JOBS':'2'}):
            return rt.execute(folder)

    def test_exact_commit_execution_and_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            _,folder,job=self.fixture(Path(td))
            self.assertEqual(self.execute(folder,job),0)
            data=json.loads((folder/'result.json').read_text())
            self.assertEqual(data['phase'],'completed');self.assertFalse(data['dirty'])
            rt.save(folder/'commands.json',job['commands'])
            ev={'test-location':'remote','remote-runs':['result.json'],'remote-command-manifests':['commands.json']}
            self.assertEqual(pc.remote_receipt_errors(folder/'evidence.md',ev,job['commit'],job['tree']),[])
            (folder/'ok.log').write_text('tampered')
            self.assertTrue(pc.remote_receipt_errors(folder/'evidence.md',ev,job['commit'],job['tree']))

    def test_failed_argv_missing_and_stale_receipts_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _,folder,job=self.fixture(Path(td))
            self.assertEqual(self.execute(folder,job),0)
            rt.save(folder/'commands.json',job['commands'])
            ev={'test-location':'remote','remote-runs':['result.json'],'remote-command-manifests':['commands.json']}
            original=json.loads((folder/'result.json').read_text())
            import copy
            variants=[]
            bad=copy.deepcopy(original);bad['results'][0]['returncode']=1;variants.append(bad)
            bad=copy.deepcopy(original);bad['results'][0]['argv']=['true'];variants.append(bad)
            bad=copy.deepcopy(original);bad['results']=[];variants.append(bad)
            bad=copy.deepcopy(original);bad['commit']='0'*40;variants.append(bad)
            bad=copy.deepcopy(original);bad['dirty']=True;variants.append(bad)
            for bad in variants:
                rt.save(folder/'result.json',bad)
                self.assertTrue(pc.remote_receipt_errors(folder/'evidence.md',ev,job['commit'],job['tree']))
            rt.save(folder/'result.json',original)
            rt.save(folder/'commands.json',job['commands']+[{'name':'omitted','argv':['false']}])
            self.assertTrue(pc.remote_receipt_errors(folder/'evidence.md',ev,job['commit'],job['tree']))

    def test_timeout_stops_descendants(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);_,folder,job=self.fixture(root)
            job.update(want=1,timeout=.1)
            job['commands'][0]['argv']=['{python}','-c','import os,time;open("../child.pid","w").write(str(os.getpid()));time.sleep(30)']
            rt.save(folder/'job.json',job)
            script=(SCRIPTS/'remote_tests.py').read_text()
            resolver='print('+repr(json.dumps(job['context']))+')'
            script=script.replace("if __name__=='__main__':",'RESOLVE = '+repr(resolver)+"\nif __name__=='__main__':")
            (folder/'remote_tests.py').write_text(script)
            (folder/'test_slots.py').write_text('import subprocess,sys,os\nos.environ["JOBS"]="1"\nsys.exit(subprocess.call(sys.argv[sys.argv.index("--")+1:]))\n')
            budget=root/'.cache/agent-dev/slots/budget.json';budget.parent.mkdir(parents=True);budget.write_text('{}')
            with patch.object(Path,'home',return_value=root):self.assertNotEqual(rt.supervise(folder),0)
            result=json.loads((folder/'result.json').read_text())
            self.assertEqual(result['phase'],'failed');self.assertIn('timed out',result['error'])
            pid=int((folder/'child.pid').read_text())
            import time
            for _ in range(20):
                stat=Path('/proc')/str(pid)/'stat'
                if not stat.exists() or stat.read_text().split()[2]=='Z':break
                time.sleep(.05)
            else:self.fail('timed-out child still running')

    def test_advanced_branch_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            source,folder,job=self.fixture(Path(td))
            (source/'file').write_text('later');subprocess.run(['git','commit','-qam','advance'],cwd=source,check=True)
            self.assertEqual(self.execute(folder,job),1)
            self.assertIn('branch advanced',json.loads((folder/'result.json').read_text())['error'])
            self.assertFalse((folder/'ok.log').exists())

    def test_mutated_input_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _,folder,job=self.fixture(Path(td))
            job['commands'][0]['argv']=['{python}','-c','open("file","w").write("changed")']
            rt.save(folder/'job.json',job)
            self.assertEqual(self.execute(folder,job),1)
            self.assertIn('input changed',json.loads((folder/'result.json').read_text())['error'])

    def test_config_drift_prevents_test(self):
        with tempfile.TemporaryDirectory() as td:
            _,folder,job=self.fixture(Path(td))
            with patch.object(rt,'RESOLVE','print("{}")'),patch.dict(os.environ,{'JOBS':'2'}):
                self.assertEqual(rt.execute(folder),1)
            self.assertFalse((folder/'ok.log').exists())

    def test_cancel_owns_only_run_group(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'run';folder.mkdir()
            rt.save(folder/'job.json',{'id':'fixture','context':{'controller_python':sys.executable},'want':1})
            budget=root/'.cache/agent-dev/slots/budget.json';budget.parent.mkdir(parents=True);budget.write_text('{}')
            (folder/'test_slots.py').write_text('import time\ntime.sleep(60)\n')
            (folder/'cancel').touch()
            with patch.object(Path,'home',return_value=root):self.assertEqual(rt.supervise(folder),1)
            self.assertEqual(json.loads((folder/'status.json').read_text())['phase'],'cancelled')
            self.assertEqual(budget.read_text(),'{}')

    def test_missing_budget_is_not_created(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rt.save(root/'job.json',{'id':'fixture'})
            with patch.object(Path,'home',return_value=root):self.assertEqual(rt.supervise(root),2)
            self.assertFalse((root/'.cache').exists())

    def test_missing_remote_evidence_rejected(self):
        self.assertTrue(pc.remote_receipt_errors(Path('/tmp/evidence.md'),{},'a'*40,'b'*40))

    def test_stale_or_incomplete_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rt.save(root/'result.json',{'schema':'agent-dev.remote-test/v1','phase':'running'})
            ev={'test-location':'remote','remote-runs':['result.json']}
            self.assertTrue(pc.remote_receipt_errors(root/'evidence.md',ev,'a'*40,'b'*40))

if __name__=='__main__':unittest.main()
