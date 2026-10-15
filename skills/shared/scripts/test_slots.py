#!/usr/bin/env python3
"""整机测试槽位：限制同机并发的重测试 worker 总数与重套件套数，防多会话 / 多仓超卖。

只暴露执行包装形态——`run` 在 wrapper 进程存活期间持锁，进程死亡（含 kill -9）由内核
释放 flock，无需 pid 存活检查，也不存在跨调用持锁的泄漏面。

子命令：
  run --want N --label L [--queue-timeout S] -- <命令...>
      阻塞排队直到拿到槽位（可以少拿：1 ≤ 分得 ≤ min(want, 空闲)），分得数注入子进程
      $JOBS；退出码透传子进程；stderr 末行打印机器可读的执行摘要（queue-seconds /
      run-seconds / load-peak / exit）。排队超时退出码 75。
  status [--json]
      列出持有者 / 排队者 / 预算与占用汇总；顺带回收死亡持有者的残留文件。
  budget [--total N] [--max-runs N] [--queue-timeout S]
      无参数 = 查看生效预算及其来源；带参数 = 写入机器级预算文件。

状态目录：$AGENT_DEV_SLOTS_DIR 或 ~/.cache/agent-dev/slots/（跨项目共享，不进任何仓库）
  budget.json     机器级预算，同机各仓唯一真源；缺失时按内置默认值自建
  alloc.lock      分配决策的短临界区互斥锁（只在拿 / 放 / 盘点时短暂持有）
  run-*.lock      运行持有：文件内容为持有者元数据 JSON，flock 持有期 = 子进程运行期
  wait-*.lock     排队持有：同上，拿到槽位或超时即删

只依赖 Python 3.8+ 标准库；仅 POSIX（fcntl.flock），macOS / Linux 可用。
退出码：run = 子进程退出码（排队超时 75，命令缺失 2）；status / budget = 0（输入错误 2）。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

EXIT_QUEUE_TIMEOUT = 75
DEFAULT_MAX_RUNS = 2
DEFAULT_QUEUE_TIMEOUT = 1200  # 大于常见 full 套件 P90，避免正常排队被误杀
POLL_SECONDS = 1.0
LOAD_SAMPLE_SECONDS = 5.0


class InputError(Exception):
    pass


def state_dir() -> Path:
    env = os.environ.get("AGENT_DEV_SLOTS_DIR")
    d = Path(env) if env else Path.home() / ".cache" / "agent-dev" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_budget() -> dict:
    return {"total": max(1, (os.cpu_count() or 2) - 2),
            "max-runs": DEFAULT_MAX_RUNS,
            "queue-timeout": DEFAULT_QUEUE_TIMEOUT}


def _validate_budget(b: dict, src: str) -> dict:
    out = {}
    for key in ("total", "max-runs", "queue-timeout"):
        v = b.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < (0 if key == "queue-timeout" else 1):
            raise InputError(f"{src} 中 {key}={v!r} 非法（须为正整数" + ("，queue-timeout 可为 0）" if key == "queue-timeout" else "）"))
        out[key] = v
    return out


def load_budget(d: Path) -> tuple[dict, str]:
    """读机器级预算；缺失时按默认值自建（首次使用即固化，保证同机各进程口径一致）。"""
    f = d / "budget.json"
    if not f.is_file():
        b = default_budget()
        f.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return b, f"{f}（按内置默认值新建）"
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"预算文件损坏：{f}：{e}")
    merged = {**default_budget(), **{k: v for k, v in raw.items() if k in ("total", "max-runs", "queue-timeout")}}
    return _validate_budget(merged, str(f)), str(f)


class HeldLock:
    """一个已持有的槽位锁文件：fd 存活期间 flock 有效，进程死亡由内核释放。"""

    def __init__(self, path: Path, fd: int):
        self.path = path
        self.fd = fd

    def release(self):
        try:
            os.close(self.fd)  # close 即放锁
        except OSError:
            pass
        try:
            self.path.unlink()
        except OSError:
            pass  # 可能已被盘点线程当作残留回收


class AllocLock:
    """alloc.lock 短临界区：文件创建 + flock + 元数据写入 + 盘点都必须在其内完成，
    保证任何观察者不会看到「已建文件但未持锁 / 未写完」的中间态。"""

    def __init__(self, d: Path):
        self.d = d
        self.fd = -1

    def __enter__(self):
        self.fd = os.open(self.d / "alloc.lock", os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _gc_and_list(d: Path) -> tuple[list[dict], list[dict]]:
    """须在 AllocLock 临界区内调用。flock 探测存活：能拿锁 = 持有者已死 → 回收。"""
    holders, waiters = [], []
    for pat, sink in (("run-*.lock", holders), ("wait-*.lock", waiters)):
        for f in sorted(d.glob(pat)):
            fd = os.open(f, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                alive = False
            except OSError:
                alive = True
            if alive:
                os.close(fd)
                try:
                    meta = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
                meta["_file"] = f.name
                sink.append(meta)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                try:
                    f.unlink()
                except OSError:
                    pass
    return holders, waiters


def _write_meta(fd: int, meta: dict):
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    os.fsync(fd)


def acquire(d: Path, budget: dict, want: int, label: str, cmd_str: str,
            timeout: float) -> tuple[HeldLock | None, int, float]:
    """阻塞到拿槽或超时。返回 (持有锁, 分得槽数, 排队秒数)；超时持有锁为 None。"""
    t0 = time.time()
    wait_path = d / f"wait-{uuid.uuid4().hex[:12]}.lock"
    wait_fd = os.open(wait_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(wait_fd, fcntl.LOCK_EX)  # 排队期持有，供 status 判活
        _write_meta(wait_fd, {"pid": os.getpid(), "label": label, "want": want, "since": t0})
        while True:
            with AllocLock(d):
                holders, _ = _gc_and_list(d)
                used = sum(int(h.get("workers") or 0) for h in holders)
                if len(holders) < budget["max-runs"] and used < budget["total"]:
                    grant = max(1, min(want, budget["total"] - used))
                    run_path = d / f"run-{uuid.uuid4().hex[:12]}.lock"
                    run_fd = os.open(run_path, os.O_CREAT | os.O_RDWR, 0o644)
                    fcntl.flock(run_fd, fcntl.LOCK_EX)
                    _write_meta(run_fd, {"pid": os.getpid(), "label": label, "workers": grant,
                                         "started": time.time(), "cmd": cmd_str})
                    return HeldLock(run_path, run_fd), grant, time.time() - t0
            remaining = timeout - (time.time() - t0)
            if remaining <= 0:
                return None, 0, time.time() - t0
            time.sleep(min(POLL_SECONDS, remaining))
    finally:
        try:
            os.close(wait_fd)
        finally:
            try:
                wait_path.unlink()
            except OSError:
                pass


def cmd_run(args) -> int:
    d = state_dir()
    budget, _ = load_budget(d)
    if args.want is not None and args.want < 1:
        raise InputError("--want 须 ≥ 1")
    want = args.want if args.want is not None else max(1, budget["total"] // budget["max-runs"])
    timeout = args.queue_timeout if args.queue_timeout is not None else budget["queue-timeout"]
    label = args.label or f"{Path.cwd().name}/pid{os.getpid()}"
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise InputError("run 需要 `-- <命令>`")

    held, grant, queued = acquire(d, budget, want, label, " ".join(cmd), timeout)
    if held is None:
        print(f"test-slots: 排队 {queued:.0f}s 超过 queue-timeout={timeout}s 未拿到槽位"
              f"（total={budget['total']} max-runs={budget['max-runs']}）；"
              f"用 status 查看持有者，或调大预算 / 错峰", file=sys.stderr)
        return EXIT_QUEUE_TIMEOUT

    peak = [0.0]
    stop = threading.Event()

    def monitor():
        while not stop.wait(LOAD_SAMPLE_SECONDS):
            try:
                peak[0] = max(peak[0], os.getloadavg()[0])
            except OSError:
                pass

    env = dict(os.environ, JOBS=str(grant))
    t0 = time.time()
    rc = 127
    try:
        proc = subprocess.Popen(cmd, env=env)
    except FileNotFoundError:
        print(f"test-slots: 命令不存在：{cmd[0]}", file=sys.stderr)
        held.release()
        return 2
    received: list[int] = []

    def forward(sig, _frame):
        received.append(sig)
        try:
            proc.send_signal(sig)
        except OSError:
            pass

    old_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        old_handlers[sig] = signal.signal(sig, forward)
    threading.Thread(target=monitor, daemon=True).start()
    try:
        rc = proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise
    finally:
        stop.set()
        for sig, h in old_handlers.items():
            signal.signal(sig, h)
        ran = time.time() - t0
        if received and rc >= 0:
            rc = 128 + received[-1]
        print(f"test-slots: label={label} granted={grant} queue-seconds={queued:.1f} "
              f"run-seconds={ran:.1f} load-peak={peak[0]:.1f} exit={rc}", file=sys.stderr)
        held.release()
    return rc


def cmd_status(args) -> int:
    d = state_dir()
    budget, src = load_budget(d)
    now = time.time()
    with AllocLock(d):
        holders, waiters = _gc_and_list(d)
    used = sum(int(h.get("workers") or 0) for h in holders)
    out = {
        "budget": budget, "budget-source": src,
        "workers": {"used": used, "total": budget["total"]},
        "runs": {"active": len(holders), "max-runs": budget["max-runs"]},
        "holders": [{"label": h.get("label"), "pid": h.get("pid"), "workers": h.get("workers"),
                     "elapsed-seconds": round(now - float(h.get("started") or now), 1),
                     "cmd": h.get("cmd")} for h in holders],
        "waiters": [{"label": w.get("label"), "pid": w.get("pid"), "want": w.get("want"),
                     "waiting-seconds": round(now - float(w.get("since") or now), 1)} for w in waiters],
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"budget: total={budget['total']} max-runs={budget['max-runs']} "
          f"queue-timeout={budget['queue-timeout']}s（来源：{src}）")
    print(f"holders: {out['runs']['active']}/{budget['max-runs']} runs, workers {used}/{budget['total']}")
    for h in out["holders"]:
        print(f"  - {h['label']} workers={h['workers']} pid={h['pid']} "
              f"elapsed={h['elapsed-seconds']:.0f}s cmd={h['cmd']}")
    print(f"waiters: {len(out['waiters'])}")
    for w in out["waiters"]:
        print(f"  - {w['label']} want={w['want']} pid={w['pid']} waiting={w['waiting-seconds']:.0f}s")
    return 0


def cmd_budget(args) -> int:
    d = state_dir()
    f = d / "budget.json"
    if args.total is None and args.max_runs is None and args.queue_timeout is None:
        budget, src = load_budget(d)
        print(json.dumps(budget, ensure_ascii=False, indent=2))
        print(f"来源：{src}")
        return 0
    current, _ = load_budget(d)
    new = {"total": args.total if args.total is not None else current["total"],
           "max-runs": args.max_runs if args.max_runs is not None else current["max-runs"],
           "queue-timeout": args.queue_timeout if args.queue_timeout is not None else current["queue-timeout"]}
    new = _validate_budget(new, "参数")
    f.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {f}：" + json.dumps(new, ensure_ascii=False))
    print("注意：这是整机预算，同机各仓 / 各会话共享，改动对全部项目生效")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="整机测试槽位（重测试并发预算；flock 持有期锁，死亡自动回收）")
    sub = ap.add_subparsers(dest="sub", required=True)
    pr = sub.add_parser("run", help="拿槽执行命令，分得槽数注入 $JOBS")
    pr.add_argument("--want", type=int, help="期望 worker 数；缺省 = total // max-runs")
    pr.add_argument("--label", help="持有标签（项目 / 票 / 档位），缺省 = 目录名/pid")
    pr.add_argument("--queue-timeout", type=float, help="排队秒数上限；缺省取预算")
    pr.add_argument("cmd", nargs=argparse.REMAINDER, help="`--` 后的命令")
    ps = sub.add_parser("status", help="持有者 / 排队者 / 预算")
    ps.add_argument("--json", action="store_true")
    pb = sub.add_parser("budget", help="查看或写入机器级预算")
    pb.add_argument("--total", type=int)
    pb.add_argument("--max-runs", type=int)
    pb.add_argument("--queue-timeout", type=int)
    args = ap.parse_args(argv)
    try:
        if args.sub == "run":
            return cmd_run(args)
        if args.sub == "status":
            return cmd_status(args)
        return cmd_budget(args)
    except InputError as e:
        print(f"test-slots: 输入错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
