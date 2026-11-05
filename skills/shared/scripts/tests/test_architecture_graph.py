import json, tempfile, unittest, subprocess, hashlib
from pathlib import Path
import sys
SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
import architecture_graph as graph

class GraphTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); (self.root/'docs/architecture').mkdir(parents=True)
    def tearDown(self): self.tmp.cleanup()
    def put(self, records): (self.root/'docs/architecture/a.json').write_text(json.dumps({'schema':'pandu.architecture-graph/v1','records':records}),encoding='utf8')
    def row(self, **kw): return {'id':'a1.entry','module':'A1','kind':'interface','name':'entry','responsibility':'test responsibility','status':'current','version':'v1','evidence':['test fixture'],'last_verified_version':'v1','source':{'path':'src.py','symbol':'run'},'verification':'evidence:test','last_verified':'2026-11-05','verified_commit':'abc123',**kw}
    def test_valid_and_queryable(self):
        (self.root/'src.py').write_text('def run(): pass')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','src.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=T','-c','user.email=t@e.invalid','commit','-qm','fixture'],check=True)
        commit=subprocess.check_output(['git','-C',str(self.root),'rev-parse','HEAD'],text=True).strip()
        row=self.row(source={'path':'src.py','symbol':'run','sha256':hashlib.sha256((self.root/'src.py').read_bytes()).hexdigest()},verified_commit=commit)
        self.put([row]); records, errors=graph.check(self.root)
        self.assertEqual([],errors); self.assertEqual(1,len(graph.select(records,module='A1',interface='entry')))
    def test_contract_check_reports_graph_errors(self):
        bad={'schema':'pandu.architecture-graph/v1','records':[{'id':'broken'}]}
        (self.root/'docs/architecture/bad.json').write_text(json.dumps(bad),encoding='utf8')
        checker=SCRIPT/'contract_check.py'
        result=subprocess.run([sys.executable,str(checker),'--check','--repo',str(self.root)],capture_output=True,text=True)
        self.assertIn('GRAPH-1',result.stdout)
    def test_rejects_dangling_relation_and_cycle(self):
        self.put([self.row(supersedes=['missing'],relations=[{'type':'calls','target':'missing'}])]); _, errors=graph.check(self.root)
        self.assertTrue(any('supersedes missing id' in e for e in errors)); self.assertTrue(any('dangling relation' in e for e in errors))
    def test_legacy_table_compatibility(self):
        (self.root/'docs/ARCHITECTURE.md').write_text('## 接口现行态\n| Interface | Contract | Source | Origin | Date |\n| --- | --- | --- | --- | --- |\n| run | sig | src.py:run | spec.md | 2026-01-01 |\n',encoding='utf8')
        self.assertEqual('run',graph.legacy_rows(self.root)[0][0])
    def test_legacy_identity_survives_annotation_changes_and_is_queryable(self):
        path=self.root/'docs/ARCHITECTURE.md'
        path.write_text('## 接口现行态\n| Interface | Contract | Source | Origin | Date |\n| --- | --- | --- | --- | --- |\n| run | sig | src.py:run | spec.md | 2026-01-01 |\n',encoding='utf8')
        first=graph.legacy_records(self.root)[0]
        path.write_text('## 接口现行态\n| Interface | Contract | Source | Origin | Date |\n| --- | --- | --- | --- | --- |\n| run | changed | src.py:run | other.md | 2026-02-01 |\n',encoding='utf8')
        second=graph.legacy_records(self.root)[0]
        self.assertEqual(first['id'],second['id'])
        self.assertEqual([first['id']], [r['id'] for r in graph.select([second])])
    def test_legacy_identity_includes_branch(self):
        (self.root/'docs/ARCHITECTURE.md').write_text('## 接口现行态\n| Interface | Contract | Source | Origin | Date | Branch |\n| --- | --- | --- | --- | --- | --- |\n| run | sig | src.py:run | spec.md | 2026-01-01 | main |\n| run | sig | src.py:run | spec.md | 2026-01-01 | release |\n',encoding='utf8')
        rows=graph.legacy_records(self.root)
        self.assertEqual(2,len({row['id'] for row in rows}))
    def test_graph_overrides_matching_legacy_row(self):
        legacy='''# ARCHITECTURE\n\n## 接口现行态\n| Interface | Contract | Source | Origin | Date |\n| --- | --- | --- | --- | --- |\n| run | sig | src.py:run | spec.md | 2026-01-01 |\n'''
        (self.root/'docs/ARCHITECTURE.md').write_text(legacy,encoding='utf8')
        row=graph.legacy_records(self.root)[0]
        explicit=self.row(id=row['id'],status='retired',verified_commit=None)
        self.put([explicit]); merged,errors=graph.read_records(self.root)
        self.assertEqual([],errors)
        self.assertEqual('retired',next(x for x in merged if x['id']==row['id'])['status'])
    def test_stale_source_hash_is_rejected_for_current(self):
        (self.root/'src.py').write_text('def run(): pass')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','src.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=T','-c','user.email=t@e.invalid','commit','-qm','fixture'],check=True)
        commit=subprocess.check_output(['git','-C',str(self.root),'rev-parse','HEAD'],text=True).strip()
        self.put([self.row(source={'path':'src.py','symbol':'run','sha256':'0'*64},verified_commit=commit)])
        _,errors=graph.check(self.root)
        self.assertTrue(any('stale source hash' in e for e in errors))
    def test_current_query_excludes_retired_and_pending_but_allows_audit_filter(self):
        (self.root/'src.py').write_text('def run(): pass')
        pending=self.row(status='needs_verification',verified_commit=None)
        retired=self.row(id='a1.old',status='retired',verified_commit=None)
        current=self.row(id='a1.v1',status='current',version='v1')
        self.put([pending,retired,current]); records,errors=graph.check(self.root)
        self.assertTrue(errors)
        self.assertEqual(['a1.v1'],[r['id'] for r in graph.select([current,pending,retired])])
        self.assertEqual(['a1.entry'],[r['id'] for r in graph.select([current,pending,retired],status='needs_verification')])
    def test_versioned_query_requires_matching_applicability(self):
        rows=[self.row(id='v1',status='needs_verification',verified_commit=None,version='v1'),
              self.row(id='v2',status='needs_verification',verified_commit=None,version='v2')]
        self.assertEqual(['v1'],[r['id'] for r in graph.select(rows,version='v1',status='needs_verification')])
    def test_unverified_excluded_from_current_query(self):
        self.put([self.row(status='needs_verification',verified_commit=None)]); records, errors=graph.check(self.root)
        self.assertEqual([],errors); self.assertEqual([],graph.select(records))

    def test_homepage_is_deterministic_and_short(self):
        rows=[self.row(status='needs_verification',verified_commit=None)]
        first=graph.homepage(rows)
        self.assertEqual(first,graph.homepage(rows))
        self.assertLess(len(first.splitlines()),20)
        self.assertIn('唯一写入源',first)
    def test_release_only_uses_historical_commit_when_source_moved(self):
        (self.root/'src.py').write_text('def run(): pass\n')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','src.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=T','-c','user.email=t@e.invalid','commit','-qm','fixture'],check=True)
        commit=subprocess.check_output(['git','-C',str(self.root),'rev-parse','HEAD'],text=True).strip()
        data=(self.root/'src.py').read_bytes(); source={'path':'src.py','symbol':'run','sha256':hashlib.sha256(data).hexdigest()}
        row=self.row(status='release_only',source=source,verified_commit=commit)
        (self.root/'src.py').unlink(); self.put([row]); _,errors=graph.check(self.root)
        self.assertEqual([],errors)
    def test_malformed_source_and_relation_return_errors(self):
        bad=self.row(source={'sha256':'x'},relations=['bad'])
        self.put([bad]); _,errors=graph.check(self.root)
        self.assertTrue(any('source.path' in e for e in errors))
        self.assertTrue(any('malformed relation' in e for e in errors))
    def test_malformed_list_and_status_are_errors_not_exceptions(self):
        bad=self.row(status=[],supersedes=None,relations=None,verified_commit=None)
        self.put([bad]); _,errors=graph.check(self.root)
        self.assertTrue(any('invalid status' in e for e in errors))
        self.assertTrue(any('supersedes must be a list' in e for e in errors))
        self.assertTrue(any('relations must be a list' in e for e in errors))
    def test_malformed_ids_and_supersedes_targets_do_not_crash_cycle_check(self):
        bad_id=self.row(id=[],supersedes=[{}],status='needs_verification',verified_commit=None)
        good=self.row(id='good',supersedes=[{}],status='needs_verification',verified_commit=None)
        self.put([bad_id,good]); _,errors=graph.check(self.root)
        self.assertTrue(any('empty id' in e for e in errors))
        self.assertTrue(any('invalid supersedes target' in e for e in errors))

if __name__=='__main__': unittest.main()
