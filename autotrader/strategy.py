"""实盘 / 模拟盘使用的策略（原理见 docs/防守策略研究报告.md）。config.toml 的 strategy.kind 选择用哪一个：

  "sleeves"（方案 B2，当前）：个股为主 + 美债 / 黄金防守
      每只个股的满额比例 = 趋势信号 × min(1, 20% ÷ 这只股票最近 20 日波动)
      候选股多于 max_holdings（7）只时，每天按满额比例排名，持有前 7 只，每只一份资金 70% / 7 = 10%；
      已持有的股票排名还在缓冲范围内就保留，避免在第 7 名附近来回换；满额比例为 0 的股票不占名额、不受缓冲保护
      （候选股不多于 7 只时全部持有，每只 70% / 候选数，即方案 B1）
      防守资产（IEF、GLD）各 15%，加上个股没用完的钱平均分给它们；低于自己 200 日均线时那一份留作现金；
      防守资产也按 min(1, 15% ÷ 自己最近 20 日波动) 缩放（defense_vol_target），缩下来的部分留作现金
      逢跌加仓（dip_threshold）：SPY 在 300 日均线之上、但比最近 63 日高点跌超 8% 时，
      仍在自己 300 日均线之上的股票按满额持有（押注牛市中的急跌会较快反弹）

  "spy_ief"（之前的方案）：SPY 仓位 = 趋势信号 × min(1, 10% ÷ SPY 最近 20 日波动)，带申请失业金过滤；
      剩余资金在 IEF 高于 200 日均线时放 IEF，否则现金

趋势信号 = 100/150/200/250/300 日五条均线“价格在均线之上”的加权比例（0~1，权重见 config.toml 的 trend_weights），
每条带 1% 缓冲。
计算函数和研究代码（autotrader/research/strategies.py 的 sleeves_core / defensive_core）是同一套，
所以模拟盘的决策和回测完全一致。策略只做纯计算，不碰券商。
"""
from __future__ import annotations

import pandas as pd

from .research.data import Market
from .research.strategies import (dip_signal, economy_filter, realized_vol, sleeves_core, trend_ensemble,
                                  trend_signal, vol_weight)


def max_holdings(params: dict) -> int:
    """实际持有的个股数：候选股和 max_holdings 取小。"""
    return min(params.get("max_holdings") or len(params["stocks"]), len(params["stocks"]))


def rank_buffer(params: dict) -> int:
    """排名缓冲："auto" = 候选股数 ÷ 4（取整）；也可以直接写数字。
    最多取 候选数 - 持有数 - 1：保证排在最后一名的已持有股票总能被换掉
    （否则候选只比持有数多一两只时，名单永远不会变）。"""
    n, k = len(params["stocks"]), max_holdings(params)
    b = params.get("rank_buffer", "auto")
    b = n // 4 if b == "auto" else int(b)
    return max(0, min(b, n - k - 1))


def sleeves_kwargs(params: dict) -> dict:
    """config.toml 的参数 → sleeves_core 的参数（run.py 和 backtest.py 共用，保证两边一致）。"""
    return dict(stocks=params["stocks"], defense=params["defense"], stock_share=params["stock_share"],
                target=params["vol_target"], windows=tuple(params["trend_windows"]), band=params["trend_band"],
                defense_n=params["defense_trend_window"], lookback=params["vol_lookback"],
                trend_weights=params.get("trend_weights"), max_holdings=max_holdings(params),
                rank_buffer=rank_buffer(params), defense_vol_target=params.get("defense_vol_target"),
                dip_threshold=params.get("dip_threshold") or None, dip_market=params.get("dip_market", "SPY"))


def load(cfg: dict) -> dict:
    """把 [strategy] 的共用参数和当前策略（kind）的专用参数合并成一个字典。"""
    common = {k: v for k, v in cfg["strategy"].items() if not isinstance(v, dict)}
    return common | cfg["strategy"][common["kind"]]


def symbols(params: dict) -> list[str]:
    if params["kind"] == "sleeves":
        return list(params["stocks"]) + list(params["defense"])
    return [params["stock"], params["bond"]]


def data_symbols(params: dict) -> list[str]:
    """需要下载价格的标的 = 交易的标的 + 只用来算信号的标的（逢跌加仓看 SPY）。"""
    extra = [params.get("dip_market", "SPY")] if params.get("dip_threshold") else []
    return symbols(params) + [s for s in extra if s not in symbols(params)]


def exit_symbols(params: dict) -> list[str]:
    """要清仓的旧持仓：exit_symbols 里不属于当前策略的标的。"""
    return [s for s in params.get("exit_symbols", []) if s not in symbols(params)]


def history_days(params: dict) -> int:
    """需要多少根日线：最长均线 + 足够长的预热（均线缓冲带有“记忆”，多给一些历史让状态稳定）。"""
    return max(params["trend_windows"]) + 500


def target_weights(closes: pd.DataFrame, params: dict,
                   economy_good: pd.Series | None = None) -> tuple[dict[str, float], dict]:
    """closes：复权收盘价，行 = 日期（升序），列 = 当前策略的全部标的。
    economy_good：经济是否没有转差（只有 spy_ief 用）。
    返回 (每个标的的目标权重, 用于日志的中间结果)。权重合计 <= 1，其余是现金。"""
    need = max(params["trend_windows"]) + params["vol_lookback"]
    cols = data_symbols(params)
    if len(closes) < need or closes[cols].iloc[-need:].isna().any().any():
        raise ValueError(f"数据不足：至少需要 {need} 根完整的日线，只有 {len(closes)} 根")
    if params["kind"] == "sleeves":
        return _sleeves(closes[cols], params)
    return _spy_ief(closes, params, economy_good)


def _sleeves(closes: pd.DataFrame, params: dict) -> tuple[dict[str, float], dict]:
    windows, band = tuple(params["trend_windows"]), params["trend_band"]
    rets = closes.pct_change()
    m = Market(rets, pd.Series(0.0, index=closes.index), closes)
    frame, size, score, chosen = sleeves_core(m, **sleeves_kwargs(params), detail=True)
    w = frame.iloc[-1]
    rank = score.iloc[-1].rank(ascending=False, method="first")
    info = {"date": closes.index[-1].strftime("%Y-%m-%d"), "assets": {}, "holdings": max_holdings(params),
            "buffer": rank_buffer(params)}
    if params.get("dip_threshold"):
        mk = closes[params.get("dip_market", "SPY")]
        info["dip"] = {"market": params.get("dip_market", "SPY"), "threshold": params["dip_threshold"],
                       "drawdown": float(mk.iloc[-1] / mk.iloc[-63:].max() - 1),
                       "bull": bool(trend_signal(mk, 300, band).iloc[-1] > 0),
                       "on": bool(dip_signal(mk, params["dip_threshold"], 63, 300, band).iloc[-1])}
    for s in params["stocks"]:
        info["assets"][s] = {"trend": float(trend_ensemble(closes[s], windows, band, params.get("trend_weights")).iloc[-1]),
                             "vol": float(realized_vol(rets[s], params["vol_lookback"]).iloc[-1]),
                             "size": float(size[s].iloc[-1]), "rank": int(rank[s]), "chosen": bool(chosen[s].iloc[-1])}
    for d in params["defense"]:
        info["assets"][d] = {"trend": float(trend_signal(closes[d], params["defense_trend_window"], band).iloc[-1]),
                             "vol": float(realized_vol(rets[d], params["vol_lookback"]).iloc[-1])}
    return {s: float(w[s]) for s in symbols(params)}, info


def _spy_ief(closes: pd.DataFrame, params: dict, economy_good: pd.Series | None) -> tuple[dict[str, float], dict]:
    stock, bond = params["stock"], params["bond"]
    windows, band = tuple(params["trend_windows"]), params["trend_band"]
    raw_trend = trend_ensemble(closes[stock], windows, band)
    trend = economy_filter(raw_trend, economy_good)
    sizing = vol_weight(closes[stock].pct_change(), params["vol_target"], params["vol_lookback"])
    bond_ok = trend_signal(closes[bond], params["bond_trend_window"], band)
    w_stock = float((trend * sizing).iloc[-1])
    w_bond = float(((1 - trend * sizing) * bond_ok).iloc[-1])
    info = {
        "date": closes.index[-1].strftime("%Y-%m-%d"),
        "raw_trend": float(raw_trend.iloc[-1]),
        "trend": float(trend.iloc[-1]),
        "vol": float(realized_vol(closes[stock].pct_change(), params["vol_lookback"]).iloc[-1]),
        "vol_scale": float(sizing.iloc[-1]),
        "bond_trend": float(bond_ok.iloc[-1]),
    }
    return {stock: w_stock, bond: w_bond}, info
