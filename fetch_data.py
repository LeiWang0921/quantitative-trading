"""从盈透下载复权日线，存到 data/history/<标的>.csv，供 backtest.py 和 notebooks 使用。

每个 CSV 的列：
  date, open, high, low, close   复权后的开盘、最高、最低、收盘价
  volume                         成交量（股数）
  hist_vol                       历史波动率：过去 30 天实际涨跌幅度，按年化计（0.25 = 25%）
  implied_vol                    隐含波动率：从期权价格反推出的市场对未来 30 天波动的预期，年化

用法：
  python fetch_data.py              下载当前策略（config.toml 的 strategy.kind）的全部标的和对比基准 SPY（backtest.years 年）
  python fetch_data.py QQQ AAPL     只下载指定标的
  python fetch_data.py --research   下载 [research] 里的研究用标的（research.years 年）
  python fetch_data.py --universe   下载候选股票池（autotrader/research/universe.py，20 年，只取价格）

需要 IB Gateway 已登录。只读连接，不会下单。
"""
from __future__ import annotations

import argparse
from datetime import date

from autotrader import strategy
from autotrader.broker.ibkr import IBKRBroker
from autotrader.config import ibkr_port, load_config
from autotrader.history import history_file, save_bars


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*", help="要下载的标的，默认用 config.toml 里的")
    parser.add_argument("--research", action="store_true", help="下载 [research] 里的研究用标的")
    parser.add_argument("--universe", action="store_true", help="下载候选股票池（只取价格，较快）")
    args = parser.parse_args()
    if args.universe:
        from autotrader.research.universe import SYMBOLS
        symbols, years = args.symbols or SYMBOLS, 20
    elif args.research:
        symbols, years = args.symbols or cfg["research"]["symbols"], cfg["research"]["years"]
    else:
        # 当前策略的全部标的，再加上对比基准 SPY（backtest.py 和 candidate_charts.py 用）
        default = strategy.symbols(strategy.load(cfg))
        symbols, years = args.symbols or default + [s for s in ["SPY"] if s not in default], cfg["backtest"]["years"]

    ib = cfg["ibkr"]
    # client_id + 20：避免和正在运行的 run.py 冲突
    broker = IBKRBroker(ib["host"], ibkr_port(cfg), ib["client_id"] + 20, readonly=True,
                        exchanges=cfg["research"]["exchanges"])
    broker.connect()
    try:
        for symbol in symbols:
            if args.universe and history_file(symbol).exists():
                first = history_file(symbol).read_text(encoding="utf-8").splitlines()[1].split(",")[0]
                if first <= f"{date.today().year - years}-12-31":
                    print(f"{symbol}: 本地已有 {first} 起的更长历史，跳过")
                    continue
            # 研究用的标的（如 SPY、IEF、GLD）按研究的年限下载，避免把研究需要的长历史截短
            n = max(years, cfg["research"]["years"]) if symbol in cfg["research"]["symbols"] else years
            bars = broker.adjusted_history(symbol, n)
            if not bars:
                print(f"{symbol}: 没有取到数据")
                continue
            extra = {} if args.universe else {
                "hist_vol": broker.daily_values(symbol, n, "HISTORICAL_VOLATILITY"),
                "implied_vol": broker.daily_values(symbol, n, "OPTION_IMPLIED_VOLATILITY"),
            }
            save_bars(symbol, bars, extra)
            print(f"{symbol}: {len(bars)} 根日线 {bars[0].date} ~ {bars[-1].date} → {history_file(symbol)}")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
