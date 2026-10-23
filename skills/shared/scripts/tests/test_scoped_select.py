#!/usr/bin/env python3
"""scoped_select.py 契约测试：合成小仓验证 import 反向闭包（含函数内惰性 import）、脚本引用、
dir-map 数据映射、忽略清单、enumerable 判定、deferred 语义、git diff 取变更与 --run 执行。

运行：python3 -m unittest discover -s skills/shared/scripts/tests -p "test_scoped_select.py" -v
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "scoped_select.py"
sys.path.insert(0, str(SCRIPT.parent))
import scoped_select

FILES = {
    'skills/s1/scripts/core.py': 'def f():\n    return 1\n',
    # 惰性 import：只在函数体内出现
    'skills/s1/scripts/lazy.py': 'def g():\n    import core\n    return core.f()\n',
    'skills/s1/scripts/cli.py': 'from lazy import g\n',
    'skills/s1/scripts/standalone.py': 'print("hi")\n',
    'skills/s1/scripts/pkg/__init__.py': '',
    'skills/s1/scripts/pkg/inner.py': 'from . import sibling\n',
    'skills/s1/scripts/pkg/sibling.py': 'X = 1\n',
    # 字面量 import_module 当作静态边
    'tools/helper.py': 'import importlib\nimportlib.import_module("core")\n',
    'tools/tests/a_smoke.py': ('import sys\nsys.path.insert(0, "skills/s1/scripts")\n'
                               'import cli\n'),
    'tools/tests/b_smoke.py': 'def t():\n    import helper\n',
    'tools/tests/c_smoke.py': ('import subprocess\n'
                               'subprocess.run(["python3", "skills/s1/scripts/standalone.py"])\n'),
    'tools/tests/d_smoke.py': 'print("unrelated")\n',
    'tools/tests/e_smoke.sh': 'cat method/x/rules.yaml\n',
    'tools/tests/f_smoke.py': 'DIR = "special_dir"\n',
    'tools/tests/g_smoke.py': 'import pkg.inner\n',
    'method/x/rules.yaml': 'a: 1\n',
    'method/special_dir/foo.json': '{}\n',
}


def build(root, files=FILES):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class SyntheticRepo(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        build(self.root)

    def pick(self, *changed):
        return scoped_select.select(self.root, list(changed))

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scoped_select.main(['--repo', str(self.root), *argv])
        return code, json.loads(out.getvalue())

    def test_reverse_closure_includes_lazy_imports(self):
        result = self.pick('skills/s1/scripts/core.py')
        # core ← lazy(函数内 import) ← cli ← a_smoke；core ← helper(import_module 字面量) ← b_smoke(函数内)
        self.assertEqual(result['selected'], ['a_smoke.py', 'b_smoke.py'])
        self.assertTrue(result['enumerable'])
        self.assertEqual(result['total'], 7)
        self.assertEqual(result['ratio'], round(2 / 7, 4))
        self.assertEqual(result['text_match'], {})

    def test_leaf_change_only_selects_its_dependents(self):
        self.assertEqual(self.pick('skills/s1/scripts/cli.py')['selected'], ['a_smoke.py'])
        self.assertEqual(self.pick('tools/helper.py')['selected'], ['b_smoke.py'])

    def test_script_path_literal_is_an_edge(self):
        self.assertEqual(self.pick('skills/s1/scripts/standalone.py')['selected'], ['c_smoke.py'])

    def test_package_relative_import(self):
        self.assertEqual(self.pick('skills/s1/scripts/pkg/sibling.py')['selected'], ['g_smoke.py'])

    def test_changed_test_is_selected_directly(self):
        result = self.pick('tools/tests/d_smoke.py')
        self.assertEqual(result['selected'], ['d_smoke.py'])
        self.assertEqual(result['direct'], ['d_smoke.py'])

    def test_helper_tests_excluded_via_categories(self):
        # 块形态分类清单：gate: helper 的 tests 不进测试清单与选择集
        build(self.root, {'tools/test_categories.yaml':
                          'schema: v1\ncategories:\n  helper:\n    gate: helper\n    tests:\n'
                          '      - b_smoke.py\n  env-x:\n    gate: env\n    tests:\n      - a_smoke.py\n'})
        result = self.pick('tools/helper.py')
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['total'], 6)  # b_smoke.py 被排除
        # 内联形态：{gate: helper, tests: [...]}
        build(self.root, {'tools/test_categories.yaml':
                          'categories:\n  helper: {gate: helper, tests: [a_smoke.py]}\n'})
        self.assertEqual(self.pick('skills/s1/scripts/cli.py')['selected'], [])
        # 无分类清单时不排除
        (self.root / 'tools/test_categories.yaml').unlink()
        self.assertEqual(self.pick('tools/helper.py')['selected'], ['b_smoke.py'])

    def test_dir_map_covers_data_files(self):
        # dir-map 命中的数据文件：入选且视为可证明覆盖（不再触发 uncertain/defer）
        build(self.root, {'dev/scoped-map.yaml':
                          '- glob: "method/x/**"\n  tests:\n    - e_smoke.sh\n'})
        result = self.pick('method/x/rules.yaml')
        self.assertEqual(result['selected'], ['e_smoke.sh'])
        self.assertTrue(result['enumerable'])
        self.assertEqual(result['counts']['dir_map'], 1)
        self.assertEqual(result['text_match'], {})
        self.assertEqual(result['dir_map'], {'e_smoke.sh': ['method/x/rules.yaml']})

    def test_unmapped_data_uses_text_match_but_not_proven(self):
        result = self.pick('method/special_dir/foo.json')
        self.assertEqual(result['selected'], ['f_smoke.py'])
        self.assertFalse(result['enumerable'])
        self.assertTrue(any('foo.json' in r for r in result['reasons']))

    def test_meta_and_docs_are_ignored(self):
        # 仓库元信息 / dev / docs：无行为消费者，不需要覆盖证明（也不产生 uncertain）
        result = self.pick('.gitignore')
        self.assertEqual(result['selected'], [])
        self.assertTrue(result['enumerable'])
        self.assertEqual(result['reasons'], ['没有任何测试命中变更文件'])
        self.assertTrue(self.pick('docs/readme.md')['enumerable'])
        self.assertTrue(self.pick('dev/impl-log.md')['enumerable'])

    def test_unmatched_change_reports_reason(self):
        result = self.pick('unmatched/data.yaml')
        self.assertEqual(result['selected'], [])
        self.assertTrue(result['reasons'])
        self.assertFalse(result['enumerable'])

    def test_dynamic_loading_makes_selection_non_enumerable(self):
        build(self.root, {'skills/s1/scripts/orphan.py': 'X = 1\n'})
        self.assertFalse(self.pick('skills/s1/scripts/orphan.py')['enumerable'])
        build(self.root, {'tools/loader.py': 'import importlib\n'
                                             'def load(name):\n    return importlib.import_module(name)\n'})
        result = self.pick('skills/s1/scripts/orphan.py')
        self.assertFalse(result['enumerable'])
        self.assertIn('tools/loader.py', result['dynamic_sites'])
        self.assertTrue(any('orphan.py' in r for r in result['reasons']))
        self.assertTrue(self.pick('skills/s1/scripts/core.py')['enumerable'])

    def test_deleted_code_and_parse_error_force_full_fallback(self):
        (self.root / 'skills/s1/scripts/core.py').unlink()
        deleted = self.pick('skills/s1/scripts/core.py')
        self.assertFalse(deleted['enumerable'])
        self.assertTrue(any('已删除代码' in reason for reason in deleted['reasons']))
        build(self.root, {'skills/s1/scripts/core.py': 'def broken(:\n'})
        parsed = self.pick('skills/s1/scripts/core.py')
        self.assertFalse(parsed['enumerable'])
        self.assertTrue(any('解析失败' in reason for reason in parsed['reasons']))

    def test_run_non_enumerable_defers_unless_forced(self):
        # 可执行测试：运行时写 marker，验证 deferred 什么都没跑
        build(self.root, {'tools/tests/m1.py': 'open("m1.ran", "w").write("1")\n',
                          'tools/tests/m2.py': 'open("m2.ran", "w").write("1")\n'})
        code, report = self.run_cli('--run', '--changed', 'unmatched.md')
        self.assertEqual(code, 0)
        self.assertEqual(report['deferred'], 'closure-incomplete')
        self.assertEqual(report['execution_scope'], 'full_fallback')
        self.assertEqual(report['ran'], [])
        self.assertFalse((self.root / 'tools/tests/m1.ran').exists())
        # --fallback-run：全量执行（含 synthetic 里会失败的惰性 import 测试，broken 如实列）
        code, report = self.run_cli('--run', '--fallback-run', '--changed', 'unmatched.md')
        self.assertEqual(code, 1)
        self.assertNotIn('deferred', report)
        self.assertTrue((self.root / 'tools/tests/m1.ran').exists())
        self.assertIn('m1.py', [r['name'] for r in report['ran']])

    def test_run_selected_executes_and_reports_broken(self):
        build(self.root, {'tools/tests/ok1.py': 'print("ok")\n',
                          'tools/tests/bad1.py': 'raise SystemExit(3)\n'})
        code, report = self.run_cli('--run', '--changed', 'tools/tests/ok1.py',
                                    '--changed', 'tools/tests/bad1.py')
        self.assertEqual(code, 1)
        self.assertEqual(report['execution_scope'], 'selected')
        self.assertEqual(report['broken'], ['bad1.py'])
        self.assertEqual(sorted(r['name'] for r in report['ran']), ['bad1.py', 'ok1.py'])

    def test_git_diff_input(self):
        env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
               'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}

        def git(*args):
            subprocess.run(['git', *args], cwd=self.root, env=env, check=True,
                           capture_output=True)
        git('init', '-q')
        git('add', '-A')
        git('commit', '-q', '-m', 'base')
        (self.root / 'skills/s1/scripts/cli.py').write_text('from lazy import g\n# edit\n')
        git('commit', '-q', '-am', 'edit')
        code, result = self.run_cli('--base', 'HEAD~1')
        self.assertEqual(code, 0)
        self.assertEqual(result['changed'], ['skills/s1/scripts/cli.py'])
        self.assertEqual(result['selected'], ['a_smoke.py'])

        # 暂存区与工作树可以不一致，两边都要计入
        (self.root / 'tools/helper.py').write_text('import importlib\n# staged\n')
        git('add', 'tools/helper.py')
        (self.root / 'tools/helper.py').write_text(FILES['tools/helper.py'])
        (self.root / 'skills/s1/scripts/lazy.py').write_text('def g():\n    import core\n    return 2\n')
        (self.root / 'tools/tests/new name\nsmoke.py').write_text('pass\n')
        (self.root / 'skills/s1/scripts/pkg/inner.py').rename(
            self.root / 'skills/s1/scripts/pkg/renamed.py')
        changed = scoped_select.changed_files(self.root, 'HEAD~1')
        self.assertEqual(changed, sorted([
            'skills/s1/scripts/cli.py', 'skills/s1/scripts/lazy.py',
            'skills/s1/scripts/pkg/inner.py', 'skills/s1/scripts/pkg/renamed.py',
            'tools/helper.py', 'tools/tests/new name\nsmoke.py']))


if __name__ == '__main__':
    unittest.main()
