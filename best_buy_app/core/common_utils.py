#!/usr/bin/env python3
# -*- coding: utf-8 -*-


def fmt_num(v):
    if v is None:
        return "N/A"
    if isinstance(v, (int, float)) and abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v}"
