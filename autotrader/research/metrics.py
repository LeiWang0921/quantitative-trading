"""评价指标。按你的目标，重点是“亏得少”：最大回撤、危机期间亏损、回本时间。

几个名词：
  回撤（drawdown）：  当前资产比历史最高点低了多少。最大回撤 = 历史上最惨的一次“从高点跌下来”。
  水下时间：          从高点跌下去，到重新回到这个高点，经过了多久。
  Calmar 比率：       年化收益 ÷ |最大回撤|。每承担 1% 的最大回撤换来多少年化收益，越高越好。
  夏普比率：          （年化收益 - 无风险利率）÷ 年化波动。每承担一份波动换来多少超额收益。
  Ulcer 指数：        回撤的均方根，同时反映回撤有多深、持续多久（越小越好）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# 美股历史上的主要下跌（按标普 500 / 全市场的高点和低点划分），只用于事后评价
CRISES = {
    "1929 大萧条": ("1929-09-03", "1932-06-01"),
    "1937 衰退": ("1937-03-06", "1938-03-31"),
    "1973 石油危机": ("1973-01-11", "1974-10-03"),
    "1987 黑色星期一": ("1987-08-25", "1987-12-04"),
    "1990 海湾战争": ("1990-07-16", "1990-10-11"),
    "2000 互联网泡沫": ("2000-03-24", "2002-10-09"),
    "2008 金融危机": ("2007-10-09", "2009-03-09"),
    "2011 欧债危机": ("2011-04-29", "2011-10-03"),
    "2018 四季度": ("2018-09-20", "2018-12-24"),
    "2020 新冠": ("2020-02-19", "2020-03-23"),
    "2022 加息熊市": ("2022-01-03", "2022-10-12"),
}


def drawdown(ret: pd.Series) -> pd.Series:
    equity = (1 + ret).cumprod()
    peak = equity.cummax().clip(lower=1.0)  # 起始资金 1 也算一个高点（第一天就亏也要算回撤）
    return equity / peak - 1


def years_of(index: pd.DatetimeIndex) -> float:
    """样本覆盖的日历年数。注意 1952 年以前美股周六也交易，每年约 290 个交易日，不能用 252 天折算。"""
    return (index[-1] - index[0]).days / 365.25


def periods_per_year(index: pd.DatetimeIndex) -> float:
    return (len(index) - 1) / years_of(index)


def max_underwater_years(ret: pd.Series) -> float:
    """最长水下时间（年）：从某个高点跌下去到重新回到该高点，最长经历了多久（未回本的按到最后一天算）。"""
    dd = drawdown(ret)
    dates = dd.index
    longest, peak = pd.Timedelta(0), None
    for i, value in enumerate(dd.to_numpy()):
        if value < 0 and peak is None:
            peak = dates[i - 1] if i > 0 else dates[0]  # 从高点那天开始算
        elif value >= 0 and peak is not None:
            longest, peak = max(longest, dates[i] - peak), None
    if peak is not None:
        longest = max(longest, dates[-1] - peak)
    return longest.days / 365.25


def worst_window(ret: pd.Series, days: int) -> float:
    """任意连续 days 个交易日里最差的累计收益（如最差的一个月、一年）。"""
    log = np.log1p(ret).rolling(days).sum()
    return float(np.expm1(log.min()))


def summary(ret: pd.Series, rf: pd.Series | None = None, exposure: pd.Series | None = None,
            turnover: pd.Series | None = None) -> dict:
    years = years_of(ret.index)
    n = periods_per_year(ret.index)  # 每年的交易日数（现代约 252，1952 年以前约 290）
    equity = (1 + ret).cumprod()
    cagr = equity.iloc[-1] ** (1 / years) - 1
    vol = ret.std() * np.sqrt(n)
    excess = ret - (rf.reindex(ret.index).fillna(0) if rf is not None else 0)
    downside = np.sqrt((excess.clip(upper=0) ** 2).mean() * n)  # 下行偏差：只惩罚低于无风险收益的部分
    dd = drawdown(ret)
    mdd = dd.min()
    out = {
        "年化收益": cagr,
        "年化波动": vol,
        "夏普": excess.mean() / ret.std() * np.sqrt(n) if vol > 0 else np.nan,
        "索提诺": excess.mean() * n / downside if downside > 0 else np.nan,
        "最大回撤": mdd,
        "最大回撤日": dd.idxmin().strftime("%Y-%m-%d"),
        "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "Ulcer": float(np.sqrt((dd ** 2).mean())),
        "最长水下(年)": max_underwater_years(ret),
        "最差21日": worst_window(ret, 21),
        "最差252日": worst_window(ret, TRADING_DAYS),
    }
    if exposure is not None:
        out["平均仓位"] = float(exposure.mean())
    if turnover is not None:
        out["年换手"] = float(turnover.sum() / years)
    return out


def crisis_returns(ret: pd.Series) -> dict:
    """每次危机期间的累计收益（危机不在数据范围内的记为 NaN）。"""
    out = {}
    for name, (start, end) in CRISES.items():
        window = ret.loc[start:end].iloc[1:]  # 从高点那天收盘之后开始算
        if len(window) and window.index[0] <= pd.Timestamp(start) + pd.Timedelta(days=10):
            out[name] = float((1 + window).prod() - 1)
        else:
            out[name] = np.nan
    return out


def bootstrap_mdd(ret: pd.Series, horizon_years: int = 10, n_paths: int = 2000,
                  block: int = 126, seed: int = 0) -> np.ndarray:
    """分块自助法（block bootstrap）：从历史日收益里随机抽取连续的若干段（每段 block 天），
    拼成 horizon_years 年的“可能的未来”，重复 n_paths 次，返回每条路径的最大回撤。

    用来回答：“未来 10 年里，最大回撤超过 20% 的概率有多大？”
    按段抽取是为了保留“大跌往往连续出现”的特点。"""
    rng = np.random.default_rng(seed)
    r = ret.to_numpy()
    length = int(round(horizon_years * periods_per_year(ret.index)))
    n_blocks = int(np.ceil(length / block))
    starts = rng.integers(0, len(r) - block + 1, size=(n_paths, n_blocks))
    paths = r[starts[..., None] + np.arange(block)].reshape(n_paths, -1)[:, :length]
    equity = np.cumprod(1 + paths, axis=1)
    peak = np.maximum(np.maximum.accumulate(equity, axis=1), 1.0)
    return (equity / peak - 1).min(axis=1)
