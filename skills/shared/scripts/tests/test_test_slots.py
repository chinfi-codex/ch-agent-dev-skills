#!/usr/bin/env python3
"""test_slots.py 的并发 / 回收 / 超时契约测试。

运行：python3 -m unittest discover -s skills/shared/scripts/tests -v
每个用例用 AGENT_DEV_SLOTS_DIR 指向独立临时目录，与真实机器级状态隔离。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "test_slots.py"

CHILD = (
    "import os,sys,time;"
    "m=os.environ['MARKER'];"
    "open(m,'a').write('S %s '+os.environ.get('JOBS','?')+'\\n');"
    "time.sleep(float(sys.argv[1]));"
    "open(m,'a').write('E %s\\n')"
)


class SlotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "slots"
        self.dir.mkdir()
        self.marker = Path(self.tmp.name) / "events.log"
        self.env = dict(os.environ, AGENT_DEV_SLOTS_DIR=str(self.dir))
        self.write_budget(total=8, max_runs=2, queue_timeout=60)

    def write_budget(self, total, max_runs, queue_timeout):
        (self.dir / "budget.json").write_text(
            json.dumps({"total": total, "max-runs": max_runs, "queue-timeout": queue_timeout}),
            encoding="utf-8")

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def spawn(self, label, want, sleep_s, extra=()):
        child = CHILD % (label, label)
        env = dict(self.env, MARKER=str(self.marker))
        return subprocess.Popen(
            [sys.executable, str(SCRIPT), "run", "--want", str(want), "--label", label,
             *extra, "--", sys.executable, "-c", child, str(sleep_s)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_holder(self, label, timeout=15):
        """轮询 status 直到 label 出现在持有者里（确保其已占槽后再继续）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.cli("status", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            holders = json.loads(r.stdout)["holders"]
            if any(h["label"] == label for h in holders):
                return holders
            time.sleep(0.2)
        self.fail(f"{label} 未在 {timeout}s 内成为持有者")

    def sweep_events(self):
        """把 START/END 事件扫成时间线，返回每时刻的 (活跃套数, worker 总数) 峰值。"""
        active = {}
        max_runs, max_workers = 0, 0
        for line in self.marker.read_text().splitlines():
            tag, label, *rest = line.split()
            if tag == "S":
                active[label] = int(rest[0])
            else:
                active.pop(label, None)
            max_runs = max(max_runs, len(active))
            max_workers = max(max_workers, sum(active.values()))
        return max_runs, max_workers

    def test_concurrency_limits(self):
        """5 个 run --want 6（total=8, max-runs=2）：任意时刻套数 ≤ 2 且 worker 总和 ≤ 8，全部最终成功。"""
        procs = [self.spawn(f"t{i}", 6, 1.5) for i in range(5)]
        for p in procs:
            _, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
            self.assertIn("test-slots:", err)  # 执行摘要落 stderr
        max_runs, max_workers = self.sweep_events()
        self.assertLessEqual(max_runs, 2)
        self.assertLessEqual(max_workers, 8)
        self.assertEqual(max_runs, 2)  # 5 并发提交，理应真的打满过 2 套

    def test_partial_grant_floor_one(self):
        """A 拿走 6/8 后，B --want 6 只能拿 2（少拿但不少于 1）。"""
        a = self.spawn("big", 6, 4)
        self.wait_holder("big")
        b = self.spawn("small", 6, 0.1)
        _, berr = b.communicate(timeout=60)
        self.assertEqual(b.returncode, 0, berr)
        a.communicate(timeout=60)
        grants = {ln.split()[1]: int(ln.split()[2]) for ln in self.marker.read_text().splitlines()
                  if ln.startswith("S ")}
        self.assertEqual(grants["big"], 6)
        self.assertEqual(grants["small"], 2)

    def test_kill9_reclaims(self):
        """持有者被 kill -9 后，槽位被回收，后续 run 能立即拿到。"""
        a = self.spawn("victim", 8, 30)
        self.wait_holder("victim")
        a.send_signal(signal.SIGKILL)
        a.wait(timeout=10)
        r = self.cli("status", "--json")  # status 顺带回收
        self.assertEqual(json.loads(r.stdout)["holders"], [])
        b = self.spawn("next", 8, 0.1, extra=("--queue-timeout", "10"))
        _, berr = b.communicate(timeout=60)
        self.assertEqual(b.returncode, 0, berr)

    def test_queue_timeout(self):
        """total=4 max-runs=1 被占满时，--queue-timeout 2 的排队者以 75 退出并给出说明。"""
        self.write_budget(total=4, max_runs=1, queue_timeout=60)
        a = self.spawn("holder", 4, 5)
        self.wait_holder("holder")
        t0 = time.time()
        b = self.spawn("late", 4, 0.1, extra=("--queue-timeout", "2"))
        _, berr = b.communicate(timeout=60)
        self.assertEqual(b.returncode, 75, berr)
        self.assertIn("queue-timeout", berr)  # 给出排队超时说明而非静默等待
        self.assertGreaterEqual(time.time() - t0, 1.5)
        a.communicate(timeout=60)

    def test_budget_roundtrip_and_default_want(self):
        """budget 子命令写入后 status 反映；--want 缺省 = total // max-runs。"""
        r = self.cli("budget", "--total", "6", "--max-runs", "3", "--queue-timeout", "30")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual(st["budget"], {"total": 6, "max-runs": 3, "queue-timeout": 30})
        p = subprocess.Popen(
            [sys.executable, str(SCRIPT), "run", "--label", "dw", "--",
             sys.executable, "-c", CHILD % ("dw", "dw"), "0.2"],
            env=dict(self.env, MARKER=str(self.marker)),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("granted=2", err)  # 6 // 3


if __name__ == "__main__":
    unittest.main()
