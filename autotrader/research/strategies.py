"""候选的防守型策略。每个函数返回“目标权重”表：行 = 日期，列 = 资产，值 = 收盘后希望持有的比例。
权重合计 <= 1，剩下的部分是现金（按国库券利率计息）。所有信号只用当天收盘及以前的数据。

单一资产（只在一个股票资产和现金之间切换）：
  buy_hold        一直持有
  static          固定比例，如 60% 股票 + 40% 现金
  sma_trend       长周期均线趋势：价格在 N 日均线之上持有，跌破就换成现金
  monthly_sma     同上，但只在每月最后一个交易日检查一次（换手更少）
  tsmom           时间序列动量：过去 12 个月跑赢现金就持有，否则空仓
  vol_target      波动率目标：仓位 = 目标波动 ÷ 近期实际波动，波动大时自动减仓
  trend_vol       趋势 × 波动率目标
  tipp            回撤控制（TIPP）：离“最高点 × (1 - 回撤上限)”越近仓位越低

多资产：
  inverse_vol     按波动率倒数分配（波动小的资产多配），可再加组合层面的波动率目标
  gtaa            每个资产等权，各自用 10 个月均线决定持有还是换成现金（Faber 的 GTAA）
  dual_momentum   双动量：美股、海外股票谁过去 12 个月更强就持有谁；都不如现金就转到债券
  risk_sleeves    “袖子”组合：每个资产一份基础权重，各自乘以趋势信号和波动率目标
  defensive_core  研究最初选出的方案：多周期趋势 × 波动率目标的股票仓位，其余放国债（国债也要在趋势之上）或现金
  sleeves_core    ★ 当前模拟盘的方案：几只个股各占一份（各自趋势 × 波动率目标），其余放美债 / 黄金（各自趋势过滤）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import TRADING_DAYS, Market


def _frame(m: Market, values: dict[str, pd.Series | float]) -> pd.DataFrame:
    out = pd.DataFrame(index=m.close_ret.index, columns=m.assets, dtype=float).fillna(0.0)
    for asset, value in values.items():
        out[asset] = value
    return out


def month_end(index: pd.DatetimeIndex) -> pd.Series:
    """每个月最后一个交易日为 True。数据的最后一天：只有下一个工作日已经是下个月才算月末。"""
    s = pd.Series(index.to_period("M"), index=index)
    out = s != s.shift(-1)
    if len(index):
        out.iloc[-1] = (index[-1] + pd.offsets.BDay(1)).month != index[-1].month
    return out


def monthly(signal: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """只在月末取信号，月中保持不变。"""
    mask = month_end(signal.index)
    return signal.where(mask).ffill()


def compound(ret: pd.Series, days: int) -> pd.Series:
    """最近 days 天的累计收益。"""
    return np.expm1(np.log1p(ret).rolling(days).sum())


def realized_vol(ret: pd.Series, lookback: int | str) -> pd.Series:
    """年化波动率估计。lookback 为整数 = 最近 N 日标准差；"ewma" = 指数加权（RiskMetrics，λ=0.94）。"""
    if lookback == "ewma":
        return np.sqrt((ret ** 2).ewm(alpha=0.06, adjust=False).mean() * TRADING_DAYS)
    return ret.rolling(lookback).std() * np.sqrt(TRADING_DAYS)


# ---------------------------------------------------------------- 单一资产

def buy_hold(m: Market, asset: str) -> pd.DataFrame:
    return _frame(m, {asset: 1.0})


def static(m: Market, weights: dict[str, float]) -> pd.DataFrame:
    return _frame(m, weights)


def trend_signal(close: pd.Series, n: int = 200, band: float = 0.0) -> pd.Series:
    """价格在 N 日均线之上 = 1，否则 0。band > 0 时加缓冲：
    涨过均线 × (1 + band) 才买入，跌破均线 × (1 - band) 才卖出，减少在均线附近来回切换。"""
    sma = close.rolling(n).mean()
    up, down = close > sma * (1 + band), close < sma * (1 - band)
    state = pd.Series(np.nan, index=close.index)
    state[up], state[down] = 1.0, 0.0
    return state.ffill().fillna(0.0).where(sma.notna(), np.nan)


def sma_trend(m: Market, asset: str, n: int = 200, band: float = 0.0) -> pd.DataFrame:
    return _frame(m, {asset: trend_signal(m.close[asset], n, band)})


def monthly_sma(m: Market, asset: str, months: int = 10) -> pd.DataFrame:
    close = m.close[asset]
    month_close = close[month_end(close.index)]
    above = (month_close > month_close.rolling(months).mean()).astype(float)
    above[month_close.rolling(months).mean().isna()] = np.nan
    return _frame(m, {asset: above.reindex(close.index).ffill()})


def tsmom(m: Market, asset: str, lookback: int = 252) -> pd.DataFrame:
    asset_ret = m.close[asset].pct_change(lookback)
    cash_ret = compound(m.rf, lookback)
    signal = (asset_ret > cash_ret).astype(float).where(asset_ret.notna())
    return _frame(m, {asset: monthly(signal)})


def vol_weight(ret: pd.Series, target: float, lookback: int | str = 20, cap: float = 1.0) -> pd.Series:
    return (target / realized_vol(ret, lookback)).clip(upper=cap)


def vol_target(m: Market, asset: str, target: float = 0.12, lookback: int | str = 20) -> pd.DataFrame:
    return _frame(m, {asset: vol_weight(m.close_ret[asset], target, lookback)})


def trend_vol(m: Market, asset: str, n: int = 200, target: float = 0.12,
              lookback: int | str = 20, band: float = 0.0) -> pd.DataFrame:
    w = trend_signal(m.close[asset], n, band) * vol_weight(m.close_ret[asset], target, lookback)
    return _frame(m, {asset: w})


def tipp(m: Market, asset: str, limit: float = 0.20, multiplier: float = 4.0) -> pd.DataFrame:
    """回撤控制（TIPP，带棘轮的固定比例组合保险）：
    底线 = 历史最高资产 × (1 - limit)；仓位 = multiplier × (资产 - 底线) / 资产，最多 100%。
    资产越接近底线仓位越低。缺点：一旦贴近底线，几乎全是现金，很难再回到高点（“现金锁定”）。
    这里用不计成本的近似净值来算仓位（实际回测时引擎会计成本）。"""
    r = m.close_ret[asset].fillna(0).to_numpy()
    rf = m.rf.to_numpy()
    w = np.zeros(len(r))
    equity, peak = 1.0, 1.0
    for i in range(len(r)):
        floor = peak * (1 - limit)
        w[i] = min(1.0, max(0.0, multiplier * (equity - floor) / equity))
        if i + 1 < len(r):  # 今天收盘定的仓位，承担明天的涨跌
            equity *= 1 + w[i] * r[i + 1] + (1 - w[i]) * rf[i + 1]
            peak = max(peak, equity)
    return _frame(m, {asset: pd.Series(w, index=m.close_ret.index)})


# ---------------------------------------------------------------- 多资产

def inverse_vol(m: Market, assets: list[str], lookback: int = 63, target: float | None = None,
                trend_n: int | None = None) -> pd.DataFrame:
    """按波动率倒数分配，每月末调整一次。
    target：组合层面的波动率目标（用过去 lookback 天的协方差估计组合波动）；None 表示不做。
    trend_n：每个资产再加一个 N 日均线过滤，跌破均线的资产那一份换成现金。"""
    rets = m.close_ret[assets]
    inv = 1 / (rets.rolling(lookback).std() * np.sqrt(TRADING_DAYS))
    w = inv.div(inv.sum(axis=1), axis=0)
    if target is not None:
        port_vol = pd.Series(np.nan, index=rets.index)
        ends = month_end(rets.index)
        for date in rets.index[ends.to_numpy()]:
            window = rets.loc[:date].tail(lookback)
            weights = w.loc[date]
            if len(window) == lookback and weights.notna().all():
                cov = window.cov().to_numpy() * TRADING_DAYS
                port_vol[date] = float(np.sqrt(weights @ cov @ weights))
        scale = (target / port_vol.ffill()).clip(upper=1.0)
        w = w.mul(scale, axis=0)
    w = monthly(w)
    if trend_n is not None:
        trend = pd.DataFrame({a: trend_signal(m.close[a], trend_n) for a in assets})
        w = w * trend
    return _frame(m, {a: w[a] for a in assets})


def gtaa(m: Market, assets: list[str], months: int = 10) -> pd.DataFrame:
    weight = 1 / len(assets)
    return _frame(m, {a: monthly_sma(m, a, months)[a] * weight for a in assets})


def dual_momentum(m: Market, us: str, intl: str, bond: str, lookback: int = 252) -> pd.DataFrame:
    mom = m.close[[us, intl]].pct_change(lookback)
    cash = compound(m.rf, lookback)
    best = mom.fillna(-np.inf).idxmax(axis=1)  # 前 lookback 天还算不出动量，之后会被 where 去掉
    risk_on = mom.max(axis=1) > cash
    out = {}
    for asset in (us, intl):
        out[asset] = ((best == asset) & risk_on).astype(float)
    out[bond] = (~risk_on).astype(float)
    frame = pd.DataFrame(out).where(mom.notna().all(axis=1))
    frame = monthly(frame)
    return _frame(m, {a: frame[a] for a in frame})


def risk_sleeves(m: Market, base: dict[str, float], target: float, n: int = 200, band: float = 0.02,
                 lookback: int | str = 20) -> pd.DataFrame:
    """“袖子”组合：每个资产 a 是一个独立的袖子，权重 = 基础权重 × 趋势信号 × min(上限, 目标波动 / 实际波动)。
    上限 = 1 / 基础权重（单个资产最多占满整个组合），各袖子合计超过 100% 时等比例缩小（不加杠杆）。
    例：base = {"SPY": 0.6, "IEF": 0.4}, target = 0.15
      SPY 在 200 日均线之上、近期波动 20% → SPY 权重 = 0.6 × 1 × 0.75 = 45%
      IEF 跌破均线 → IEF 权重 = 0，这部分是现金"""
    w = pd.DataFrame({
        a: b * trend_signal(m.close[a], n, band) * vol_weight(m.close_ret[a], target, lookback, cap=1 / b)
        for a, b in base.items()
    })
    w = w.div(w.sum(axis=1).clip(lower=1.0), axis=0)
    return _frame(m, {a: w[a] for a in base})


def _normalized(weights, n: int) -> np.ndarray:
    w = np.full(n, 1 / n) if weights is None else np.asarray(weights, dtype=float)
    if len(w) != n or (w < 0).any() or w.sum() <= 0:
        raise ValueError(f"均线权重必须是 {n} 个非负数：{weights}")
    return w / w.sum()


def trend_ensemble(close: pd.Series, windows=(50, 100, 150, 200, 250), band: float = 0.02,
                   weights=None) -> pd.Series:
    """多周期趋势：几条均线信号的加权平均（weights 不给就是等权）。例如 5 条等权、3 条“在均线之上”→ 0.6。
    比单一均线切换更平滑，也不依赖某一个特定参数。"""
    signals = pd.concat([trend_signal(close, n, band) for n in windows], axis=1)
    if weights is None:
        return signals.mean(axis=1, skipna=False)
    return (signals * _normalized(weights, len(windows))).sum(axis=1, min_count=len(windows))


def trend_strength(close: pd.Series, windows=(100, 150, 200, 250, 300), weights=None,
                   vol_lookback: int = 60) -> pd.Series:
    """趋势强度（连续值）：价格平均高出这几条均线多少（按均线权重加权），再除以最近 vol_lookback 日的年化波动。
    用来给“趋势信号 × 波动缩放”相同的股票排先后（例如几只都是满额 100%）。"""
    w = _normalized(weights, len(windows))
    distance = sum(wi * (close / close.rolling(n).mean() - 1) for n, wi in zip(windows, w))
    return distance / realized_vol(close.pct_change(), vol_lookback)


def select_top(score: pd.DataFrame, k: int, buffer: int = 0, eligible: pd.DataFrame | None = None) -> pd.DataFrame:
    """每天按 score 从高到低选出 k 只（score 为 NaN 的不参加），返回 0/1 表。
    缓冲：已经选中的只要排名还在前 k + buffer 名就保留，空出来的名额按排名补上，避免在第 k 名附近来回换。
    缓冲最多取“当天有得分的候选数 - k - 1”，保证排在最后一名的已选股票总能被换掉。
    eligible（True/False 表，可选）：当天不合格的股票既不能新选入，也不受缓冲保护。sleeves_core 传入“满额比例 > 0”：
      满额比例为 0 的股票本来就没有仓位，留着它的名额省不了任何交易，却会挡住排在后面、有仓位的股票
      （2026-11-10 发现：NVDA 满额比例 0、排第 9，YUMC 满额比例 84% 却进不来）。
    逐日回放全部历史，所以结果只取决于价格历史，不需要另外保存“当前持有哪几只”。"""
    s = score.to_numpy(dtype=float)
    ok_all = (np.ones_like(s, dtype=bool) if eligible is None
              else eligible.reindex(index=score.index, columns=score.columns).fillna(False).to_numpy(dtype=bool))
    out = np.zeros_like(s)
    held = np.zeros(s.shape[1], dtype=bool)
    for t in range(len(s)):
        valid = ~np.isnan(s[t])
        ok = valid & ok_all[t]
        b = max(0, min(buffer, int(valid.sum()) - k - 1))
        order = np.argsort(np.where(valid, -s[t], np.inf), kind="stable")   # 得分从高到低
        rank = np.empty(len(order), dtype=int)
        rank[order] = np.arange(len(order))                                 # 0 = 第一名
        held = held & ok & (rank < k + b)
        for j in order:
            if held.sum() >= k or not valid[j]:
                break
            if ok[j]:
                held[j] = True
        out[t] = held
    return pd.DataFrame(out, index=score.index, columns=score.columns)


def economy_filter(trend: pd.Series, economy_good: pd.Series | None) -> pd.Series:
    """经济过滤：趋势转弱（< 1）但经济没有转差（economy_good == 1）时，按趋势满分处理，不减仓。
    economy_good 为 None 或某天缺数据（NaN）时不过滤，即照常按趋势减仓（更保守）。"""
    if economy_good is None:
        return trend
    good = economy_good.reindex(trend.index) == 1
    return trend.where(~((trend < 1) & good), 1.0)


def defensive_core(m: Market, stock: str, bond: str | None = None, target: float = 0.10,
                   windows=(100, 150, 200, 250, 300), band: float = 0.01, bond_n: int = 200,
                   lookback: int | str = 20, economy_good: pd.Series | None = None) -> pd.DataFrame:
    """★ 最终方案。每天收盘后：
      股票仓位 = 多周期趋势信号（0~1）× min(1, 目标波动 / 最近 20 日波动)
        多周期趋势信号 = 100/150/200/250/300 日五条均线里“价格在均线之上”的比例，各带 1% 缓冲
      剩余资金 → 国债（只在国债自己高于 200 日均线时；否则留作现金）
    bond=None 时剩余资金全部是现金。

    参数怎么来的（详见 docs/防守策略研究报告.md）：
      均线周期：文献常用的 200 日附近取一组，平均后不依赖某一个参数；比较过单一 200 日、50-250、100-300，差别不大
      缓冲 1%：不加缓冲时债券在均线附近来回切换，换手率每年 12 倍以上；2% 在 1930 年代卖得太慢
      波动率目标 10%：让 1951 年以来和 2005 年以来的最大回撤都在 20% 以内，并留出余量
    economy_good：可选的经济过滤（见 economy_filter 和 autotrader/research/economy.py）"""
    trend = economy_filter(trend_ensemble(m.close[stock], windows, band), economy_good)
    w_stock = trend * vol_weight(m.close_ret[stock], target, lookback)
    weights = {stock: w_stock}
    if bond is not None:
        weights[bond] = (1 - w_stock) * trend_signal(m.close[bond], bond_n, band)
    return _frame(m, weights)


def dip_signal(market_close: pd.Series, threshold: float, window: int = 63, guard: int = 300,
               band: float = 0.01, min_days: int = 0) -> pd.Series:
    """逢跌加仓的开关（True/False）。
    触发：大盘在 guard 日均线之上（长期牛市），且比最近 window 日的最高收盘价下跌超过 threshold。
    min_days > 0 时，最后一次满足触发条件之后，开关至少再维持 min_days 个交易日（含触发当天），
      即使大盘已经反弹回阈值以内；但大盘跌破 guard 日均线（判断为熊市）时立即关闭。
      不设最短天数时，大盘一反弹回阈值以内就关闭：2006 年以来开关的中位数只持续 2 天，
      在阈值附近反复开关，每次关闭都会在反弹刚开始时减仓（2026-11-10 的回测，见报告第 3.6 节）。"""
    drawdown = market_close / market_close.rolling(window).max() - 1
    bull = trend_signal(market_close, guard, band)
    raw = ((drawdown <= -threshold) & (bull > 0)).fillna(False)
    if min_days <= 0:
        return raw
    raw_a, bull_a = raw.to_numpy(), (bull > 0).to_numpy()
    out = np.zeros(len(raw_a), dtype=bool)
    last = -10**9
    for t in range(len(raw_a)):
        if raw_a[t]:
            last = t
        out[t] = raw_a[t] or (t - last < min_days and bull_a[t])
    return pd.Series(out, index=market_close.index)


def sleeves_core(m: Market, stocks: list[str], defense: list[str], stock_share: float = 0.70,
                 target: float = 0.20, windows=(100, 150, 200, 250, 300), band: float = 0.01,
                 defense_n: int = 200, lookback: int | str = 20, trend_weights=None,
                 max_holdings: int | None = None, rank_buffer: int = 0, defense_vol_target: float | None = None,
                 dip_threshold: float | None = None, dip_market: str = "SPY", dip_window: int = 63,
                 dip_guard: int = 300, dip_min_days: int = 0, detail: bool = False):
    """★ 个股为主、美债 / 黄金防守的组合。每天收盘后：
      每只个股的满额比例 = 多周期趋势信号（0~1，均线按 trend_weights 加权）× min(1, 目标波动 / 这只股票最近 20 日波动)
      候选股不多于 max_holdings（或没设）时：全部持有，每只一份资金 = stock_share / 候选数（方案 B1）
      候选股多于 max_holdings 时：每天按满额比例排名（相同时比趋势强度），持有前 max_holdings 只，
        每只一份资金 = stock_share / max_holdings；缓冲 rank_buffer 见 select_top（方案 B2）；
        满额比例为 0 的股票不占名额，也不受缓冲保护（2026-11-10 起）
      防守部分：每个防守资产 (1 - stock_share) / 防守资产数（例如 30% / 2 = 15%），
        再加上个股没用完的钱（平均分给各防守资产）；防守资产自己低于 200 日均线时，它那一份留作现金。
        defense_vol_target 不为 None 时，防守资产也乘 min(1, 目标 / 自己最近 20 日波动)，缩下来的部分留作现金
        （和股票同一个原则：黄金、国债自己剧烈波动时，常常和股票一起下跌，例如 2008、2020 年 3 月、2026 年初）。
      逢跌加仓（dip_threshold 不为 None 时）：大盘 dip_market 仍在 dip_guard 日均线之上（长期牛市），
        但比最近 dip_window 日的高点下跌超过 dip_threshold 时，自己仍在 dip_guard 日均线之上的股票按满额比例 100% 处理
        （不因短期均线跌破或波动升高而减仓），押注牛市中的急跌会较快反弹；
        dip_min_days > 0 时，开关在最后一次触发后至少维持这么多交易日（大盘跌破长期均线时立即关闭），见 dip_signal。
    detail=True 时返回 (目标权重, 满额比例, 排名得分, 是否入选) 四张表，供日志和报告使用。"""
    k = len(stocks) if max_holdings is None else min(max_holdings, len(stocks))
    sleeve = stock_share / k
    size = pd.DataFrame({s: trend_ensemble(m.close[s], windows, band, trend_weights)
                         * vol_weight(m.close_ret[s], target, lookback) for s in stocks})
    if dip_threshold is not None:
        on = dip_signal(m.close[dip_market], dip_threshold, dip_window, dip_guard, band, dip_min_days)
        for s in stocks:
            long_ok = trend_signal(m.close[s], dip_guard, band)
            size[s] = size[s].where(~on, np.maximum(size[s], long_ok).where(size[s].notna()))
    if k < len(stocks):
        strength = pd.DataFrame({s: trend_strength(m.close[s], windows, trend_weights) for s in stocks})
        score = size + 1e-4 * strength.clip(-50, 50).where(size.notna())
        chosen = select_top(score, k, rank_buffer, eligible=size > 0)  # 满额比例为 0 的不占名额、不受缓冲保护
        ready = score.notna().any(axis=1)                       # 预热期（还没有任何股票有信号）保持 NaN
        held = size.where(chosen > 0, 0.0).where(ready)
    else:
        score, chosen, held = size, size.notna().astype(float), size
    weights = {s: sleeve * held[s] for s in stocks}
    freed = stock_share - pd.DataFrame(weights).sum(axis=1, skipna=False)
    base = (1 - stock_share) / len(defense)
    for d in defense:
        weights[d] = (base + freed / len(defense)) * trend_signal(m.close[d], defense_n, band)
        if defense_vol_target is not None:
            weights[d] = weights[d] * vol_weight(m.close_ret[d], defense_vol_target, lookback)
    frame = _frame(m, weights)
    return (frame, size, score, chosen) if detail else frame
