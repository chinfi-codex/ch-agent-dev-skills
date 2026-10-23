#!/usr/bin/env python3
"""通用 scoped 选择器（scoped 档关联测试选择，仓库无关）。

做什么：合并提交差异、暂存/未暂存差异及未跟踪文件，对扫描根下的 .py 建静态 import 图，
求变更模块的反向传递闭包；数据 / 配置文件查 dir-map 声明映射；得出受影响测试集（选择集）。

为什么按 AST 而不是 grep import：import 常写在函数体内（惰性导入），测试又常靠
`sys.path[:0] = [...]` 按裸模块名导入；这两种形态都由 ast.walk 全树扫描覆盖。

规则：
- 变更的是测试本身：直接入选。
- 变更的是 .py：模块 → 反向闭包里所有测试入选；字符串字面量里的脚本路径（.py/.sh 结尾）
  按「脚本引用」边处理。
- 变更的不是 .py（yaml/json/md/sh 等）：先查 dir-map（默认 <repo>/dev/scoped-map.yaml，
  数据路径 glob → 测试清单的声明式契约），命中即入选并视为可证明覆盖；
  未命中且属无行为消费者清单（仓库元信息 / dev/ / docs/）→ 不要求覆盖证明；
  否则退文本匹配（仅提示，不算可证明）。
- 已删除代码、解析失败、没有可证明覆盖的代码、存在动态加载点且变更模块无静态入边：
  输出 enumerable: false。--run 对此输出 deferred 不执行（套件级验证由集成票 full /
  每日回归兜底，evidence 记 scoped-deferred: closure-incomplete）；--fallback-run 强制全量。

用法：
  python3 scoped_select.py --repo <仓> [--base REV | --changed F ...] [--list | --run]
      [--jobs N] [--timeout S] [--map PATH] [--scan DIR ...] [--test-dir DIR] [--fallback-run]
退出码：--list 恒 0；--run 有失败即 1，deferred 恒 0。只依赖 Python 3.8+ 标准库。
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKIP_PARTS = {'__pycache__', 'node_modules', '.git', '.venv', 'venv', 'dist', 'runs'}
# 目录名太泛，作文本匹配关键词会选中几乎所有测试。
GENERIC_DIRS = {'tools', 'skills', 'scripts', 'tests', 'docs', 'dev', 'method', 'protocol',
                'reports', 'references', 'configuration', 'missions', '.', ''}
DYNAMIC_CALLS = {'import_module', '__import__', 'run_path', 'run_module'}
# 无行为消费者、不需要覆盖证明的仓内文件（dir-map 映射优先于本清单）。
IGNORE_EXACT = {'.gitignore', '.gitattributes', '.editorconfig', 'LICENSE'}
IGNORE_PREFIX = ('dev/', 'docs/')
TEST_SUFFIX = {'.py', '.sh'}


def _git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True)


def default_base(repo):
    for upstream in ('main', 'origin/main', 'master'):
        result = _git(repo, 'merge-base', upstream, 'HEAD')
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.decode().strip()
    return 'HEAD~1'


def changed_files(repo, base):
    changed = set()
    for args in (('diff', '--name-only', '-z', '--no-renames', f'{base}..HEAD'),
                 ('diff', '--cached', '--name-only', '-z', '--no-renames', 'HEAD'),
                 ('diff', '--name-only', '-z', '--no-renames'),
                 ('ls-files', '--others', '--exclude-standard', '-z')):
        result = _git(repo, *args)
        if result.returncode != 0:
            raise RuntimeError(f'git {args[0]} 失败：{result.stderr.decode(errors="replace").strip()}')
        changed.update(p.decode('utf-8', errors='surrogateescape')
                       for p in result.stdout.split(b'\0') if p)
    return sorted(changed)


def scan_files(repo, scan_roots):
    """图节点：扫描根下的 .py。"""
    files = []
    for top in scan_roots:
        base = repo / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*.py')):
            rel = path.relative_to(repo)
            if SKIP_PARTS & set(rel.parts[:-1]):
                continue
            files.append(rel.as_posix())
    return files


def test_files(repo, test_dir):
    """测试清单：test-dir 下的 .py / .sh（相对 test-dir 的名字）。"""
    base = repo / test_dir
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_file() and p.suffix in TEST_SUFFIX)


def module_keys(rel):
    parts = list(Path(rel).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts = parts[:-1]
    keys = []
    for i in range(len(parts) - 1, -1, -1):
        if not all(p.isidentifier() for p in parts[i:]):
            break
        keys.append('.'.join(parts[i:]))
    return keys


class Graph:
    def __init__(self, repo, files):
        self.repo = repo
        self.index = {}
        for rel in files:
            for key in module_keys(rel):
                self.index.setdefault(key, set()).add(rel)
        self.by_base = {}
        for rel in files:
            self.by_base.setdefault(Path(rel).name, set()).add(rel)
        self.edges = {rel: set() for rel in files}
        self.dynamic = {}
        self.parse_errors = {}
        for rel in files:
            self._scan(rel)

    def _targets(self, dotted):
        found = set()
        parts = dotted.split('.')
        for i in range(len(parts), 0, -1):
            found |= self.index.get('.'.join(parts[:i]), set())
        return found

    def _relative(self, rel, level, module):
        base = (self.repo / rel).parent
        for _ in range(level - 1):
            base = base.parent
        target = base.joinpath(*module.split('.')) if module else base
        found = set()
        for cand in (target.with_suffix('.py'), target / '__init__.py'):
            try:
                r = cand.relative_to(self.repo).as_posix()
            except ValueError:
                continue
            if r in self.edges:
                found.add(r)
        return found

    def _scan(self, rel):
        try:
            tree = ast.parse((self.repo / rel).read_text(encoding='utf-8'), filename=rel)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            self.parse_errors[rel] = type(exc).__name__
            return
        deps = self.edges[rel]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    deps |= self._targets(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    deps |= self._relative(rel, node.level, node.module or '')
                    for alias in node.names:
                        deps |= self._relative(rel, node.level,
                                               '.'.join(filter(None, [node.module, alias.name])))
                elif node.module:
                    deps |= self._targets(node.module)
                    for alias in node.names:
                        deps |= self.index.get(f'{node.module}.{alias.name}', set())
            elif isinstance(node, ast.Call):
                self._call(rel, node, deps)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                deps |= self._script_ref(node.value)
        deps.discard(rel)

    def _call(self, rel, node, deps):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
        if name not in DYNAMIC_CALLS:
            return
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if name == 'run_path':
                deps |= self._script_ref(arg.value)
            else:
                deps |= self._targets(arg.value)
        else:
            self.dynamic.setdefault(rel, []).append(f'{name}(非字面量) 行 {node.lineno}')

    def _script_ref(self, text):
        if not text.endswith(('.py', '.sh')) or len(text) > 300:
            return set()
        hits = self.by_base.get(Path(text).name, set())
        if '/' in text:
            tail = text.lstrip('./')
            hits = {h for h in hits if h.endswith(tail)}
        return set(hits)

    def reverse_closure(self, seeds):
        reverse = {}
        for src, dsts in self.edges.items():
            for dst in dsts:
                reverse.setdefault(dst, set()).add(src)
        seen, stack = set(seeds), list(seeds)
        while stack:
            for parent in reverse.get(stack.pop(), ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def incoming(self, rel):
        return any(rel in dsts for src, dsts in self.edges.items() if src != rel)


def text_matches(repo, test_dir, tests, changed_rel):
    needles = [Path(changed_rel).name]
    parent = Path(changed_rel).parent.name
    if parent not in GENERIC_DIRS:
        needles.append(parent)
    hit = []
    for name in tests:
        text = (repo / test_dir / name).read_text(encoding='utf-8', errors='replace')
        if any(re.search(r'(?<![\w.-])' + re.escape(n) + r'(?![\w-])', text) for n in needles):
            hit.append(name)
    return hit


def load_dir_map(path):
    """读 dir-map（- glob: / tests: / - name 扁平结构）；文件缺失返回空表。"""
    if not path.is_file():
        return []
    entries, current, in_tests = [], None, False
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r'^- glob:\s*["\']?(.+?)["\']?\s*$', line.strip())
        if m:
            current = {'glob': m.group(1), 'tests': []}
            entries.append(current)
            in_tests = False
            continue
        if line.strip() == 'tests:':
            in_tests = True
            continue
        m = re.match(r'^-\s+(\S+)\s*$', line.strip())
        if m and in_tests and current is not None:
            current['tests'].append(m.group(1))
    return entries


def dir_map_matches(entries, changed_rel):
    hit = []
    for e in entries:
        g = e['glob']
        prefix = g[:-2] if g.endswith('**') else g
        if changed_rel.startswith(prefix) or changed_rel == prefix.rstrip('/'):
            hit.extend(e['tests'])
    return hit


def select(repo, changed, scan_roots=('tools', 'skills'), test_dir='tools/tests', map_path=None):
    """返回选择结果字典（不执行）。"""
    repo = Path(repo).resolve()
    files = scan_files(repo, scan_roots)
    graph = Graph(repo, files)
    tests = test_files(repo, test_dir)
    test_rels = {f'{test_dir}/{t}' for t in tests}
    dir_map = load_dir_map(map_path or (repo / 'dev' / 'scoped-map.yaml'))
    direct, seeds, text, mapped, reasons = set(), set(), {}, {}, []
    uncertain = []
    for rel in changed:
        path = repo / rel
        if rel in test_rels:
            direct.add(Path(rel).name)
        if rel in graph.edges:
            seeds.add(rel)
        elif rel not in test_rels:
            hits = dir_map_matches(dir_map, rel)
            if hits:
                for name in hits:
                    if name in test_rels or (repo / test_dir / name).is_file():
                        mapped.setdefault(name, []).append(rel)
            elif rel in IGNORE_EXACT or rel.startswith(IGNORE_PREFIX):
                pass
            else:
                for name in text_matches(repo, test_dir, tests, rel):
                    text.setdefault(name, []).append(rel)
                # Text matching is a hint, never proof of complete coverage.
                uncertain.append(f'{rel}: 静态图外且未命中 dir-map' + ('（已删除）' if not path.exists() else ''))
        if rel.endswith('.py') and not path.is_file():
            uncertain.append(f'{rel}: 已删除代码')
    closure = graph.reverse_closure(seeds)
    by_import = {Path(r).name for r in closure if r in test_rels}
    selected = sorted(direct | by_import | set(mapped) | set(text))
    unreferenced = sorted(s for s in seeds if s not in test_rels and not graph.incoming(s))
    enumerable = not uncertain and not graph.parse_errors
    reasons.extend(uncertain)
    if graph.parse_errors:
        reasons.append('源码解析失败：' + '、'.join(sorted(graph.parse_errors)))
    covered = set(test_rels)
    stack = list(test_rels)
    while stack:
        for dependency in graph.edges.get(stack.pop(), ()):
            if dependency not in covered:
                covered.add(dependency)
                stack.append(dependency)
    uncovered = sorted(seeds - covered)
    if uncovered:
        enumerable = False
        reasons.append('代码变更没有可证明的测试覆盖：' + '、'.join(uncovered))
    if unreferenced and graph.dynamic:
        enumerable = False
        sites = sorted(graph.dynamic)
        reasons.append('变更模块无静态入边，且仓库存在无法静态确定目标的动态加载点：'
                       + '、'.join(unreferenced) + ' ← 动态点位于 ' + '、'.join(sites[:8]))
    if not selected:
        reasons.append('没有任何测试命中变更文件')
    return {'changed': list(changed), 'selected': selected, 'total': len(tests),
            'ratio': round(len(selected) / len(tests), 4) if tests else 0.0,
            'enumerable': enumerable, 'reasons': reasons,
            'counts': {'direct': len(direct), 'import_closure': len(by_import - direct),
                       'dir_map': len(set(mapped) - by_import - direct),
                       'text_match': len(set(text) - by_import - direct - set(mapped))},
            'direct': sorted(direct),
            'dir_map': {k: sorted(v) for k, v in sorted(mapped.items())
                        if k not in by_import and k not in direct},
            'text_match': {k: sorted(v) for k, v in sorted(text.items())
                           if k not in by_import and k not in direct and k not in mapped},
            'dynamic_sites': {k: v for k, v in sorted(graph.dynamic.items())}}


def run_tests(repo, test_dir, names, jobs, timeout):
    """通用执行：每个测试一个子进程（.py 用当前解释器、.sh 用 bash），并行 jobs。"""
    def one(name):
        cmd = [sys.executable, name] if name.endswith('.py') else ['bash', name]
        try:
            r = subprocess.run(cmd, cwd=repo / test_dir, capture_output=True, timeout=timeout)
            return {'name': name, 'exit': r.returncode}
        except subprocess.TimeoutExpired:
            return {'name': name, 'exit': 'timeout'}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        ran = list(pool.map(one, names))
    return ran


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='scoped_select.py',
        description='通用 scoped 选择器：import 反向闭包 + dir-map 数据映射；不可枚举不跑全量')
    ap.add_argument('--repo', default='.', help='仓库根（默认当前目录）')
    ap.add_argument('--base', help='比较基线 rev，默认 main 与 HEAD 的 merge-base')
    ap.add_argument('--changed', action='append', default=[], help='直接给变更文件（可重复），不读 git diff')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='只列选择集（默认）')
    mode.add_argument('--run', action='store_true', help='并行执行选择集')
    ap.add_argument('--jobs', type=int, default=6, help='并发数，默认 6')
    ap.add_argument('--timeout', type=int, default=300, help='单项超时秒数，默认 300')
    ap.add_argument('--map', dest='map_path', help='dir-map 路径，默认 <repo>/dev/scoped-map.yaml')
    ap.add_argument('--scan', action='append', default=[], help='import 图扫描根（可重复，默认 tools skills）')
    ap.add_argument('--test-dir', default='tools/tests', help='测试目录，默认 tools/tests')
    ap.add_argument('--fallback-run', action='store_true',
                    help='闭包不可证明时仍全量执行（默认 deferred 不执行，由集成票/每日回归兜底）')
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    scan_roots = tuple(args.scan) or ('tools', 'skills')
    map_path = Path(args.map_path) if args.map_path else None
    if args.changed:
        base, changed = None, sorted(args.changed)
    else:
        base = args.base or default_base(repo)
        changed = changed_files(repo, base)
    result = {'base': base, **select(repo, changed, scan_roots, args.test_dir, map_path)}
    result['execution_scope'] = ('full_fallback' if not result['enumerable'] else
                                 'selected' if result['selected'] else 'empty')

    if not args.run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not result['enumerable'] and not args.fallback_run:
        # 闭包不可证明时全量 = 变相每票 full；改为延期：不执行、如实输出，
        # 套件级验证由集成票 full 与每日回归兜底（调用方记 evidence scoped-deferred）。
        result['deferred'] = 'closure-incomplete'
        result['ran'] = []
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    names = result['selected'] if result['enumerable'] else test_files(repo, args.test_dir)
    ran = run_tests(repo, args.test_dir, names, max(1, args.jobs), args.timeout)
    result['ran'] = ran
    result['broken'] = [r['name'] for r in ran if r['exit'] != 0]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['broken'] else 0


if __name__ == '__main__':
    sys.exit(main())
