"""本地历史数据：把日线存成 CSV（每个标的一个文件），回测时直接读，不用连 Gateway。

文件位置：data/history/AAPL.csv，可以用 Excel 打开查看。
除了开高低收和成交量，还可以附加其它日度指标（如波动率），作为额外的列。
"""
from __future__ import annotations

import csv

from .broker.base import Bar
from .config import HISTORY_DIR

FIELDS = ["date", "open", "high", "low", "close", "volume"]


def history_file(symbol: str):
    return HISTORY_DIR / f"{symbol}.csv"


def save_bars(symbol: str, bars: list[Bar], extra: dict[str, dict[str, float]] | None = None) -> None:
    """extra：附加列，{列名: {日期: 数值}}；某天没有数值就留空。"""
    extra = extra or {}
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(history_file(symbol), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS + list(extra))
        for b in bars:
            writer.writerow([b.date, b.open, b.high, b.low, b.close, b.volume]
                            + [col.get(b.date, "") for col in extra.values()])


def load_bars(symbol: str) -> list[Bar]:
    path = history_file(symbol)
    if not path.exists():
        raise SystemExit(f"找不到 {path}，先运行 fetch_data.py 下载数据")
    with open(path, newline="", encoding="utf-8") as f:
        return [
            Bar(row["date"], float(row["open"]), float(row["high"]), float(row["low"]),
                float(row["close"]), float(row["volume"]))
            for row in csv.DictReader(f)
        ]
