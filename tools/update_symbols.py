"""下载全部美股和 ETF 的代码、英文名、行业和市值，存到 data/us_symbols.csv，供窗口程序“加入候选股”的离线检索使用。

来源（都是纳斯达克公开的数据，不连盈透）：
  ftp.nasdaqtrader.com 证券目录（每天更新）  nasdaqlisted.txt + otherlisted.txt，约 0.9 MB：全部代码、名称、交易所、是否 ETF
  api.nasdaq.com 选股器（stock screener）      约 2 MB：股票的行业（sector / industry）、市值、所在国家（ETF 没有）
新股上市、改名不频繁，几个月更新一次就够了：
  .venv\\Scripts\\python tools\\update_symbols.py
"""
import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autotrader.gui.symbols import US_SYMBOLS  # noqa: E402

# 纳斯达克的 FTP（网页地址 nasdaqtrader.com/dynamic/SymbolDirectory/ 现在返回“Page Not Available”）
BASE = "ftp://ftp.nasdaqtrader.com/symboldirectory/"
SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130 Safari/537.36", "Accept": "application/json, text/plain, */*"}
EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe", "V": "IEX"}
# 名字末尾的证券类别说明，如 "New Common Stock"、"Class A Ordinary Shares"、"American Depositary Shares"
SUFFIX = re.compile(r"\s+(New\s+)?(Class [A-Z]\s+)?(Common Stock|Common Shares|Ordinary Shares|American Depositary "
                    r"(Shares|Receipts)|Depositary Shares|Capital Stock)\b.*$", re.I)
FIELDS = ["symbol", "name", "exchange", "etf", "sector", "industry", "market_cap", "country"]


def fetch(name: str) -> list[dict]:
    with urllib.request.urlopen(BASE + name, timeout=60) as resp:
        lines = resp.read().decode("utf-8", errors="replace").splitlines()
    if not lines or "Security Name" not in lines[0]:
        raise SystemExit(f"{name} 的内容不是证券目录（可能网址变了），没有改动 data/us_symbols.csv")
    rows = list(csv.DictReader(lines, delimiter="|"))
    return [r for r in rows if r.get("Test Issue") == "N"]  # 最后一行是文件生成时间，也会被这一步去掉


def ib_symbol(sym: str) -> str:
    """盈透的写法：BRK.B / BRK/B → BRK B。"""
    return sym.replace(".", " ").replace("/", " ").strip()


def fetch_screener() -> dict[str, dict]:
    """代码 → 行业、市值、国家。下载失败时返回空字典（检索照样能用，只是没有行业和市值排序）。"""
    try:
        req = urllib.request.Request(SCREENER, headers=BROWSER)
        with urllib.request.urlopen(req, timeout=120) as resp:
            rows = json.loads(resp.read())["data"]["rows"]
    except Exception as e:
        print(f"选股器数据下载失败（{e}），这次只更新代码和名称")
        return {}
    return {ib_symbol(r["symbol"]): {"sector": r["sector"], "industry": r["industry"],
                                     "market_cap": r["marketCap"].split(".")[0] if r["marketCap"] else "",
                                     "country": r["country"]} for r in rows}


def main() -> None:
    out = []
    for r in fetch("nasdaqlisted.txt"):
        out.append((r["Symbol"], r["Security Name"], "NASDAQ", r["ETF"]))
    for r in fetch("otherlisted.txt"):
        out.append((r["ACT Symbol"], r["Security Name"], EXCHANGES.get(r["Exchange"], r["Exchange"]), r["ETF"]))
    extra = fetch_screener()
    rows = []
    for sym, name, exch, etf in out:
        if "$" in sym:  # 优先股等，不在策略考虑范围内
            continue
        sym = ib_symbol(sym)
        name = SUFFIX.sub("", name.split(" - ")[0]).strip()  # 去掉 " - Common Stock" 这类后缀
        x = extra.get(sym, {})
        rows.append([sym, name, exch, etf, x.get("sector", ""), x.get("industry", ""), x.get("market_cap", ""),
                     x.get("country", "")])
    US_SYMBOLS.parent.mkdir(exist_ok=True)
    with US_SYMBOLS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(sorted(rows))
    with_sector = sum(1 for r in rows if r[4])
    print(f"已保存 {len(rows)} 个代码到 {US_SYMBOLS}（其中 {with_sector} 个有行业和市值）")


if __name__ == "__main__":
    main()
