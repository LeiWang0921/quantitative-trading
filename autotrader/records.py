"""run.py 每次运行留下的记录，供窗口程序（gui.py）读取。

  data/last_run.json    最近一次运行的决策：排名、打分、目标权重、价格、持仓、账户、下单情况
  data/nav_history.csv  每次运行时的账户净值（画净值曲线用）。第一次读取时从 logs/trader.log 补齐以前的记录
  data/bars/<代码>.csv  每次运行时下载的最近约 800 个交易日的复权日线（窗口程序画“近 1 年 K 线”用），每次整份覆盖
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime

from .config import DATA_DIR, HISTORY_DIR, LOGS_DIR

LAST_RUN = DATA_DIR / "last_run.json"
NAV_HISTORY = DATA_DIR / "nav_history.csv"
NAV_FIELDS = ["time", "mode", "nav", "currency", "usd_cash", "strategy_usd"]
BARS_DIR = DATA_DIR / "bars"
BAR_FIELDS = ["date", "open", "high", "low", "close", "volume"]

# trader.log 里两种写法的净值行（2026-10-19 起的旧格式、2026-10-22 起的“账户：”格式）
_LOG_NAV = re.compile(r"^(\S+ \S+?),\d+ INFO (?:账户：)?模式=(\w+) .*?净值=([\d.]+) (\w+) 美元现金=([\d.]+)"
                      r"(?: 策略资产=([\d.]+))?")


def save_last_run(record: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = LAST_RUN.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    tmp.replace(LAST_RUN)  # 先写临时文件再替换：窗口程序不会读到写了一半的文件


def load_last_run() -> dict | None:
    try:
        return json.loads(LAST_RUN.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def append_nav(mode: str, nav: float, currency: str, usd_cash: float, strategy_usd: float) -> None:
    if not NAV_HISTORY.exists():
        _backfill_from_log()
    with NAV_HISTORY.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode, f"{nav:.2f}", currency,
                                f"{usd_cash:.2f}", f"{strategy_usd:.2f}"])


def load_nav(mode: str) -> list[dict]:
    """某个模式的净值记录，每天只保留最后一条，按时间升序。"""
    if not NAV_HISTORY.exists():
        _backfill_from_log()
    if not NAV_HISTORY.exists():
        return []
    by_day: dict[str, dict] = {}
    with NAV_HISTORY.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["mode"] == mode:
                by_day[row["time"][:10]] = row
    return [by_day[d] for d in sorted(by_day)]


def _backfill_from_log() -> None:
    """nav_history.csv 还不存在时，从 trader.log 里的净值行生成（只做一次）。"""
    rows = []
    try:
        with (LOGS_DIR / "trader.log").open(encoding="utf-8") as f:
            for line in f:
                m = _LOG_NAV.match(line)
                if m:
                    t, mode, nav, ccy, cash, strat = m.groups()
                    rows.append([t, mode, nav, ccy, cash, strat or ""])
    except FileNotFoundError:
        pass
    DATA_DIR.mkdir(exist_ok=True)
    with NAV_HISTORY.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(NAV_FIELDS)
        w.writerows(rows)


def save_recent_bars(symbol: str, rows: list[dict]) -> None:
    """run.py 下载的复权日线（rows：每天一个 {date, open, high, low, close, volume}），整份覆盖 data/bars/<代码>.csv。"""
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    path = BARS_DIR / f"{symbol}.csv"
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BAR_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)  # 先写临时文件再替换：窗口程序不会读到写了一半的文件


def load_recent_bars(symbol: str) -> tuple[list[dict], str]:
    """窗口程序画 K 线用的日线（按日期升序，数值已转成 float）和数据来源说明。
    优先用 run.py 每天保存的 data/bars/，没有时用 fetch_data.py 下载的 data/history/（可能不是最新）。"""
    for path, source in ((BARS_DIR / f"{symbol}.csv", "每天自动运行时更新"),
                         (HISTORY_DIR / f"{symbol}.csv", "来自 fetch_data.py 下载的历史数据，不会每天更新")):
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                rows = [{"date": r["date"][:10], **{k: float(r[k]) for k in BAR_FIELDS[1:]}}
                        for r in csv.DictReader(f) if r.get("close")]
            if rows:
                return rows, source
    return [], ""
