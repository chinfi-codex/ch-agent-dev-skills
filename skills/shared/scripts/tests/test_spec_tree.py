#!/usr/bin/env python3
"""§3.1 tree parsing, slice projection and slice CLI contract tests."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spec_tree as st


SCRIPT = Path(__file__).resolve().parent.parent / "spec_tree.py"

TREE = """\
<repo>/
  services/order/                      # 订单模块：本次改动主场
    order_service.py      [修改] close_order() 增加幂等键校验，回调重试不再重复关单
    idempotency.py        [新增] 幂等键生成与校验工具
  tests/order/
    test_order_close.py   [修改] 新增幂等重试场景 2 条
  docs/ARCH-CHECK.json   [修改] 新增幂等规则；由承接票更新规则并 render write
"""

TREE_ERROR_CASES = {
    "missing action": "  a.py 修复幂等问题\n",
    "unknown action": "  a.py [重构] 改写幂等\n",
    "missing note": "  a.py [修改]\n",
    "glob path": "  a*.py [修改] 说明\n",
    "directory action": "  services/ [修改] 说明\n",
    "duplicate path": "  a.py [修改] 一\n  a.py [修改] 二\n",
    "dotdot segment": "  ../escape.py [修改] 越界\n",
    "stray line": "  这是裸文本说明行\n",
    "tabs": "\ta.py [修改] 说明\n",
}


def spec_with_tree(tree: str, version: int = 1) -> str:
    head = "---\nstatus: 已确认（待拆解）\n"
    if version is not None:
        head += "spec-tree-version: %d\n" % version
    return head + "---\n\n# Spec\n\n### 3.1 模块拆解\n\n```text\n%s```\n" % tree


class SpecTreeParseTest(unittest.TestCase):
    def test_parse_template_style_tree(self):
        entries = st.parse_tree(spec_with_tree(TREE))
        self.assertEqual([entry["path"] for entry in entries],
                         ["services/order/order_service.py", "services/order/idempotency.py",
                          "tests/order/test_order_close.py", "docs/ARCH-CHECK.json"])
        self.assertEqual(entries[0]["action"], "修改")
        self.assertIn("幂等键校验", entries[0]["note"])
        self.assertEqual(entries[1]["action"], "新增")

    def test_missing_31_section_or_tree_block_returns_none(self):
        self.assertIsNone(st.parse_tree("# Spec\n\n## 1. 目标\n"))
        self.assertIsNone(st.parse_tree(spec_with_tree(TREE).replace("```text", "```yaml")))

    def test_root_prefix_and_decoration_styles(self):
        entries = st.parse_tree(spec_with_tree("myrepo/\n  myrepo/src/\n    ├── a.py [新增] 首个文件\n    └── b.py [修改] 第二个文件\n"))
        self.assertEqual([entry["path"] for entry in entries], ["src/a.py", "src/b.py"])
        entries = st.parse_tree(spec_with_tree("a.py [修改] 根级文件\n  nested/\n    inner.py [删除] 删除内部实现\n"))
        self.assertEqual([entry["path"] for entry in entries], ["a.py", "nested/inner.py"])

    def test_note_free_text_with_brackets_and_hash(self):
        entries = st.parse_tree(spec_with_tree("a.py [修改] 关单逻辑 # 备注含 [修改] 字样\n"))
        self.assertEqual(entries[0]["note"], "关单逻辑 # 备注含 [修改] 字样")

    def test_directory_lines_build_hierarchy_without_entering_files(self):
        entries = st.parse_tree(spec_with_tree("<repo>/\n  services/\n    a.py [新增] 一\n"))
        self.assertEqual([entry["path"] for entry in entries], ["services/a.py"])

    def test_malformed_trees_raise(self):
        for name, tree in TREE_ERROR_CASES.items():
            with self.subTest(case=name):
                with self.assertRaises(st.SpecTreeError):
                    st.parse_tree(spec_with_tree(tree))

    def test_unterminated_and_multiple_blocks_raise(self):
        with self.assertRaisesRegex(st.SpecTreeError, "unterminated"):
            st.parse_tree(spec_with_tree(TREE)[:-4])
        with self.assertRaisesRegex(st.SpecTreeError, "exactly one"):
            st.parse_tree(spec_with_tree(TREE + "extra.py [新增] 同节第二块\n```\n\n```text\nx.py [新增] 重复块\n"))

    def test_spec_tree_version_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy.md"
            legacy.write_text("# Spec\n\n### 3.1 模块拆解\n\n```text\n%s```\n" % TREE, encoding="utf-8")
            self.assertIsNone(st.spec_tree_version(legacy))
            bad = Path(tmp) / "bad.md"
            bad.write_text(spec_with_tree(TREE).replace("spec-tree-version: 1", "spec-tree-version: 2"), encoding="utf-8")
            with self.assertRaisesRegex(st.SpecTreeError, "unsupported"):
                st.spec_tree_version(bad)
            good = Path(tmp) / "good.md"
            good.write_text(spec_with_tree(TREE), encoding="utf-8")
            self.assertEqual(st.spec_tree_version(good), 1)


class ProjectionAndSliceTest(unittest.TestCase):
    ENTRIES = st.parse_tree(spec_with_tree(TREE))

    def test_projection_matches_globs_and_keeps_spec_order(self):
        lines = st.projection(self.ENTRIES, ["services/**", "tests/**"])
        self.assertEqual(lines, ["services/order/order_service.py [修改] close_order() 增加幂等键校验，回调重试不再重复关单",
                                 "services/order/idempotency.py [新增] 幂等键生成与校验工具",
                                 "tests/order/test_order_close.py [修改] 新增幂等重试场景 2 条"])
        lines = st.projection(self.ENTRIES, ["docs/ARCH-CHECK.json"])
        self.assertEqual(lines, ["docs/ARCH-CHECK.json [修改] 新增幂等规则；由承接票更新规则并 render write"])
        self.assertEqual(st.projection(self.ENTRIES, ["src/other/**"]), [])

    def test_verify_slice_full_equality(self):
        expected = st.projection(self.ENTRIES, ["services/**"])
        sha = hashlib.sha256(b"spec").hexdigest()
        source = "dev/features/m/demo/demo-tech-spec.md"
        issue = "# ticket\n\n" + st.slice_section(source, sha, expected)
        st.verify_slice(issue, source, sha, expected)
        with self.assertRaisesRegex(st.SpecTreeError, "lacks a 方案切片"):
            st.verify_slice("# ticket\n", source, sha, expected)
        with self.assertRaisesRegex(st.SpecTreeError, "SHA256"):
            st.verify_slice(issue, source, "0" * 64, expected)
        with self.assertRaisesRegex(st.SpecTreeError, "stale"):
            st.verify_slice(issue, source, sha, expected + ["extra.py [新增] 多出来的一行"])
        with self.assertRaisesRegex(st.SpecTreeError, "duplicate"):
            doubled = issue.replace("```text\n", "```text\n" + expected[0] + "\n", 1)
            st.verify_slice(doubled, source, sha, expected)

    def test_write_slice_is_idempotent_and_sits_before_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = Path(tmp) / "issue-001.md"
            issue.write_text("# ticket\n\n## 备注\n\n上游：tech-spec `demo.md`。\n", encoding="utf-8")
            sha = hashlib.sha256(b"spec").hexdigest()
            lines = st.projection(self.ENTRIES, ["docs/ARCH-CHECK.json"])
            st.write_slice(issue, "demo.md", sha, lines)
            first = issue.read_text(encoding="utf-8")
            self.assertIn("## 方案切片", first)
            self.assertLess(first.index("## 方案切片"), first.index("## 备注"))
            st.write_slice(issue, "demo.md", sha, lines)
            self.assertEqual(issue.read_text(encoding="utf-8"), first)
            st.write_slice(issue, "demo.md", sha, [])
            self.assertIn("未命中 3.1 树文件", issue.read_text(encoding="utf-8"))


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SliceCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "Test")
        (self.root / "src").mkdir()
        (self.root / "src/a.py").write_text("a = 1\n", encoding="utf-8")
        self.feature = self.root / "dev/features/m/demo"
        (self.feature / "issues").mkdir(parents=True)
        self.spec = self.feature / "demo-tech-spec-2026-11-22.md"
        self.spec.write_text(spec_with_tree("src/\n  a.py [修改] a() 增加幂等校验\n"), encoding="utf-8")
        self.issue = self.feature / "issues/issue-001-demo.md"
        self.issue.write_text("""---
feature-slug: demo
issue-id: issue-001
status: proposed
declared-scope: [src/**]
---

# Demo

## 要做什么（What to build）

打通幂等。

## 验收标准（DoD，可自动判定）

- [ ] `python3 -I tests/test_a.py` → 退出码 `0`

## 备注

上游：tech-spec `demo-tech-spec-2026-11-22.md`，相关节 `#3.1`。
""", encoding="utf-8")
        sh(self.root, "git", "add", ".")
        sh(self.root, "git", "commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_slice(self):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "slice",
                               "--issue", str(self.issue), "--repo", str(self.root)],
                              capture_output=True, text=True)

    def test_slice_cli_writes_projection_section(self):
        result = self._run_slice()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["slice_lines"], 1)
        text = self.issue.read_text(encoding="utf-8")
        self.assertIn("src/a.py [修改] a() 增加幂等校验", text)
        self.assertIn(hashlib.sha256(self.spec.read_bytes()).hexdigest(), text)
        # idempotent regeneration
        self.assertEqual(self._run_slice().returncode, 0)

    def test_slice_cli_rejects_legacy_spec_and_empty_scope(self):
        self.spec.write_text("# Spec\n\n### 3.1 模块拆解\n\n```text\nsrc/\n  a.py [修改] x\n```\n", encoding="utf-8")
        result = self._run_slice()
        self.assertEqual(result.returncode, 2)
        self.assertIn("spec-tree-version", result.stderr)
        self.spec.write_text(spec_with_tree("src/\n  a.py [修改] a() 增加幂等校验\n"), encoding="utf-8")
        self.issue.write_text(self.issue.read_text(encoding="utf-8").replace("declared-scope: [src/**]",
                                                                             "declared-scope: []"), encoding="utf-8")
        result = self._run_slice()
        self.assertEqual(result.returncode, 2)
        self.assertIn("declared-scope", result.stderr)

    def test_slice_cli_rejects_malformed_tree(self):
        self.spec.write_text(spec_with_tree("src/\n  a.py [重构] 未知动作\n"), encoding="utf-8")
        result = self._run_slice()
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown tree action", result.stderr)


if __name__ == "__main__":
    unittest.main()
