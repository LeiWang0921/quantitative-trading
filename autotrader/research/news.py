"""新闻 / 调查类指标：读取，按“当时能看到的时间”对齐（避免偷看未来），再标准化成可比较的分数。

四个指标（fetch_macro.py 下载）：
  news_sentiment  旧金山联储每日新闻情绪：报纸经济类文章的正面程度，越高越乐观
  USEPUINDXD      经济政策不确定性（每日）：报纸里谈“政策不确定”的文章比例，越高越悲观
  UMCSENT         密歇根消费者信心（每月调查），越高越乐观
  EMVOVERALLEMV   股市波动新闻（每月）：报纸里谈论股市动荡的文章比例，越高越悲观

统一成“好消息分数”：分数 > 0 表示新闻比过去 3 年的平均水平更乐观，< 0 表示更悲观。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import MACRO_DIR
from .data import load_fred

# sign：+1 表示数值越高越乐观；-1 表示越高越悲观（取负号后统一成“越高越好”）
# lag：日度数据要等几个工作日才能用（发布延迟，保守取值）；月度数据在下个月第 7 天才算可用
INDICATORS = {
    "news_sentiment": {"名称": "新闻情绪（旧金山联储）", "sign": +1, "freq": "D", "lag": 3},
    "USEPUINDXD": {"名称": "经济政策不确定性", "sign": -1, "freq": "D", "lag": 1},
    "UMCSENT": {"名称": "消费者信心（密歇根）", "sign": +1, "freq": "M", "lag": 7},
    "EMVOVERALLEMV": {"名称": "股市波动新闻", "sign": -1, "freq": "M", "lag": 7},
}


def load_raw(name: str) -> pd.Series:
    if name == "news_sentiment":
        df = pd.read_csv(MACRO_DIR / "news_sentiment.csv", parse_dates=["date"], index_col="date")
        return df["value"].rename(name)
    return load_fred(name)


def point_in_time(name: str, index: pd.DatetimeIndex) -> pd.Series:
    """对齐到交易日：每个交易日只用当天已经发布的数据。
    日度数据：先取最近 21 天的平均（每天的数值噪声很大），再往后推 lag 个工作日。
    月度数据：某月的数值在下个月第 lag 天才可用（例如 9 月的消费者信心，10 月 7 日起才用）。"""
    spec = INDICATORS[name]
    raw = load_raw(name).dropna()
    if spec["freq"] == "D":
        smooth = raw.rolling(21, min_periods=15).mean()
        smooth.index = smooth.index + pd.offsets.BDay(spec["lag"])
    else:
        smooth = raw.copy()
        smooth.index = smooth.index + pd.offsets.MonthBegin(1) + pd.Timedelta(days=spec["lag"] - 1)
    smooth = smooth.groupby(level=0).last()  # 周末的数据推到同一个工作日时，保留最新的那个
    merged = smooth.reindex(smooth.index.union(index)).ffill()
    return merged.reindex(index)


def score(name: str, index: pd.DatetimeIndex, window: int = 756) -> pd.Series:
    """好消息分数 = 符号 × (当前值 - 过去 3 年平均) / 过去 3 年标准差。只用过去的数据。"""
    x = point_in_time(name, index)
    z = (x - x.rolling(window, min_periods=window // 2).mean()) / x.rolling(window, min_periods=window // 2).std()
    return (INDICATORS[name]["sign"] * z).rename(name)


def trend_breaks(trend: pd.Series, min_full_days: int = 21) -> pd.DatetimeIndex:
    """“趋势开始转弱”的日子：多周期趋势信号从满分（1）掉下来，而且之前至少连续 min_full_days 天满分。"""
    full = (trend == 1).astype(int)
    run = full.groupby((full != full.shift()).cumsum()).cumsum()  # 连续满分的天数
    prev_run = run.shift(1)
    return trend.index[(trend < 1) & (trend.shift(1) == 1) & (prev_run >= min_full_days)]


def forward_excess(close: pd.Series, rf: pd.Series, days: int) -> pd.Series:
    """从某天收盘起，未来 days 个交易日相对现金的超额收益。"""
    fwd = close.shift(-days) / close - 1
    cash = np.expm1(np.log1p(rf)[::-1].rolling(days).sum()[::-1].shift(-1))
    return fwd - cash
