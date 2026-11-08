"""经济周期指标：判断“经济是否在转差”，用来区分“牛市中的回调”和“伴随衰退的熊市”。

思路（来自 Growth-Trend Timing，Philosophical Economics 2016）：深而长的熊市几乎都伴随经济衰退；
没有衰退的下跌通常很快恢复。所以趋势转弱时，只有经济也在转差才减仓。

每个指标返回“经济好”的布尔序列（True = 经济没有在转差），按实际公布时间对齐，只用当时能看到的数据：
  unemployment  失业率 ≤ 过去 12 个月平均（失业没有在上升）         月度，下个月约 10 日后可用
  claims        首次申请失业金 4 周平均 ≤ 过去 52 周平均             每周，周六截止，约 6 天后可用
  sahm          萨姆衰退指标（实时版）< 0.5（没有发出衰退信号）       月度，下个月约 10 日后可用
  yield_curve   10 年期国债利率 > 3 个月国库券利率（收益率曲线没有倒挂）  每日，1 天后可用
"""
from __future__ import annotations

import pandas as pd

from .data import load_fred

# 每个指标要从 FRED 下载哪些序列，以及最新数据最多可以“旧”多少天（超过就认为数据有问题，不使用过滤）
SOURCES = {"unemployment": ["UNRATE"], "claims": ["ICSA"], "sahm": ["SAHMREALTIME"], "yield_curve": ["DGS10", "DTB3"]}
MAX_AGE_DAYS = {"unemployment": 50, "claims": 16, "sahm": 50, "yield_curve": 7}

NAMES = {
    "unemployment": "失业率趋势",
    "claims": "首次申请失业金趋势",
    "sahm": "萨姆衰退指标",
    "yield_curve": "收益率曲线",
}


def _available(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """把“公布日期”索引的序列对齐到交易日：每天取当时最近一次公布的值。"""
    series = series.groupby(level=0).last()
    return series.reindex(series.index.union(index)).ffill().reindex(index)


def _monthly_release(series: pd.Series, day: int = 10) -> pd.Series:
    """FRED 月度数据的日期是“所属月份的 1 号”，实际在下个月 day 号前后公布。"""
    out = series.copy()
    out.index = out.index + pd.offsets.MonthBegin(1) + pd.Timedelta(days=day - 1)
    return out


def claims_status() -> dict:
    """首次申请失业金的最新状态（给 run.py 写日志用）。"""
    c = load_fred("ICSA")
    return {"week": c.index[-1], "avg4": float(c.tail(4).mean()), "avg52": float(c.tail(52).mean())}


def economy_good(name: str, index: pd.DatetimeIndex) -> pd.Series:
    """True = 经济没有在转差。数据还没开始的日期为 NaN。"""
    if name == "unemployment":
        u = load_fred("UNRATE")
        good = (u <= u.rolling(12).mean()).astype(float).where(u.rolling(12).mean().notna())
        return _available(_monthly_release(good), index).rename(name)
    if name == "claims":
        c = load_fred("ICSA")
        good = (c.rolling(4).mean() <= c.rolling(52).mean()).astype(float).where(c.rolling(52).mean().notna())
        good.index = good.index + pd.Timedelta(days=6)  # 截至周六的数据，下周四公布，周五起用
        return _available(good, index).rename(name)
    if name == "sahm":
        s = load_fred("SAHMREALTIME")
        return _available(_monthly_release((s < 0.5).astype(float)), index).rename(name)
    if name == "yield_curve":
        # 1982 年以前没有 T10Y3M，用 10 年期收益率减 3 个月国库券利率代替
        spread = (load_fred("DGS10") - load_fred("DTB3")).dropna()
        good = (spread > 0).astype(float)
        good.index = good.index + pd.offsets.BDay(1)
        return _available(good, index).rename(name)
    raise ValueError(name)
