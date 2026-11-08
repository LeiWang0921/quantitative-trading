"""收益率预测模型。目前只有 ARMA：用过去的日收益率预测明天的日收益率。

为了不“偷看未来”，严格按时间切分：
  训练期（前 70%）：选择模型阶数、估计参数
  测试期（后 30%）：参数固定不变，每天只用当天及以前的数据预测明天
所有效果指标都只在测试期上计算。
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.arima.model import ARIMA

from ..config import HISTORY_DIR


def load_prices(symbol: str) -> pd.DataFrame:
    """读取 fetch_data.py 下载的 CSV，日期作为索引。"""
    return pd.read_csv(HISTORY_DIR / f"{symbol}.csv", parse_dates=["date"], index_col="date")


def daily_returns(close: pd.Series) -> pd.Series:
    """日对数收益率，单位 %。log(今天/昨天) ≈ 今天涨跌幅，好处是多天可以直接相加。"""
    return (np.log(close).diff() * 100).dropna()


def fit_arma(train: pd.Series, max_p: int = 2, max_q: int = 2):
    """在训练数据上试遍 ARMA(p, q)，按 AIC（兼顾拟合好坏和参数多少的分数，越小越好）选最好的。"""
    best = None
    for p in range(max_p + 1):
        for q in range(max_q + 1):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # 收敛警告对选阶影响不大，不打印
                res = ARIMA(train.values, order=(p, 0, q), trend="c").fit()
            if best is None or res.aic < best.aic:
                best = res
    return best


def arma_forecast(returns: pd.Series, train_frac: float = 0.7) -> tuple[pd.DataFrame, dict]:
    """返回 (测试期逐日表, 模型信息)。

    逐日表的列：actual = 当天实际收益率，predicted = 前一天收盘后对当天的预测（均为 %）。
    """
    split = int(len(returns) * train_frac)
    train = returns.iloc[:split]
    model = fit_arma(train)

    # 用训练期的参数在整段数据上逐日做“一步预测”：第 t 天的预测只用到第 t-1 天及以前
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        full = model.apply(returns.values)
    predicted = pd.Series(full.fittedvalues, index=returns.index)

    table = pd.DataFrame({"actual": returns, "predicted": predicted}).iloc[split:]
    p, _, q = model.model.order
    info = {
        "order": f"ARMA({p},{q})",
        "train": f"{train.index[0]:%Y-%m-%d} ~ {train.index[-1]:%Y-%m-%d}",
        "test": f"{table.index[0]:%Y-%m-%d} ~ {table.index[-1]:%Y-%m-%d}",
        # Ljung-Box 检验：训练期收益率和自己的过去 10 天有没有相关性。
        # p 值 < 0.05 说明有一点可预测的结构；> 0.05 说明和随机噪声分不开
        "ljung_box_p": float(acorr_ljungbox(train, lags=[10])["lb_pvalue"].iloc[0]),
        "train_mean": float(train.mean()),
    }
    return table, info


def evaluate(table: pd.DataFrame, train_mean: float) -> dict:
    """测试期的预测效果，每个指标都和一个“不用模型”的基准对比。"""
    actual, pred = table["actual"], table["predicted"]
    up = actual > 0
    # 样本外 R²：比“每天都预测训练期平均收益”好多少。> 0 才说明模型有用，通常在 ±1% 以内
    r2 = 1 - ((actual - pred) ** 2).sum() / ((actual - train_mean) ** 2).sum()

    # 简单交易规则（不计手续费）：预测明天涨就持有一天，否则空仓
    long = pred > 0
    return {
        "方向准确率": float(((pred > 0) == up).mean()),
        "基准：总猜涨": float(up.mean()),
        "样本外R²": float(r2),
        "预测与实际相关": float(actual.corr(pred)),
        "预测看涨天数": float(long.mean()),
        "规则累计收益": float(np.exp(actual[long].sum() / 100) - 1),
        "一直持有收益": float(np.exp(actual.sum() / 100) - 1),
    }
