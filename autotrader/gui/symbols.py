"""“加入候选股”输入框的离线检索：完全在本地查，不连盈透。

数据来源（合并在一起）：
  data/us_symbols.csv             全部美股和 ETF：代码、英文名、交易所、行业、市值、国家（tools/update_symbols.py 下载）
  autotrader/gui/cn_names.py      约 600 家大公司的中文名
  autotrader/research/universe.py 研究用的候选股票池（中文名、更细的行业、简介，可以搜“银行”“芯片”）
  data/symbol_names.json          窗口程序查过的公司名
  data/history/*.csv              下载过历史数据的代码
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass

from ..config import DATA_DIR, HISTORY_DIR
from .cn_names import CN_NAMES

US_SYMBOLS = DATA_DIR / "us_symbols.csv"

# 纳斯达克选股器的行业 → 中文（尽量和 universe.py 的叫法一致）
SECTORS = {"Technology": "信息技术", "Telecommunications": "通信服务", "Health Care": "医疗保健", "Finance": "金融",
           "Real Estate": "房地产", "Consumer Discretionary": "可选消费", "Consumer Staples": "必需消费",
           "Industrials": "工业", "Basic Materials": "原材料", "Energy": "能源", "Utilities": "公用事业",
           "Miscellaneous": "其他"}
COUNTRIES = {"China": "中国", "Hong Kong": "中国香港", "Taiwan": "中国台湾", "Canada": "加拿大", "United Kingdom": "英国",
             "Japan": "日本", "Netherlands": "荷兰", "Switzerland": "瑞士", "Germany": "德国", "France": "法国",
             "Israel": "以色列", "India": "印度", "Brazil": "巴西", "Ireland": "爱尔兰", "Singapore": "新加坡",
             "South Korea": "韩国", "Australia": "澳大利亚", "Denmark": "丹麦", "Argentina": "阿根廷", "Mexico": "墨西哥"}


@dataclass
class Entry:
    symbol: str
    name: str = ""         # 英文名
    cn: str = ""           # 中文名
    sector: str = ""       # 中文行业
    exchange: str = ""
    etf: bool = False
    market_cap: float = 0.0
    country: str = ""
    note: str = ""         # 中文简介（只有研究股票池里的有，用来搜“银行”“芯片”这类词，不显示）

    def label(self) -> str:
        head = self.cn + (f"（{self.sector}）" if self.sector else "") if self.cn else (
            f"（{self.sector}）" if self.sector else "")
        tail = " · ".join(x for x in (self.exchange, "ETF" if self.etf else "",
                                      self.country if self.country and self.country != "United States" else "") if x)
        return "  ".join(p for p in (self.symbol.ljust(6), head, self.name) if p) + (f"   [{tail}]" if tail else "")


def load_index() -> dict[str, Entry]:
    index: dict[str, Entry] = {}

    def get(sym: str) -> Entry:
        return index.setdefault(sym, Entry(sym))

    if US_SYMBOLS.exists():
        with US_SYMBOLS.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                e = get(row["symbol"])
                e.name, e.exchange, e.etf = row["name"], row["exchange"], row["etf"] == "Y"
                e.sector = SECTORS.get(row.get("sector", ""), "")
                e.market_cap = float(row.get("market_cap") or 0)
                c = row.get("country", "")
                e.country = COUNTRIES.get(c, c)
    try:
        names = json.loads((DATA_DIR / "symbol_names.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        names = {}
    for sym, name in names.items():
        get(sym).name = get(sym).name or name
    for sym, cn in CN_NAMES.items():
        if sym in index or not US_SYMBOLS.exists():  # 已退市的不要因为有中文名又出现在检索里
            get(sym).cn = cn
    from ..research.universe import UNIVERSE
    for sym, cn, industry, note in UNIVERSE:  # 研究股票池的中文名和行业更细，优先用它
        e = get(sym)
        e.cn, e.sector, e.note = cn, industry, note
    for p in HISTORY_DIR.glob("*.csv"):
        get(p.stem)
    return index


def search(index: dict[str, Entry], query: str, limit: int = 12) -> list[Entry]:
    """先按匹配方式分档：代码完全相同 > 代码开头相同 > 中文名包含 > 英文名某个词开头相同 > 英文名包含 > 中文行业或简介包含；
    同一档里市值大的排前面（ETF 没有市值，排在同档股票之后）。"""
    q = query.strip()
    if not q:
        return []
    qu = q.upper()
    scored = []
    for e in index.values():
        name_u = e.name.upper()
        if e.symbol == qu:
            score = 0
        elif e.symbol.startswith(qu):
            score = 1
        elif q in e.cn:
            score = 2
        elif any(w.startswith(qu) for w in name_u.replace(".", " ").replace(",", " ").split()):
            score = 3
        elif len(qu) >= 3 and qu in name_u:
            score = 4
        elif q in e.sector or q in e.note:
            score = 5
        else:
            continue
        scored.append((score, -e.market_cap, e.symbol, e))
    scored.sort(key=lambda x: x[:3])
    return [x[3] for x in scored[:limit]]
