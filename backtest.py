"""回测 config.toml 里当前配置的策略（和 run.py 用的是同一个策略、同一组参数、同一套调仓规则），并和基准对比。
用研究用的回测引擎：次日开盘成交，计交易成本和现金利息。

用法：
  python backtest.py

先运行 fetch_data.py 下载当前策略全部标的的数据。
逐日净值保存在 results/backtest_strategy.csv。完整的研究和图表见 research.py 和 notebooks/defensive_research.ipynb。
"""
from __future__ import annotations

import pandas as pd

from autotrader import strategy
from autotrader.config import RESULTS_DIR, load_config
from autotrader.research import data, economy, engine, metrics
from autotrader.research import strategies as S


def candidates(p: dict, m: data.Market) -> dict[str, pd.DataFrame]:
    """当前策略 + 对照组的目标权重。"""
    windows = tuple(p["trend_windows"])
    if p["kind"] == "sleeves":
        stocks, defense = p["stocks"], p["defense"]
        return {
            "策略": S.sleeves_core(m, **strategy.sleeves_kwargs(p)),
            f"{len(stocks)}只个股一直持有（等额）": S.static(m, {s: 1 / len(stocks) for s in stocks}),
            "SPY一直持有": S.buy_hold(m, "SPY"),
        }
    stock, bond = p["stock"], p["bond"]
    good = None if p["economy_filter"] == "none" else economy.economy_good(p["economy_filter"], m.close.index)
    return {
        "策略": S.defensive_core(m, stock, bond, p["vol_target"], windows=windows, band=p["trend_band"],
                               bond_n=p["bond_trend_window"], lookback=p["vol_lookback"], economy_good=good),
        f"一直持有{stock}": S.buy_hold(m, stock),
        "60/40": S.static(m, {stock: 0.6, bond: 0.4}),
    }


def main() -> None:
    cfg = load_config()
    p, bt = strategy.load(cfg), cfg["backtest"]
    symbols = strategy.symbols(p)
    extra = ["SPY"] if p["kind"] == "sleeves" else []
    # IEF 在 2002-03 年的数据很稀疏。个股方案允许个别股票上市较晚（上市前不参加排名），起点不受它限制
    m = data.etf_market(symbols + extra, start="2004-01-01", how="outer" if p["kind"] == "sleeves" else "inner")
    first = m.close.apply(lambda c: c.first_valid_index())
    late = first[first > first.min() + pd.Timedelta(days=30)].sort_values()
    start = first.max() + pd.Timedelta(days=500)              # 留出 300 日均线的预热时间
    if p["kind"] == "sleeves":                                 # 防守资产都有信号、并且至少“持有数量”只股票有信号
        enough = first.loc[p["stocks"]].sort_values().iloc[strategy.max_holdings(p) - 1]
        start = max(first.loc[p["defense"]].max(), enough) + pd.Timedelta(days=500)

    rows, crises, equity = {}, {}, {}
    for name, target in candidates(p, m).items():
        is_strategy = name == "策略"
        r = engine.run(target.fillna(0), m, execution="next_open", cost_bps=bt["cost_bps"],
                       band=p["rebalance_band"] if is_strategy else 0.05,
                       drift_only=p.get("drift_only", False) and is_strategy)
        ret = r.ret.loc[start:]
        rows[name] = metrics.summary(ret, m.rf, r.exposure.loc[start:], r.turnover.loc[start:])
        crises[name] = metrics.crisis_returns(ret)          # 只算回测起点之后（之前有的股票还没有数据）
        equity[name] = (1 + ret).cumprod()

    pd.set_option("display.width", 200)
    print(f"策略 {p['kind']}：{'、'.join(symbols)}")
    print(f"{start:%Y-%m-%d} ~ {m.close.index[-1]:%Y-%m-%d}  交易成本 {bt['cost_bps']}bp  次日开盘成交"
          f"（起点：防守资产和至少 {strategy.max_holdings(p) if p['kind'] == 'sleeves' else len(symbols)} 只股票有信号）")
    late = late[late > start - pd.Timedelta(days=500)]
    if len(late):
        print("回测期间才有数据的标的（之前不参加，有数据后预热 300 天才有信号）："
              + "、".join(f"{s} {d:%Y-%m-%d}" for s, d in late.items()))
    print()
    cols = ["年化收益", "年化波动", "夏普", "最大回撤", "最大回撤日", "Calmar", "最长水下(年)", "平均仓位", "年换手"]
    print(pd.DataFrame(rows).T[cols].to_string(float_format=lambda x: f"{x:.3f}"))
    print()
    print(pd.DataFrame(crises).T.dropna(axis=1, how="all").to_string(float_format=lambda x: f"{x:.1%}"))

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / "backtest_strategy.csv"
    pd.DataFrame(equity).to_csv(path, encoding="utf-8-sig")
    print(f"\n逐日净值 → {path}")


if __name__ == "__main__":
    main()
