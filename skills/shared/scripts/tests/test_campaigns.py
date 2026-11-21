"""Campaign isolation exercised through real delivery, freeze and recovery APIs."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_scope as scopes
import delivery
import feature_closeout as fc
import human_decision as hd
import progress
import progress_cleanup
import review_receipt
import session_title
import ticket_context
import verify_issue
import artifact_gc
import test_artifact_gc as gc_fixture
import contract_check
import test_delivery as delivery_fixture
import test_feature_closeout as closeout_fixture
import test_human_decision as decision_fixture


class CampaignTest(unittest.TestCase):
    def fixture(self, cls):
        obj = cls()
        obj.setUp()
        self.addCleanup(obj.tearDown)
        return obj

    def campaign(self, root, source, name):
        target = source / 'campaigns' / name
        target.mkdir(parents=True)
        for entry in list(source.iterdir()):
            if entry.name == 'campaigns':
                continue
            if entry.is_dir():
                shutil.copytree(entry, target / entry.name)
            else:
                shutil.copy2(entry, target / entry.name)
        for issue in (target / 'issues').glob('*.md'):
            issue.write_text(issue.read_text().replace('feature-slug: demo',
                'feature-slug: demo\ncampaign-id: ' + name))
        return target

    def test_two_campaigns_start_same_ticket_id_without_plan_rotation(self):
        f = self.fixture(delivery_fixture.DeliveryTest)
        a = self.campaign(f.root, f.fdir, 'a')
        b = self.campaign(f.root, f.fdir, 'b')
        # Distinct spec and plan, including one explicitly shared feature PRD.
        old = b / f.spec.name
        new = old.with_name('b-tech-spec-2026-11-20.md')
        old.rename(new)
        issue_b = b / 'issues' / f.issue.name
        issue_b.write_text(issue_b.read_text().replace(f.spec.name, new.name))
        plan_b = b / 'acceptance-plan.yaml'
        plan_b.write_text(plan_b.read_text().replace(f.spec.name, new.name))
        for name in ('a', 'b'):
            delivery_fixture.sh(f.root, 'branch', 'feat/mod/demo/' + name, 'main')
        ra = delivery.start(a / 'issues' / f.issue.name, f.root)
        rb = delivery.start(issue_b, f.root)
        self.assertNotEqual(ra['worktree'], rb['worktree'])
        self.assertNotEqual(ra['target'], rb['target'])
        for result in (ra, rb):
            self.assertEqual((Path(result['worktree']) / 'dev').resolve(), (f.root / 'dev').resolve())
            context = ticket_context.load_context(result['context'])
            self.assertIn('/campaigns/', context['feature_dir'])
        # Other campaign can change without altering A's frozen bytes or admission.
        before = Path(ra['context']).read_bytes()
        plan_b.write_text('broken unrelated plan\n')
        self.assertTrue(ticket_context.check_plan(a / 'issues' / f.issue.name, f.root)['ok'])
        self.assertEqual(Path(ra['context']).read_bytes(), before)
        self.assertEqual(delivery.start(a / 'issues' / f.issue.name, f.root)['context'], ra['context'])
        self.assertEqual(set(progress.find_features(f.root)), {f.fdir, a, b})
        self.assertEqual(delivery._source_branch(ticket_context.load_context(ra['context']), a / 'issues' / f.issue.name), ra['branch'])
        ticket = {'feature_dir': str(a), 'id': 'issue-001', 'fm': {'feature-slug': 'demo'}, 'feature': scopes.scope_key(a)}
        self.assertTrue(progress_cleanup._branch_matches(ticket, ra['branch']))
        self.assertFalse(progress_cleanup._branch_matches(ticket, rb['branch']))
        exact = progress.find_ticket(f.root, str(a / 'issues' / f.issue.name))
        self.assertEqual(Path(exact['feature_dir']).resolve(), a.resolve())
        self.assertEqual(progress_cleanup._ticket_target(f.root, exact, fc.load_config(a)), ra['target'])
        choices = {'issue-001': [exact, progress.find_ticket(f.root, str(issue_b))]}
        self.assertEqual(progress.resolve_ticket(choices, 'issue-001', rb['branch'])['feature'], scopes.scope_key(b))
        self.assertIsNone(progress.resolve_ticket(choices, 'issue-001', 'demo'))
        # Valid digest does not authorize another campaign to consume this context.
        Path(rb['context']).write_bytes(Path(ra['context']).read_bytes())
        with self.assertRaisesRegex(verify_issue.VerificationError, 'another feature/campaign'):
            verify_issue._candidate_context(b, f.root, 'issue-001')


    def test_real_campaign_delivery_gate_merge_and_candidate_rerun(self):
        class Fixture(delivery_fixture.DeliveryTest):
            @property
            def source(self):
                return scopes.worktree_path(self.root, self.fdir, 'issue-001')
        f = self.fixture(Fixture)
        original = f.fdir
        a = self.campaign(f.root, original, 'a')
        b = self.campaign(f.root, original, 'b')
        f.fdir = a
        f.issue = a / 'issues' / f.issue.name
        f.spec = a / f.spec.name
        delivery_fixture.sh(f.integration, 'branch', '-m', 'feat/mod/demo/a')
        f.start()
        f.implement()
        f.final_rebase()
        f.verify('implementer')
        import test_process_check
        log = a / 'impl/issue-001/impl-log-issue-001.md'
        log.write_text(test_process_check.LOG.replace('tests/test_dod.py', 't.py'))
        result = delivery.merge(f.context_path)
        self.assertEqual(result['status'], 'merged')
        self.assertIn('status: confirmed', (b / 'issues' / f.issue.name).read_text())
        from types import SimpleNamespace
        report = fc.freeze(SimpleNamespace(feature_dir=str(a), repo=str(f.integration), round=None, issues=None))
        self.assertFalse(report.failed, str(report))
        aggregate = verify_issue.run_candidate(a / 'integration/candidate.json', f.integration,
            host='zcode', agent='supervisor-review', session='candidate-review',
            model='provider/supervisor', effort='high')
        self.assertTrue(aggregate['all-passed'], aggregate)
        self.assertEqual([item['issue'] for item in aggregate['issues']], ['issue-001'])
        self.assertFalse((b / 'integration').exists())

    def test_stacked_campaign_real_delivery_freeze_and_candidate_rerun(self):
        class Fixture(delivery_fixture.DeliveryTest):
            @property
            def source(self):
                return scopes.worktree_path(self.root, self.fdir, 'issue-001')

        f = self.fixture(Fixture)
        predecessor = f.fdir
        b = self.campaign(f.root, predecessor, 'b')
        sh = delivery_fixture.sh
        (f.integration / 'predecessor.py').write_text('previous = True\n')
        sh(f.integration, 'add', 'predecessor.py')
        sh(f.integration, 'commit', '-qm', 'predecessor')
        pin = sh(f.integration, 'rev-parse', 'HEAD')
        receipt = delivery.stack_source(f.root, predecessor, commit=pin)
        self.assertEqual(receipt['status'], 'not-accepted')
        self.assertEqual(receipt['main_ancestor'], sh(f.root, 'rev-parse', 'main'))
        # Main advancing after the pin must not invalidate the frozen receipt.
        (f.root / 'main-only.py').write_text('main_advanced = True\n')
        sh(f.root, 'add', 'main-only.py')
        sh(f.root, 'commit', '-qm', 'main advances independently')
        main = sh(f.root, 'rev-parse', 'main')
        stack = b / 'stack-base.json'
        stack.write_text(json.dumps({
            'schema': 'agent-dev.stack-base/v1', 'consumer_scope': scopes.scope_key(b),
            'predecessor_scope': scopes.scope_key(predecessor),
            'predecessor_ref': 'refs/heads/feat/demo',
            'commit': pin, 'tree': receipt['tree'],
            'source_path': receipt['path'], 'source_sha256': receipt['sha256'],
        }))
        target = f.root / '.worktrees/integration-b'
        sh(f.root, 'worktree', 'add', '-qb', 'feat/mod/demo/b', str(target), pin)
        f.fdir, f.issue, f.spec = b, b / 'issues' / f.issue.name, b / f.spec.name
        f.integration = target
        started = f.start()
        self.assertEqual(sh(f.source, 'rev-parse', 'HEAD'), pin)
        context = ticket_context.load_context(started['context'])
        self.assertEqual(context['stack_base']['commit'], pin)
        self.assertEqual(context['stack_base']['declaration_sha256'],
                         hashlib.sha256(stack.read_bytes()).hexdigest())
        previous = f.root / '.worktrees/integration-demo'
        (previous / 'next.py').write_text('next_version = True\n')
        sh(previous, 'add', 'next.py')
        sh(previous, 'commit', '-qm', 'predecessor advances')
        later = sh(previous, 'rev-parse', 'HEAD')
        f.implement()
        reopened = f.final_rebase()
        self.assertEqual(ticket_context.load_context(started['context'])['stack_base']['commit'], pin)
        self.assertEqual(reopened['predecessor_tip'], later)
        f.verify('implementer')
        import test_process_check
        log = b / 'impl/issue-001/impl-log-issue-001.md'
        log.write_text(test_process_check.LOG.replace('tests/test_dod.py', 't.py'))
        result = delivery.merge(f.context_path)
        self.assertEqual(result['status'], 'merged')
        self.assertEqual(sh(target, 'rev-parse', 'HEAD^1'), pin)
        self.assertEqual(sh(f.root, 'rev-parse', 'main'), main)
        self.assertEqual(sh(previous, 'rev-parse', 'HEAD'), later)
        from types import SimpleNamespace
        report = fc.freeze(SimpleNamespace(feature_dir=str(b), repo=str(target), round=None, issues=None))
        self.assertFalse(report.failed, str(report))
        candidate = json.loads((b / 'integration/candidate.json').read_text())
        self.assertEqual(candidate['stack_base']['commit'], pin)
        self.assertEqual(candidate['review_base'], pin)
        self.assertEqual(candidate['review_merge_base'], pin)
        aggregate = verify_issue.run_candidate(b / 'integration/candidate.json', target,
            host='zcode', agent='supervisor-review', session='candidate-review',
            model='provider/supervisor', effort='high')
        self.assertTrue(aggregate['all-passed'], aggregate)
        self.assertEqual(aggregate['stack_base']['commit'], pin)
        # Deleting the declaration cannot bypass the acceptance barrier.
        stack.unlink()
        with self.assertRaisesRegex(ValueError, 'stack-base'):
            f.final_rebase()
        refused = fc.freeze(SimpleNamespace(feature_dir=str(b), repo=str(target), round=None, issues=None))
        self.assertTrue(refused.failed)

    def test_stacked_campaign_closeout_review_main_drift_and_promotion_refusal(self):
        f = self.fixture(closeout_fixture.FeatureCloseoutTest)
        legacy = f.fdir
        pin = f.merged_commit
        receipt = delivery.stack_source(f.root, legacy, commit=pin)
        stacked = self.campaign(f.root, legacy, 'stack')
        (stacked / 'stack-base.json').write_text(json.dumps({
            'schema': 'agent-dev.stack-base/v1', 'consumer_scope': scopes.scope_key(stacked),
            'predecessor_scope': scopes.scope_key(legacy),
            'predecessor_ref': 'refs/heads/feat/demo',
            'commit': pin, 'tree': receipt['tree'],
            'source_path': receipt['path'], 'source_sha256': receipt['sha256'],
        }))
        swt = f.root / '.worktrees/integration-stack'
        delivery_fixture.sh(f.root, 'worktree', 'add', '-qb', 'feat/mod/demo/stack', str(swt), pin)
        (swt / 'src' / 'b.py').write_text('y = 2\n')
        delivery_fixture.sh(swt, 'add', '.')
        delivery_fixture.sh(swt, 'commit', '-qm', 'campaign issue-001')
        campaign_merge = delivery_fixture.sh(swt, 'rev-parse', 'HEAD')
        f.fdir = stacked
        f.wt = swt
        f.merged_commit = campaign_merge
        f.write_issue('done')
        issue = stacked / 'issues/issue-001-x.md'
        issue.write_text(issue.read_text().replace('feature-slug: demo',
                                                   'feature-slug: demo\ncampaign-id: stack'))
        f.write_conformance('conform')
        f.write_merged(campaign_merge)
        self.assertEqual(f.freeze().returncode, 0)
        candidate = f.candidate
        self.assertEqual(candidate['stack_base']['commit'], pin)
        self.assertEqual(candidate['review_base'], pin)
        # Independent main movement after the freeze is tolerated for stacked
        # candidates; the safety anchor stays the pinned predecessor.
        (f.root / 'main-only.py').write_text('main_advanced = True\n')
        delivery_fixture.sh(f.root, 'add', 'main-only.py')
        delivery_fixture.sh(f.root, 'commit', '-qm', 'main advances independently')
        f.write_evidence([f.auto_result(), f.human_result(True)])
        verification = stacked / 'integration/candidate-verification.json'
        value = json.loads(verification.read_text())
        value['stack_base'] = {'predecessor_scope': candidate['stack_base']['predecessor_scope'],
                               'predecessor_ref': candidate['stack_base']['predecessor_ref'],
                               'commit': pin,
                               'declaration_sha256': candidate['stack_base']['declaration_sha256']}
        verification.write_text(json.dumps(value))
        receipt_path = stacked / 'integration/feature-review-receipt.json'
        saved = json.loads(receipt_path.read_text())
        self.assertEqual(saved['base'], pin)
        self.assertEqual(saved['merge_base'], pin)
        self.assertEqual(saved['stack_base']['commit'], pin)
        f.confirm()
        self.assertEqual(f.check('merge').returncode, 0)
        # A receipt re-anchored onto main must fail the stacked gate.
        tampered = dict(saved)
        tampered['base'] = candidate['main_head']
        tampered['merge_base'] = candidate['merge_base']
        tampered.pop('stack_base', None)
        receipt_path.write_text(json.dumps(tampered))
        self.assertNotEqual(f.check('merge', generate_report=False).returncode, 0)
        receipt_path.write_text(json.dumps(saved))
        self.assertEqual(f.check('merge').returncode, 0)
        with self.assertRaisesRegex(delivery.DeliveryError, 'STACKED_ACCEPTANCE_UNAVAILABLE'):
            f.promote()

    def test_branch_identity_handles_existing_module_names_with_spaces(self):
        directory = Path('dev/features/data agent 核心流程/判读任务执行/campaigns/快测.回路')
        fm = {'feature-slug': '判读任务执行', 'campaign-id': '快测.回路'}
        branch = ticket_context.target_branch({'main-branch': 'main', 'feature-closeout': {'mode': 'local-human'}}, fm, directory)
        self.assertEqual(branch, 'feat/data%20agent%20核心流程/判读任务执行/快测%2E回路')
        self.assertIsNone(scopes.campaign_id(Path('dev/features/campaigns/a')))
        with self.assertRaisesRegex(ticket_context.ContextError, 'for isolation'):
            ticket_context.target_branch({'main-branch': 'main', 'feature-closeout': {'mode': 'local-human', 'integration-branch': 'feat/shared'}}, fm, directory)

        self.assertNotEqual(scopes.branch_key(directory), scopes.branch_key(Path('dev/features/data%20agent 核心流程/判读任务执行/campaigns/快测.回路')))

    def test_campaign_does_not_fall_back_to_parent_plan_or_accept_wrong_identity(self):
        f = self.fixture(delivery_fixture.DeliveryTest)
        a = self.campaign(f.root, f.fdir, 'a')
        issue = a / 'issues' / f.issue.name
        (a / 'acceptance-plan.yaml').unlink()
        self.assertFalse(ticket_context.check_plan(issue, f.root)['ok'])
        shutil.copy2(f.fdir / 'acceptance-plan.yaml', a / 'acceptance-plan.yaml')
        issue.write_text(issue.read_text().replace('campaign-id: a', 'campaign-id: b'))
        self.assertFalse(ticket_context.check_plan(issue, f.root)['ok'])

    def test_freeze_only_owns_campaign_tickets_and_plan(self):
        f = self.fixture(closeout_fixture.FeatureCloseoutTest)
        legacy = f.fdir
        a = self.campaign(f.root, legacy, 'a')
        b = self.campaign(f.root, legacy, 'b')
        b_issue = b / 'issues' / 'issue-001-x.md'
        b_issue.write_text(b_issue.read_text().replace('status: done', 'status: in-progress'))
        delivery_fixture.sh(f.wt, 'branch', '-m', 'feat/mod/demo/a')
        f.fdir = a
        result = f.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(f.candidate['feature'], 'mod/demo/campaigns/a')
        self.assertEqual([i['id'] for i in f.candidate['issues']], ['issue-001'])
        (b / 'acceptance-plan.yaml').write_text('broken\n')
        report = fc.Report()
        fc.check_plan_unchanged(report, a, f.candidate)
        self.assertFalse(report.failed)
        (a / 'acceptance-plan.yaml').write_text((a / 'acceptance-plan.yaml').read_text() + '\n')
        report = fc.Report()
        fc.check_plan_unchanged(report, a, f.candidate)
        self.assertTrue(report.failed)
        self.assertEqual(review_receipt._load_feature_config(f.wt, a)['main-branch'], 'main')

    def test_small_change_explicitly_binds_shared_baseline_not_peer_same_name(self):
        f = self.fixture(closeout_fixture.FeatureCloseoutTest)
        a = self.campaign(f.root, f.fdir, 'a')
        f.fdir = a
        baseline = f.prd
        f.docs = f.docs / 'campaigns/a'
        f.docs.mkdir(parents=True)
        f.write_change_request(retain='[AC-2]')
        f.change_request.write_text(f.change_request.read_text().replace(baseline.name, baseline.relative_to(f.root).as_posix()))
        delta = closeout_fixture.command_case()
        delta['source'] = {'document': 'change_request', 'anchor': 'AC-D1'}
        f.cases = {'AC-D1': delta, 'AC-2': closeout_fixture.human_case()}
        f.write_plan()
        self.assertEqual(fc.validate_plan_sources(a, fc.read_plan(a)), [])
        peer = f.docs.parent / 'b' / baseline.name
        peer.parent.mkdir()
        shutil.copy2(baseline, peer)
        plan = fc.read_plan(a)
        plan['documents']['prd']['path'] = peer.relative_to(f.root).as_posix()
        self.assertTrue(any('exact documents.prd.path' in error for error in fc.validate_plan_sources(a, plan)))

    def test_campaign_round_is_not_another_branch(self):
        f = self.fixture(closeout_fixture.FeatureCloseoutTest)
        a = self.campaign(f.root, f.fdir, 'a')
        delivery_fixture.sh(f.wt, 'branch', '-m', 'feat/mod/demo/a')
        result = f.run_cli('freeze', '--feature-dir', str(a), '--repo', str(f.wt),
                          '--round', 'retry-2', '--issues', 'issue-001')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        candidate = json.loads((a / 'integration/retry-2/candidate.json').read_text())
        self.assertEqual(candidate['integration_branch'], 'feat/mod/demo/a')
        self.assertEqual(candidate['round'], 'retry-2')

    def test_decisions_and_discovery_ignore_sibling_and_archive(self):
        f = self.fixture(decision_fixture.HumanDecisionTest)
        original = f.issue_path.read_text()
        for name in ('a', 'b'):
            directory = f.feature_dir / 'campaigns' / name
            path = directory / 'issues' / f.issue_path.name
            path.parent.mkdir(parents=True)
            path.write_text(original)
            envelope = f.envelope()
            envelope['scope']['campaign'] = name
            hd.request(f.repo, envelope, main_session=True, now=decision_fixture.T0)
        self.assertEqual(len(hd.list(f.repo, active=True)), 2)
        self.assertEqual(len(hd.list(f.repo, feature='mod/slug/campaigns/a', active=True)), 1)
        self.assertEqual(hd.list(f.repo, feature='mod/slug', active=True), [])
        self.assertEqual(len(hd.classify(f.repo)['issues']), 3)
        archive = f.feature_dir / 'research' / 'old' / 'decisions'
        archive.mkdir(parents=True)
        packet = hd.list(f.repo, feature='mod/slug/campaigns/a')[0]
        (archive / 'copy.json').write_text(json.dumps(packet))
        self.assertEqual(len(hd.list(f.repo, active=True)), 2)
        # No need for a config to exercise the authoritative file guard.
        from unittest.mock import patch
        with patch.object(fc.importlib, 'import_module', side_effect=ImportError):
            self.assertEqual(fc._active_decision_records(f.feature_dir), [])
            self.assertEqual(len(fc._active_decision_records(f.feature_dir / 'campaigns/a')), 1)

    def test_legacy_cleanup_and_contract_scan_do_not_merge_campaign_namespaces(self):
        f = self.fixture(gc_fixture.ArtifactGCTest)
        for generation, status, prior in [('g1', 'superseded', None), ('g2', 'superseded', 'g1'), ('g3', 'current', 'g2')]:
            f.manifest(generation, status, prior)
        feature = f.root / 'dev/features/mod/slug'
        campaign = feature / 'campaigns/a'
        shutil.copytree(feature / 'impl', campaign / 'impl')
        old = artifact_gc.preview_generations(feature)
        self.assertTrue(old['candidates'])
        self.assertFalse(any('/campaigns/' in item['abs'] for item in old['candidates']))
        self.assertTrue(artifact_gc.preview_generations(campaign)['candidates'])
        # A missing supersedes target cannot be satisfied by a peer's same basename.
        other = feature / 'campaigns/b'
        other.mkdir(parents=True)
        (campaign / 'a-tech-spec-2026-11-20.md').write_text('---\nsupersedes: old-tech-spec-2026-11-19.md\n---\n')
        (other / 'old-tech-spec-2026-11-19.md').write_text('---\nstatus: draft\n---\n')
        results = []
        contract_check.check_spec1(f.root, results)
        self.assertTrue(any('missing tech-spec' in row['message'] for row in results))

    def test_session_binding_keeps_campaign_and_rejects_ambiguous_handoff(self):
        f = self.fixture(delivery_fixture.DeliveryTest)
        for name in ('a', 'b'):
            directory = self.campaign(f.root, f.fdir, name)
            issue = directory / 'issues' / f.issue.name
            binding = session_title.normalize_binding(f.root, issue=str(issue))
            self.assertEqual(binding['feature'], 'docs/features/mod/demo/campaigns/' + name)
            self.assertEqual(session_title.feature_paths(binding)[2], directory.resolve())
        self.assertIsNone(session_title.infer_handoff_binding({'prompt': 'feature-slug: demo issue-001'}, f.root))
        bound = session_title.infer_handoff_binding({'prompt': 'feature-slug: demo campaign-id: a issue-001'}, f.root)
        self.assertEqual(bound['feature'], 'docs/features/mod/demo/campaigns/a')


if __name__ == '__main__':
    unittest.main()
