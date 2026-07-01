#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from datetime import datetime, timezone


RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
DIM = "\033[2m"


def colorize(text, color, enable=True):
    if not enable:
        return text
    return f"{color}{text}{RESET}"


def supports_color():
    return os.environ.get("NO_COLOR") is None and os.isatty(1)


def status_color(text):
    if text in ("✅", "可分批买入", "确认层支持，趋势环境偏顺", "走势顺风"):
        return GREEN
    if text in ("⚠️", "见顶信号初现，可减部分仓位或上移止盈", "确认层中性，适合等回撤"):
        return YELLOW
    if text in ("❌", "优先减仓/止盈", "确认层偏弱，先看不追", "无买入信号，暂不建议追多"):
        return RED
    return CYAN


def simple_table(rows, headers=None, color=True):
    lines = []
    if headers:
        lines.append(" | ".join(headers))
        lines.append("-+-".join("-" * len(h) for h in headers))
    for row in rows:
        lines.append(" | ".join(str(x) for x in row))
    if color:
        return "\n".join(lines)
    return "\n".join(lines)


def log_line(path, line):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ts_now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
