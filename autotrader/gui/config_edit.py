"""窗口程序对 config.toml 的改动：加入、删除候选股。

只改单行列表（[strategy.sleeves] 的 stocks、[strategy] 的 exit_symbols），其余内容（包括注释）原样保留；
改之前备份到 config.toml.bak，改完重新解析一遍，确认生效，否则还原。
"""
from __future__ import annotations

import re
import shutil
import tomllib
from typing import Callable

from ..config import ROOT

CONFIG = ROOT / "config.toml"


def _edit_list(text: str, section: str, key: str, change: Callable[[list[str]], list[str]]) -> str:
    """把 [section] 里 key = [...]（写在一行）的列表交给 change 修改，返回新的全文。"""
    sec = re.search(rf"^\[{re.escape(section)}\][^\n]*$", text, re.M)
    if not sec:
        raise ValueError(f"config.toml 里没有 [{section}] 这一节")
    nxt = re.search(r"^\[", text[sec.end():], re.M)
    end = sec.end() + (nxt.start() if nxt else len(text) - sec.end())
    body = text[sec.end():end]
    m = re.search(rf"^({key}\s*=\s*\[)([^\]\n]*)(\])", body, re.M)
    if not m:
        raise ValueError(f"[{section}] 里的 {key} 不是写在一行的列表，请手动修改 config.toml")
    items = change(re.findall(r'"([^"]+)"', m.group(2)))
    new_line = m.group(1) + ", ".join(f'"{s}"' for s in items) + m.group(3)
    return text[:sec.end()] + body[:m.start()] + new_line + body[m.end():] + text[end:]


def _write(text: str, new_text: str, check: Callable[[dict], bool]) -> None:
    shutil.copyfile(CONFIG, CONFIG.with_suffix(".toml.bak"))
    CONFIG.write_text(new_text, encoding="utf-8")
    try:
        if not check(tomllib.loads(new_text)):
            raise ValueError("写入后没有生效")
    except Exception:
        CONFIG.write_text(text, encoding="utf-8")
        raise


def add_candidate(symbol: str) -> None:
    text = CONFIG.read_text(encoding="utf-8")

    def add(items: list[str]) -> list[str]:
        if symbol in items:
            raise ValueError(f"{symbol} 已经在候选池里")
        return items + [symbol]

    new_text = _edit_list(text, "strategy.sleeves", "stocks", add)
    # 以前删掉过、又加回来：从清仓名单里拿掉（留着也不会被清仓，strategy.exit_symbols 会跳过当前策略的标的）
    new_text = _edit_list(new_text, "strategy", "exit_symbols", lambda items: [s for s in items if s != symbol])
    _write(text, new_text, lambda cfg: symbol in cfg["strategy"]["sleeves"]["stocks"])


def remove_candidate(symbol: str) -> None:
    """从候选股里删掉 symbol，同时把它加进 exit_symbols。
    run.py 只交易当前策略里的标的和 exit_symbols：不加进去的话，还持有的股票会被“遗忘”，永远不会卖出。
    没有持仓时加进去也没有影响（run.py 只清仓确实持有或有挂单的标的）。"""
    text = CONFIG.read_text(encoding="utf-8")

    def remove(items: list[str]) -> list[str]:
        if symbol not in items:
            raise ValueError(f"{symbol} 不在候选池里")
        if len(items) == 1:
            raise ValueError("候选池里至少要留一只股票")
        return [s for s in items if s != symbol]

    new_text = _edit_list(text, "strategy.sleeves", "stocks", remove)
    new_text = _edit_list(new_text, "strategy", "exit_symbols", lambda items: items if symbol in items else items + [symbol])
    _write(text, new_text, lambda cfg: symbol not in cfg["strategy"]["sleeves"]["stocks"]
           and symbol in cfg["strategy"]["exit_symbols"])
