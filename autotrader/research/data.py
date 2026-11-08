"""读取本地数据（fetch_data.py / fetch_macro.py 下载的），整理成 pandas 格式。

约定：收益率一律用小数（0.01 = 涨 1%），日期是 pandas 的 DatetimeIndex。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import HISTORY_DIR, MACRO_DIR

TRADING_DAYS = 252
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"


def download_fred(series_id: str) -> pd.DataFrame:
    """从 FRED 下载一个序列，存到 data/macro/<序列>.csv，并返回（列：date, value）。"""
    import io
    import urllib.request

    with urllib.request.urlopen(FRED_URL.format(series_id), timeout=60) as resp:
        content = resp.read()
    if content.lstrip()[:1] == b"<":  # 返回的是网页（比如“网站维护中”），不是 CSV
        raise ValueError("FRED 返回的是网页而不是数据，网站可能在维护")
    df = pd.read_csv(io.BytesIO(content))
    df.columns = ["date", "value"]  # FRED 的 CSV 两列：observation_date, <序列名>
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # 节假日的值是空的
    df = df.dropna()
    MACRO_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(MACRO_DIR / f"{series_id}.csv", index=False)
    return df


PRICE_COLS = ["open", "high", "low", "close"]
SPLIT_RATIOS = [1.5, 2, 3, 4, 5, 8, 10, 20, 2 / 3, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 8, 1 / 10, 1 / 20]  # 拆股（如 3 拆 2）和合股（如 8 合 1）
# 盈透漏掉复权的情况只在 2005 年及以前出现过；之后的复权可靠，大幅涨跌一律当作真实涨跌（例如英特尔 2024-08-02 暴跌 26%）
SPLIT_CHECK_UNTIL = pd.Timestamp("2005-12-31")
# 2005 年以前、看起来像拆股但其实是真实暴跌的日子（已核实，不做处理）
REAL_MOVES = {
    "AAPL": ["2000-09-29"],   # 苹果发布盈利预警，单日跌 52%（当年 6 月 21 日才是一拆二）
    "NVDA": ["2004-08-06"],   # 英伟达 2004 年没有拆股（拆股在 2000、2001、2006、2007 年）
    # 网飞 2004 年只在 2 月 12 日 1 拆 2；7 月 16 日（财报不及预期）和 10 月 15 日（因亚马逊进入 DVD 租赁而降价）
    # 是真实暴跌，跌幅恰好接近“3 拆 2”的比例
    "NFLX": ["2004-07-16", "2004-10-15"],
}


def _volume_ratio(df: pd.DataFrame, i: int) -> float:
    """事件后第 2~11 天和事件前 10 天成交量（股数）中位数之比。跳过事件当天和次日，避开暴跌时的成交量高峰。"""
    vol = df["volume"].to_numpy()
    before, after = vol[max(i - 10, 0):i], vol[i + 2:i + 12]
    if len(before) < 5 or len(after) < 5 or np.median(before) <= 0:
        return np.nan
    return float(np.median(after) / np.median(before))


def clean_prices(df: pd.DataFrame, symbol: str = "", verbose: bool = False) -> pd.DataFrame:
    """修正盈透“复权”数据里的两类错误（例如 IWM、EFA 在 2005-06-09 的拆股没有复权；个股在 2003 年以前更多）：
      1. 未复权的拆股（只检查 2005 年及以前）：单日收盘价变化接近拆股比例 k（允许当天另有 ±15% 的真实涨跌），
         并且满足下面任意一条确认，才按拆股处理，把之前的价格除以 k：
           a. 之后成交的股数也变成约 k 倍；
           b. 开盘价正好按拆股比例跳空（和前一天收盘价 ÷ k 相差 < 5%）。
              盈透 2005 年以前的成交量时而复权、时而没复权，单靠 a 会漏掉很多拆股（在标普 500 成分股上核对过）。
         仅凭价格和成交量仍可能把真实暴跌误当成拆股，已核实的例外列在 REAL_MOVES 里。
      2. 错误的开盘价：开盘价同时远离前一天收盘价和当天收盘价（>15%），用前一天收盘价代替
    """
    df = df.copy()
    ratio = df["close"] / df["close"].shift(1)
    gap = df["open"] / df["close"].shift(1)
    suspicious = ratio[((ratio < 0.75) | (ratio > 1.4)) & (ratio.index <= SPLIT_CHECK_UNTIL)]
    real_moves = {pd.Timestamp(d) for d in REAL_MOVES.get(symbol, [])}
    for date, r in suspicious.items():
        if date in real_moves:
            if verbose:
                print(f"  {symbol} {date.date()}: 收盘价单日变化 {r - 1:+.1%}，已核实是真实涨跌，保留")
            continue
        i = df.index.get_loc(date)
        vol_ratio = _volume_ratio(df, i)
        candidates = [k for k in SPLIT_RATIOS if abs(r * k - 1) < 0.15
                      and ((not np.isnan(vol_ratio) and 0.5 * k <= vol_ratio <= 1.8 * k) or abs(gap[date] * k - 1) < 0.05)]
        if not candidates:
            if verbose:
                print(f"  {symbol} {date.date()}: 收盘价单日变化 {r - 1:+.1%}，成交量倍数 {vol_ratio:.1f}，"
                      f"不像拆股，保留为真实涨跌")
            continue
        k = min(candidates, key=lambda c: abs(np.log(r * c)))
        before = df.index < date
        df.loc[before, PRICE_COLS] = df.loc[before, PRICE_COLS] / k
        df.loc[before, "volume"] = df.loc[before, "volume"] * k
        if verbose:
            print(f"  {symbol} {date.date()}: 收盘价单日变化 {r - 1:+.1%}，成交量变成 {vol_ratio:.1f} 倍，"
                  f"按 1 拆 {k:g} 复权（复权后当天实际 {r * k - 1:+.1%}）")
    prev_close = df["close"].shift(1)
    bad_open = ((df["open"] / prev_close - 1).abs() > 0.15) & ((df["open"] / df["close"] - 1).abs() > 0.15)
    for date in df.index[bad_open]:
        if verbose:
            print(f"  {symbol} {date.date()}: 开盘价 {df.at[date, 'open']:.2f} 异常"
                  f"（前收 {prev_close[date]:.2f}，当日收 {df.at[date, 'close']:.2f}），用前收代替")
        df.at[date, "open"] = prev_close[date]
    return df


def load_etf(symbol: str, verbose: bool = False) -> pd.DataFrame:
    """盈透下载的复权日线（已清洗）：open, high, low, close, volume, hist_vol, implied_vol。"""
    df = pd.read_csv(HISTORY_DIR / f"{symbol}.csv", parse_dates=["date"], index_col="date")
    df = df[df["close"] > 0].sort_index()
    return clean_prices(df, symbol, verbose)


def load_fred(series_id: str) -> pd.Series:
    """FRED 序列（原始单位，利率类是 %）。"""
    df = pd.read_csv(MACRO_DIR / f"{series_id}.csv", parse_dates=["date"], index_col="date")
    return df["value"].rename(series_id)


def load_french() -> pd.DataFrame:
    """弗伦奇美国全市场日收益：mkt = 全市场总收益（含分红），rf = 1 个月国库券日收益。"""
    return pd.read_csv(MACRO_DIR / "french_daily.csv", parse_dates=["date"], index_col="date")


def tbill_daily(index: pd.DatetimeIndex) -> pd.Series:
    """对齐到给定交易日的现金日收益：3 个月国库券年化利率换算成每个交易日的收益。"""
    d = load_fred("DTB3") / 100
    # FRED 的国库券利率是“贴现率”，先换算成债券等价收益率：365d / (360 - 91d)
    rate = 365 * d / (360 - 91 * d)
    daily = (1 + rate) ** (1 / TRADING_DAYS) - 1
    return daily.reindex(index, method="ffill").fillna(0.0)


def synthetic_bond_returns(series_id: str = "DGS10", maturity: float = 10.0) -> pd.Series:
    """用国债收益率合成“恒定期限国债”的日总回报（2004 年以前没有国债 ETF 时使用）。

    做法：每天按当天收益率买入一张票面利率 = 收益率、期限 maturity 年的平价债券，
    第二天按新的收益率重新定价，回报 = 价格变化 + 一天的利息。半年付息。
    """
    y = load_fred(series_id) / 100
    dt = y.index.to_series().diff().dt.days / 365.25
    y_prev = y.shift(1)
    n = 2 * maturity                            # 付息次数（半年一次）
    disc = (1 + y / 2) ** (-n)
    price = y_prev / y * (1 - disc) + disc      # 票息 = 昨天收益率的债券，用今天收益率定价（不含应计利息）
    ret = (1 + y / 2) ** (2 * dt) * price - 1   # 持有 dt 年：按今天收益率复利计入这段时间的利息
    return ret.dropna().rename(f"bond{int(maturity)}y")


@dataclass
class Market:
    """回测用的、日期对齐的多资产日数据。

    close_ret：收盘到收盘的收益（每列一个资产）
    gap_ret：  隔夜收益 = 今天开盘 / 昨天收盘 - 1（没有开盘价的数据为 None）
    intra_ret：日内收益 = 今天收盘 / 今天开盘 - 1
    rf：       现金（国库券）日收益
    close：    收盘价（或总回报指数），给策略计算均线等信号用
    """
    close_ret: pd.DataFrame
    rf: pd.Series
    close: pd.DataFrame
    gap_ret: pd.DataFrame | None = None
    intra_ret: pd.DataFrame | None = None

    @property
    def assets(self) -> list[str]:
        return list(self.close_ret.columns)

    def slice(self, start=None, end=None) -> "Market":
        s = slice(start, end)
        return Market(self.close_ret.loc[s], self.rf.loc[s], self.close.loc[s],
                      None if self.gap_ret is None else self.gap_ret.loc[s],
                      None if self.intra_ret is None else self.intra_ret.loc[s])


def etf_market(symbols: list[str], start: str | None = None, how: str = "inner") -> Market:
    """ETF / 股票组合。
    how="inner"：只保留所有标的都有数据的日期（起点受上市最晚的标的限制）。
    how="outer"：保留至少一半标的有数据的日期；上市较晚的标的在上市前是 NaN（策略里视为“还不能买”），
                 上市后没有成交的日子（例如停牌）沿用前一天收盘价。"""
    frames = {s: load_etf(s) for s in symbols}
    if how == "inner":
        index = None
        for df in frames.values():
            index = df.index if index is None else index.intersection(df.index)
    else:
        count = pd.concat([df["close"].notna().rename(s) for s, df in frames.items()], axis=1, sort=True).sum(axis=1)
        index = count.index[count >= len(symbols) / 2]
    if start:
        index = index[index >= pd.Timestamp(start)]
    close = pd.DataFrame({s: frames[s]["close"].reindex(index) for s in symbols})
    opn = pd.DataFrame({s: frames[s]["open"].reindex(index) for s in symbols})
    if how != "inner":
        for s in symbols:
            listed = (index >= frames[s].index[0]) & (index <= frames[s].index[-1])
            close[s] = close[s].ffill().where(listed)
    close_ret = close.pct_change()
    gap = opn / close.shift(1) - 1
    intra = close / opn - 1
    keep = close_ret.index[1:]
    return Market(close_ret.loc[keep], tbill_daily(keep), close.loc[keep], gap.loc[keep], intra.loc[keep])


def french_market(with_bond: bool = False, start: str | None = None) -> Market:
    """弗伦奇美国全市场（1926 年起）；with_bond=True 时加入合成的 10 年期国债（1962 年起）。"""
    ff = load_french()
    rets = {"US_STOCK": ff["mkt"]}
    if with_bond:
        bond = synthetic_bond_returns()
        # 股市和债市的假日不完全相同：先把债券回报累乘成指数，按股票交易日取值，再算回日收益，
        # 这样债市开门、股市休市那天的回报会并入下一个股票交易日，不会丢失
        ff = ff[ff.index > bond.index[0]]
        level = (1 + bond).cumprod()
        level = level.reindex(level.index.union(ff.index)).ffill().reindex(ff.index)
        rets["US_BOND10"] = level.pct_change().fillna(0.0)
    close_ret = pd.DataFrame(rets).loc[ff.index]
    if start:
        close_ret = close_ret[close_ret.index >= pd.Timestamp(start)]
    close = (1 + close_ret).cumprod()  # 总回报指数，代替价格算均线
    return Market(close_ret, ff["rf"].loc[close_ret.index], close)


def annualized_vol(ret: pd.Series | pd.DataFrame, window: int) -> pd.Series | pd.DataFrame:
    return ret.rolling(window).std() * np.sqrt(TRADING_DAYS)
