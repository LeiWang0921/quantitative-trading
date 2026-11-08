"""窗口程序对 config.toml 的唯一改动：往 [strategy.sleeves] 的候选股列表末尾加一个代码。

只改 stocks = [...] 这一行，其余内容（包括注释）原样保留；改之前备份到 config.toml.bak，
改完重新解析一遍，确认生效，否则还原。
"""
from __future__ import annotations

import re
import shutil
import tomllib

from ..config import ROOT

CONFIG = ROOT / "config.toml"


def add_candidate(symbol: str) -> None:
    text = CONFIG.read_text(encoding="utf-8")
    sec = re.search(r"^\[strategy\.sleeves\].*$", text, re.M)
    if not sec:
        raise ValueError("config.toml 里没有 [strategy.sleeves] 这一节")
    nxt = re.search(r"^\[", text[sec.end():], re.M)
    end = sec.end() + (nxt.start() if nxt else len(text) - sec.end())
    body = text[sec.end():end]
    m = re.search(r"^(stocks\s*=\s*\[)([^\]\n]*)(\])", body, re.M)
    if not m:
        raise ValueError("[strategy.sleeves] 里的 stocks 不是写在一行的列表，请手动修改 config.toml")
    items = re.findall(r'"([^"]+)"', m.group(2))
    if symbol in items:
        raise ValueError(f"{symbol} 已经在候选池里")
    inner = m.group(2).rstrip()
    new_line = m.group(1) + (f'{inner}, "{symbol}"' if items else f'"{symbol}"') + m.group(3)
    new_body = body[:m.start()] + new_line + body[m.end():]
    new_text = text[:sec.end()] + new_body + text[end:]

    shutil.copyfile(CONFIG, CONFIG.with_suffix(".toml.bak"))
    CONFIG.write_text(new_text, encoding="utf-8")
    try:
        if symbol not in tomllib.loads(new_text)["strategy"]["sleeves"]["stocks"]:
            raise ValueError("写入后没有生效")
    except Exception:
        CONFIG.write_text(text, encoding="utf-8")
        raise
