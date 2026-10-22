#!/usr/bin/env python3
"""contract_check.py 的初始化盘点 / 模块四方一致 / supersedes 链 / 契约义务派发 / 登记↔代码 契约测试。

运行：python3 -m unittest discover -s skills/shared/scripts/tests -v
每个用例在独立临时目录建合成项目（docs/ + dev/ 结构），不起 git。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "contract_check.py"

GLOSSARY = """# GLOSSARY

## 术语表

| 术语 | 定义 | 别名/口语说法 | 拒绝词 | 关联 feature-slug |
| --- | --- | --- | --- | --- |
| … | … | … | … | … |

## 数据口径

| 指标 | 定义/公式 | 统计窗口 | 数据来源 |
| --- | --- | --- | --- |
| … | … | … | … |

## feature-module 归档

| feature-module | 说明 | 成员 feature-slug |
| --- | --- | --- |
| 判读配置 | 判读规则与模板配置 | 文档驱动模板配置 |
| 数据接入 | 外部数据源接入 | 实时流接入 |
"""

ARCH_OK = """# ARCHITECTURE

## 模块现行态

| 模块（feature-module） | 职责一句话 | 代码位置 | 成员 feature-slug |
| --- | --- | --- | --- |
| 判读配置 | 判读规则与模板配置 | src/config | 文档驱动模板配置 |
| 数据接入 | 外部数据源接入 | src/ingest | 实时流接入 |

## 接口现行态

| 接口（名 + 形态：函数 / HTTP / 事件 / 协议） | 现行签名 / 契约要点 | 代码位置（文件:符号） | 来源（tech-spec 文件名） | 生效日期 |
| --- | --- | --- | --- | --- |
| load_config（函数） | load_config(path) -> dict | src/config/loader.py:load_config | 文档驱动模板配置-tech-spec-2026-10-01.md | 2026-10-01 |

## 跨 feature 架构约束

| 约束 | 依据（PRD / tech-spec / 仓库等级） | 生效日期 |
| --- | --- | --- |
| … | … | … |
"""

SPEC_EMPTY_32 = """---
doc_type: tech-spec
status: 已确认（待拆解）
supersedes:
---

# Tech Spec：x

### 3.1 模块拆解
x

### 3.2 接口变更（新增 / 修改 / 废弃）

<!-- 契约更新由承接票随分支执行 -->

### 3.3 数据与存储
x
"""

SPEC_WITH_32 = SPEC_EMPTY_32.replace(
    "<!-- 契约更新由承接票随分支执行 -->", "新增 `load_config_v2(path)` 替代旧加载入口")

ISSUE_NO_CONTRACT = """---
issue-id: issue-001
status: confirmed
---

# x

## 验收标准（DoD，可自动判定）

- [ ] `pytest tests/test_x.py` → 期望输出 `exit 0`
"""

ISSUE_WITH_CONTRACT = ISSUE_NO_CONTRACT.replace(
    "- [ ] `pytest tests/test_x.py` → 期望输出 `exit 0`",
    "- [ ] `pytest tests/test_x.py` → 期望输出 `exit 0`\n"
    "- [ ] `git diff main...HEAD -- docs/ARCHITECTURE.md | grep load_config_v2` → 期望输出 `命中接口条目`",
)


def run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd, capture_output=True, text=True,
    )


def make_repo(root, glossary=GLOSSARY, arch=None, specs=(), issues=(), code_files=()):
    """specs/issues: (relpath, content)；code_files: (relpath, content)。"""
    docs = root / "docs"
    docs.mkdir(parents=True)
    if glossary is not None:
        (docs / "GLOSSARY.md").write_text(glossary, encoding="utf-8")
    if arch is not None:
        (docs / "ARCHITECTURE.md").write_text(arch, encoding="utf-8")
    for rel, content in list(specs) + list(issues) + list(code_files):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


SPEC_REL = "dev/features/判读配置/文档驱动模板配置/文档驱动模板配置-tech-spec-2026-10-01.md"
ISSUE_REL = "dev/features/判读配置/文档驱动模板配置/issues/issue-001-x.md"


class InitTest(unittest.TestCase):
    def test_init_prefills_modules_from_glossary(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            r = run("--init", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stderr)
            arch = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
            self.assertIn("| 判读配置 | 判读规则与模板配置 | （待核对） | 文档驱动模板配置 |", arch)
            self.assertIn("| 数据接入 | 外部数据源接入 | （待核对） | 实时流接入 |", arch)

    def test_init_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), arch=ARCH_OK)
            r = run("--init", "--repo", str(root))
            self.assertEqual(r.returncode, 2)
            self.assertIn("拒绝覆盖", r.stderr)

    def test_init_without_glossary_leaves_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), glossary=None)
            r = run("--init", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stderr)
            arch = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
            self.assertIn("| … | … | … | … |", arch)


class CheckTest(unittest.TestCase):
    def test_consistent_project_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(
                Path(td), arch=ARCH_OK,
                code_files=[("src/config/loader.py", "def load_config(path):\n    return {}\n")],
            )
            (root / "src/config").mkdir(parents=True, exist_ok=True)
            (root / "src/ingest").mkdir(parents=True, exist_ok=True)
            (root / "docs/features/判读配置").mkdir(parents=True)
            (root / "docs/features/数据接入").mkdir(parents=True)
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("FAIL 0 项", r.stdout)

    def test_mod1_fail_on_arch_glossary_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            arch = ARCH_OK.replace("| 数据接入 | 外部数据源接入 | src/ingest | 实时流接入 |\n", "")
            root = make_repo(Path(td), arch=arch)
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("MOD-1", r.stdout)

    def test_mod1_dir_transient_is_info_not_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), arch=ARCH_OK,
                             code_files=[("src/config/loader.py", "load_config")])
            (root / "src/ingest").mkdir(parents=True)
            (root / "docs/features/判读配置").mkdir(parents=True)
            (root / "docs/features/数据接入").mkdir(parents=True)
            (root / "docs/features/新模块").mkdir()  # 目录先行，登记未补
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("[INFO] MOD-1", r.stdout)

    def test_spec1_missing_target(self):
        with tempfile.TemporaryDirectory() as td:
            spec = SPEC_EMPTY_32.replace("supersedes:", "supersedes: 不存在-tech-spec-2020-01-01.md")
            root = make_repo(Path(td), specs=[(SPEC_REL, spec)])
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("SPEC-1", r.stdout)

    def test_spec1_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            a = SPEC_EMPTY_32.replace("supersedes:", "supersedes: b-tech-spec-2026-10-02.md")
            b = SPEC_EMPTY_32.replace("supersedes:", "supersedes: a-tech-spec-2026-10-01.md")
            root = make_repo(Path(td), specs=[
                ("dev/features/m/s/a-tech-spec-2026-10-01.md", a),
                ("dev/features/m/s/b-tech-spec-2026-10-02.md", b),
            ])
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("环", r.stdout)

    def test_spec2_fail_when_32_nonempty_and_no_obligation(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), specs=[(SPEC_REL, SPEC_WITH_32)],
                             issues=[(ISSUE_REL, ISSUE_NO_CONTRACT)])
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("SPEC-2", r.stdout)

    def test_spec2_pass_when_ticket_declares_contract_dod(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), specs=[(SPEC_REL, SPEC_WITH_32)],
                             issues=[(ISSUE_REL, ISSUE_WITH_CONTRACT)])
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_spec2_pass_when_arch_sources_cite_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), arch=ARCH_OK, specs=[(SPEC_REL, SPEC_WITH_32)],
                             issues=[(ISSUE_REL, ISSUE_NO_CONTRACT)],
                             code_files=[("src/config/loader.py", "load_config")])
            (root / "src/ingest").mkdir(parents=True)
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_spec2_skips_empty_32(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), specs=[(SPEC_REL, SPEC_EMPTY_32)],
                             issues=[(ISSUE_REL, ISSUE_NO_CONTRACT)])
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_code1_fail_on_missing_symbol(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), arch=ARCH_OK,
                             code_files=[("src/config/loader.py", "def other():\n    pass\n")])
            (root / "src/ingest").mkdir(parents=True)
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("CODE-1", r.stdout)

    def test_code1_fail_on_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), arch=ARCH_OK)
            (root / "src/ingest").mkdir(parents=True)
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("文件不存在", r.stdout)

    def test_code2_fail_on_missing_module_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), arch=ARCH_OK,
                             code_files=[("src/config/loader.py", "load_config")])
            # src/ingest 不建
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("CODE-2", r.stdout)

    def test_placeholder_rows_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            skeleton = ARCH_OK.replace(
                "| 判读配置 | 判读规则与模板配置 | src/config | 文档驱动模板配置 |\n"
                "| 数据接入 | 外部数据源接入 | src/ingest | 实时流接入 |",
                "| … | … | … | … |",
            ).replace(
                "| load_config（函数） | load_config(path) -> dict | src/config/loader.py:load_config | 文档驱动模板配置-tech-spec-2026-10-01.md | 2026-10-01 |",
                "| … | … | … | … | … |",
            )
            root = make_repo(Path(td), arch=skeleton)
            r = run("--check", "--repo", str(root))
            # 占位行不触发 CODE 检查；模块表占位（全 …）不参与 MOD-1 对账
            self.assertNotIn("CODE-1", r.stdout)
            self.assertNotIn("CODE-2", r.stdout)

    def test_glossary_second_table_and_retired_rows_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            glossary = GLOSSARY + """
**slug 说明与迁移记录**

| feature-slug | 所属模块 | 说明 |
| --- | --- | --- |
| 文档驱动模板配置 | 判读配置 | 迁移说明 |

| feature-module | 说明 | 成员 |
| --- | --- | --- |
| ~~判读阶段选择与性能~~ | 已退役并入判读流程 | - |
"""
            root = make_repo(Path(td), glossary=glossary, arch=ARCH_OK,
                             code_files=[("src/config/loader.py", "load_config")])
            (root / "src/ingest").mkdir(parents=True)
            r = run("--check", "--repo", str(root))
            # 模块集合仍恰为 {判读配置, 数据接入}：附表 slug 与退役行不混入 → 无 MOD-1 FAIL
            self.assertNotIn("[FAIL] MOD-1", r.stdout)

    def test_spec2_grandfathered_before_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            # ARCH 接口表来源列不引用本 spec，确保落到祖父条款分支而非「已落地」分支
            arch = "<!-- baseline: 2026-10-22 -->\n" + ARCH_OK.replace(
                "文档驱动模板配置-tech-spec-2026-10-01.md", "别的-tech-spec-2026-09-01.md")
            root = make_repo(Path(td), arch=arch, specs=[(SPEC_REL, SPEC_WITH_32)],
                             issues=[(ISSUE_REL, ISSUE_NO_CONTRACT)],
                             code_files=[("src/config/loader.py", "load_config")])
            (root / "src/ingest").mkdir(parents=True)
            r = run("--check", "--repo", str(root))  # spec 日期 2026-10-01 < baseline
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("早于建档日", r.stdout)

    def test_spec2_fail_on_or_after_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            arch = "<!-- baseline: 2026-10-22 -->\n" + ARCH_OK
            late_spec = SPEC_REL.replace("2026-10-01", "2026-10-25")
            root = make_repo(Path(td), arch=arch, specs=[(late_spec, SPEC_WITH_32)],
                             issues=[(ISSUE_REL, ISSUE_NO_CONTRACT)],
                             code_files=[("src/config/loader.py", "load_config")])
            (root / "src/ingest").mkdir(parents=True)
            r = run("--check", "--repo", str(root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("SPEC-2", r.stdout)


if __name__ == "__main__":
    unittest.main()
