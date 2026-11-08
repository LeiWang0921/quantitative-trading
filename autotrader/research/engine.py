"""多资产组合回测引擎。

策略每天收盘后给出“目标权重”（每个资产占总资产的比例，剩下的是现金），引擎负责模拟：
  - 成交时点（execution）：
      "next_open"  收盘后算信号，第二天开盘成交（需要开盘价；和 run.py 的实际做法一致）
      "next_close" 收盘后算信号，第二天收盘成交（只有收盘价的数据用这个，更保守）
      "close"      当天收盘成交（假设收盘前几分钟算信号并下收盘单，最乐观，只用于敏感性检查）
  - 持仓漂移：两次调仓之间，各资产占比随涨跌自然变化
  - 再平衡阈值（band）：只有某个资产的实际占比偏离目标超过 band 才调仓，避免频繁小额交易
      drift_only=False：任何一个资产偏离超过 band，就把所有资产一起调回目标
      drift_only=True： 只调整偏离超过 band 的那几个资产（标的多、每份小时用这个，交易笔数少得多）
  - 交易成本（cost_bps）：每成交 1 美元付 cost_bps 个基点（0.01%）的手续费 + 买卖价差
  - 现金按国库券利率计息
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import Market


@dataclass
class Result:
    ret: pd.Series          # 每日组合收益（已扣成本）
    exposure: pd.Series     # 每日收盘时的风险资产总占比（0 = 全现金，1 = 满仓）
    turnover: pd.Series     # 每日成交额 / 总资产
    holdings: pd.DataFrame  # 每日收盘时各资产占比

    @property
    def equity(self) -> pd.Series:
        return (1 + self.ret).cumprod()


def run(target: pd.DataFrame | pd.Series, market: Market, execution: str = "next_open",
        cost_bps: float = 5.0, band: float = 0.05, drift_only: bool = False) -> Result:
    if isinstance(target, pd.Series):
        target = target.to_frame(market.assets[0])
    target = target.reindex(index=market.close_ret.index, columns=market.assets).fillna(0.0)
    if (target.sum(axis=1) > 1 + 1e-9).any() or (target < -1e-12).any().any():
        raise ValueError("目标权重必须 >= 0 且合计 <= 1（不加杠杆、不做空）")
    if execution == "next_open" and market.gap_ret is None:
        raise ValueError("这份数据没有开盘价，请用 execution='next_close'")

    T = target.to_numpy()
    R = market.close_ret.fillna(0.0).to_numpy()
    rf = market.rf.to_numpy()
    G = None if market.gap_ret is None else market.gap_ret.fillna(0.0).to_numpy()
    I = None if market.intra_ret is None else market.intra_ret.fillna(0.0).to_numpy()
    cost = cost_bps / 10_000

    n, k = R.shape
    v = np.zeros(k)       # 各资产的市值
    c = 1.0               # 现金
    ret = np.zeros(n)
    exposure = np.zeros(n)
    turnover = np.zeros(n)
    holdings = np.zeros((n, k))

    def rebalance(tgt: np.ndarray) -> float:
        """调到目标权重（偏离超过 band 才调），返回成交额占总资产的比例。"""
        nonlocal v, c
        eq = v.sum() + c
        if drift_only:
            move = (np.abs(tgt - v / eq) > band) | ((tgt == 0) & (v > 0))
            if not move.any():
                return 0.0
            desired = np.where(move, tgt * eq, v)
            buys = np.clip(desired - v, 0, None)
            sells = np.clip(v - desired, 0, None)
            # 没调整的资产可能略高于目标：现金不够时按比例少买（现金账户不能透支）
            room = c + sells.sum() * (1 - cost)
            if buys.sum() > 0 and buys.sum() * (1 + cost) > room:
                buys *= max(room, 0.0) / (buys.sum() * (1 + cost))
            desired = v - sells + buys
        else:
            if np.abs(tgt - v / eq).max() <= band and not (tgt.sum() == 0 and v.sum() > 0):
                return 0.0
            desired = tgt * eq
        traded = np.abs(desired - v).sum()
        c = c - (desired - v).sum() - traded * cost
        v = desired
        return traded / eq

    for i in range(n):
        eq_start = v.sum() + c
        if execution == "next_open":
            v = v * (1 + G[i])                                  # 隔夜：持有昨天的仓位
            turnover[i] = rebalance(T[i - 1]) if i > 0 else 0.0  # 开盘：按昨天收盘后的信号调仓
            v = v * (1 + I[i])                                  # 日内
            c = c * (1 + rf[i])
        else:
            v = v * (1 + R[i])
            c = c * (1 + rf[i])
            signal = T[i] if execution == "close" else (T[i - 1] if i > 0 else None)
            if signal is not None:
                turnover[i] = rebalance(signal)
        eq_end = v.sum() + c
        ret[i] = eq_end / eq_start - 1
        exposure[i] = v.sum() / eq_end
        holdings[i] = v / eq_end

    idx = market.close_ret.index
    return Result(pd.Series(ret, idx), pd.Series(exposure, idx), pd.Series(turnover, idx),
                  pd.DataFrame(holdings, idx, market.assets))
