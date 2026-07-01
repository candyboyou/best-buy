#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"
DEFAULT_NGROK_URL = "tarot-dairy-diner.ngrok-free.dev"


SERVICES = {
    "dashboard": {
        "pid": RUNTIME / "dashboard.pid",
        "log": RUNTIME / "dashboard.log",
    },
    "ngrok": {
        "pid": RUNTIME / "ngrok.pid",
        "log": RUNTIME / "ngrok.log",
    },
    "watch": {
        "pid": RUNTIME / "watch.pid",
        "log": RUNTIME / "watch.log",
    },
}


def ensure_runtime():
    RUNTIME.mkdir(exist_ok=True)


def is_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid(path):
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def service_running(name):
    return is_running(read_pid(SERVICES[name]["pid"]))


def start_process(name, cmd):
    ensure_runtime()
    if service_running(name):
        print(f"{name} 已在运行 pid={read_pid(SERVICES[name]['pid'])}")
        return
    log_path = SERVICES[name]["log"]
    log_file = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    SERVICES[name]["pid"].write_text(str(proc.pid), encoding="utf-8")
    print(f"{name} 已启动 pid={proc.pid} log={log_path}")


def stop_service(name, timeout=8):
    pid_path = SERVICES[name]["pid"]
    pid = read_pid(pid_path)
    if not pid or not is_running(pid):
        print(f"{name} 未运行")
        pid_path.unlink(missing_ok=True)
        return
    os.killpg(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(pid):
            break
        time.sleep(0.2)
    if is_running(pid):
        os.killpg(pid, signal.SIGKILL)
    pid_path.unlink(missing_ok=True)
    print(f"{name} 已停止")


def start(args):
    if shutil.which("ngrok") is None:
        print("错误：找不到 ngrok，请先安装或把 ngrok 加到 PATH。")
        return
    start_process("dashboard", [sys.executable, "dashboard_server.py", "--host", args.host, "--port", str(args.port)])
    start_process("ngrok", ["ngrok", "http", f"--url={args.ngrok_url}", str(args.port)])
    if args.watch:
        cmd = [sys.executable, "best_buy.py", "--watch", "--symbol", args.symbol]
        if args.ignore_market_hours:
            cmd.append("--ignore-market-hours")
        start_process("watch", cmd)
    print(f"本地页面: http://{args.host}:{args.port}")
    print(f"公网页面: https://{args.ngrok_url}")


def stop(args):
    names = ["ngrok", "dashboard"]
    if args.watch:
        names.insert(0, "watch")
    for name in names:
        stop_service(name)


def status(_args):
    for name, meta in SERVICES.items():
        pid = read_pid(meta["pid"])
        state = "运行中" if is_running(pid) else "未运行"
        pid_text = f" pid={pid}" if pid else ""
        print(f"{name}: {state}{pid_text} log={meta['log']}")


def logs(args):
    path = SERVICES[args.service]["log"]
    if not path.exists():
        print(f"{args.service} 暂无日志: {path}")
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines:]:
        print(line)


def restart(args):
    stop(argparse.Namespace(watch=args.watch))
    start(args)


def main():
    parser = argparse.ArgumentParser(description="best-buy 服务管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--ngrok-url", default=DEFAULT_NGROK_URL)
        p.add_argument("--watch", action="store_true", help="同时后台启动行情监控")
        p.add_argument("--symbol", default="07709")
        p.add_argument("--ignore-market-hours", action="store_true", help="后台监控忽略交易时段，始终轮询")

    p_start = sub.add_parser("start", help="后台启动 dashboard 和 ngrok")
    add_common(p_start)
    p_start.set_defaults(func=start)

    p_restart = sub.add_parser("restart", help="重启服务")
    add_common(p_restart)
    p_restart.set_defaults(func=restart)

    p_stop = sub.add_parser("stop", help="停止服务")
    p_stop.add_argument("--watch", action="store_true", help="同时停止后台行情监控")
    p_stop.set_defaults(func=stop)

    p_status = sub.add_parser("status", help="查看服务状态")
    p_status.set_defaults(func=status)

    p_logs = sub.add_parser("logs", help="查看日志")
    p_logs.add_argument("service", choices=sorted(SERVICES))
    p_logs.add_argument("-n", "--lines", type=int, default=80)
    p_logs.set_defaults(func=logs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
