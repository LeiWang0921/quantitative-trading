"""训练出来的模型。全部采用“滚动训练”（walk-forward）：
每年年初只用之前的数据重新训练，然后对这一年逐日预测；模型在任何时候都没见过未来的数据。

1. 波动率预测：预测未来 21 个交易日（约一个月）的年化波动率
     rv20     最近 20 日实际波动（不需要训练，作为基准）
     ewma     指数加权波动（RiskMetrics，λ=0.94，不需要训练）
     garch    GARCH(1,1)：今天的方差 = 常数 + a × 昨天的冲击² + b × 昨天的方差
     har      HAR 回归：用过去 1 周、1 个月、3 个月的波动（取对数）线性回归未来一个月的波动
     har_vix  HAR 再加上 VIX（期权市场对未来 30 天波动的预期，1990 年起才有）

2. 危机概率分类器：预测“未来 21 个交易日累计下跌超过 5%”的概率
     特征：趋势（离 200 日均线多远）、动量（1/3/12 个月涨跌）、波动、离一年高点的回撤，
          以及宏观数据（期限利差、信用利差、VIX）
     模型：逻辑回归（线性、稳健）、梯度提升树（非线性）、神经网络 MLP（非线性，最灵活也最容易过拟合）

3. 隐马尔可夫状态模型（Markov switching / HMM）：假设市场在“平稳”和“动荡”两种状态之间切换，
   两种状态的平均收益和波动不同；模型从数据中学出每种状态的特征和切换概率，
   每天给出“当前处于动荡状态”的概率。只用“滤波”概率（只用到当天为止的数据），
   不用“平滑”概率（会用到未来数据）。
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .data import TRADING_DAYS

HORIZON = 21  # 预测未来多少个交易日


# ---------------------------------------------------------------- 波动率预测

def future_vol(ret: pd.Series, horizon: int = HORIZON) -> pd.Series:
    """预测目标：第 t+1 到 t+horizon 天的实际年化波动（第 t 天收盘时还不知道）。"""
    sq = (ret ** 2)[::-1].rolling(horizon).mean()[::-1].shift(-1)
    return np.sqrt(sq * TRADING_DAYS)


def _har_features(ret: pd.Series, vix: pd.Series | None = None) -> pd.DataFrame:
    sq = ret ** 2
    feats = {f"log_rv{n}": 0.5 * np.log(sq.rolling(n).mean() * TRADING_DAYS + 1e-10) for n in (5, 21, 63)}
    if vix is not None:
        feats["log_vix"] = np.log(vix.reindex(ret.index).ffill() / 100)
    return pd.DataFrame(feats)


def _yearly_refit(index: pd.DatetimeIndex, first_year: int):
    """依次给出 (年份, 这一年的日期掩码)，用于每年重新训练。"""
    for year in range(first_year, index[-1].year + 1):
        mask = index.year == year
        if mask.any():
            yield year, mask


def forecast_vol(ret: pd.Series, method: str, first_year: int, vix: pd.Series | None = None) -> pd.Series:
    """第 t 天收盘时对未来 21 日年化波动的预测。需要训练的方法从 first_year 开始才有预测。"""
    if method == "rv20":
        return ret.rolling(20).std() * np.sqrt(TRADING_DAYS)
    if method == "ewma":
        return np.sqrt((ret ** 2).ewm(alpha=0.06, adjust=False).mean() * TRADING_DAYS)

    out = pd.Series(np.nan, index=ret.index)
    if method in ("har", "har_vix"):
        X = _har_features(ret, vix if method == "har_vix" else None)
        y = np.log(future_vol(ret))
        for year, mask in _yearly_refit(ret.index, first_year):
            # 训练数据：今年之前、而且目标（未来 21 天）也在今年之前结束的样本
            cutoff = ret.index[mask][0]
            train = X.index < cutoff - pd.Timedelta(days=40)
            rows = train & X.notna().all(axis=1) & y.notna()
            A = np.column_stack([np.ones(rows.sum()), X[rows].to_numpy()])
            beta, *_ = np.linalg.lstsq(A, y[rows].to_numpy(), rcond=None)
            Xt = X[mask]
            pred = np.column_stack([np.ones(len(Xt)), Xt.to_numpy()]) @ beta
            out[mask] = np.exp(pred)
        return out

    if method == "garch":
        from arch import arch_model
        r = ret * 100  # arch 包习惯用百分比收益
        for year, mask in _yearly_refit(ret.index, first_year):
            cutoff = ret.index[mask][0]
            train = r[r.index < cutoff].dropna()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = arch_model(train, mean="Constant", vol="GARCH", p=1, q=1).fit(disp="off")
            # 参数固定，用到今年年底为止的数据逐日滤波，得到每天收盘时对未来 21 天的方差预测
            upto = r[r.index <= ret.index[mask][-1]].dropna()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = arch_model(upto, mean="Constant", vol="GARCH", p=1, q=1).fix(fit.params)
                fc = res.forecast(horizon=HORIZON, start=cutoff, reindex=False)
            var = fc.variance.mean(axis=1)  # 未来 21 天的平均日方差（%²）
            out.loc[var.index] = np.sqrt(var.to_numpy() * TRADING_DAYS) / 100
        return out
    raise ValueError(method)


def vol_forecast_scores(forecast: pd.Series, actual: pd.Series, baseline: pd.Series) -> dict:
    """样本外评价：对数波动的 R²（相对基准方法的改进）和 QLIKE 损失（越小越好，专门用于评价方差预测）。"""
    df = pd.DataFrame({"f": forecast, "a": actual, "b": baseline}).dropna()
    lf, la, lb = np.log(df.f), np.log(df.a), np.log(df.b)
    qlike = lambda f: float(np.mean(df.a ** 2 / f ** 2 - np.log(df.a ** 2 / f ** 2) - 1))
    return {
        "样本数": len(df),
        "R2_对数波动": float(1 - ((la - lf) ** 2).sum() / ((la - la.mean()) ** 2).sum()),
        "相对基准误差下降": float(1 - ((la - lf) ** 2).sum() / ((la - lb) ** 2).sum()),
        "QLIKE": qlike(df.f),
        "基准QLIKE": qlike(df.b),
    }


# ---------------------------------------------------------------- 危机概率分类器

def crash_label(close: pd.Series, horizon: int = HORIZON, threshold: float = -0.05) -> pd.Series:
    """标签：未来 horizon 天的累计收益是否低于 threshold（1 = 会大跌）。最后 horizon 天未知，为 NaN。"""
    fwd = close.shift(-horizon) / close - 1
    return (fwd < threshold).astype(float).where(fwd.notna())


def crash_features(close: pd.Series, macro: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """第 t 天收盘时可以算出的特征。"""
    ret = close.pct_change()
    f = pd.DataFrame(index=close.index)
    f["dist_sma200"] = close / close.rolling(200).mean() - 1
    f["dist_sma50"] = close / close.rolling(50).mean() - 1
    for months, days in ((1, 21), (3, 63), (12, 252)):
        f[f"mom_{months}m"] = close.pct_change(days)
    f["vol_20"] = ret.rolling(20).std() * np.sqrt(TRADING_DAYS)
    f["vol_ratio"] = f["vol_20"] / (ret.rolling(252).std() * np.sqrt(TRADING_DAYS))
    f["dd_252"] = close / close.rolling(252).max() - 1
    for name, series in (macro or {}).items():
        # FRED 数据在观测日的下一个工作日才发布，所以晚一天才能用
        s = series.shift(1).reindex(close.index.union(series.index)).ffill().reindex(close.index)
        f[name] = s
        f[f"{name}_chg63"] = s - s.shift(63)
    return f


def walk_forward_classifier(features: pd.DataFrame, label: pd.Series, model: str,
                            first_year: int, horizon: int = HORIZON) -> pd.Series:
    """每年重新训练一次，返回每天预测的“未来大跌概率”。
    训练集和预测年之间留出 horizon 天的空隙，因为最后 horizon 天样本的标签要用到预测年的数据。"""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    data = features.join(label.rename("y"))
    valid = data.drop(columns="y").notna().all(axis=1)
    out = pd.Series(np.nan, index=features.index)
    for year, mask in _yearly_refit(features.index, first_year):
        start = features.index[mask][0]
        train_end = features.index[features.index < start][-horizon - 1]
        train = data[valid & data["y"].notna() & (data.index <= train_end)]
        if train["y"].nunique() < 2:
            continue
        if model == "logit":
            clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000))
        elif model == "mlp":  # 两层神经网络，权重衰减 + 提前停止，减少过拟合
            from sklearn.neural_network import MLPClassifier
            clf = make_pipeline(StandardScaler(), MLPClassifier(
                hidden_layer_sizes=(16, 8), alpha=1e-2, early_stopping=True, max_iter=500, random_state=0))
        else:  # 浅层梯度提升树，强正则化，减少过拟合
            clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                                 min_samples_leaf=200, l2_regularization=1.0,
                                                 random_state=0)
        clf.fit(train.drop(columns="y").to_numpy(), train["y"].to_numpy())
        rows = mask & valid.to_numpy()
        if rows.any():
            out[rows] = clf.predict_proba(features[rows].to_numpy())[:, 1]
    return out


# ---------------------------------------------------------------- 隐马尔可夫状态模型

def markov_turbulence(ret: pd.Series, first_year: int, refit_every: int = 5,
                      sample: str = "W-FRI") -> pd.Series:
    """两状态马尔可夫切换模型：每 refit_every 年用之前的数据重新估计一次参数，
    然后用固定参数对之后的数据做滤波，返回每天“处于高波动状态”的概率。

    为了速度和稳健，用周收益（每周五）来估计和滤波，日度数据向前填充（周内沿用上周五的概率）。"""
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

    weekly = np.log1p(ret).resample(sample).sum()
    weekly = weekly[weekly != 0] * 100
    out = pd.Series(np.nan, index=weekly.index)
    years = list(range(first_year, weekly.index[-1].year + 1, refit_every))
    for i, year in enumerate(years):
        start = pd.Timestamp(f"{year}-01-01")
        end = pd.Timestamp(f"{years[i + 1]}-01-01") if i + 1 < len(years) else weekly.index[-1] + pd.Timedelta(days=1)
        train = weekly[weekly.index < start]
        np.random.seed(0)  # 参数估计用了随机初始值，固定种子保证每次结果一样
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = MarkovRegression(train.to_numpy(), k_regimes=2, trend="c", switching_variance=True).fit(
                disp=False, search_reps=5)
            # 方差大的那个状态 = 动荡状态
            names = fit.model.param_names
            variances = [fit.params[names.index(f"sigma2[{k}]")] for k in range(2)]
            turbulent = int(np.argmax(variances))
            upto = weekly[weekly.index < end]
            filt = MarkovRegression(upto.to_numpy(), k_regimes=2, trend="c", switching_variance=True).filter(
                fit.params)
        probs = pd.Series(filt.filtered_marginal_probabilities[:, turbulent], index=upto.index)
        span = (probs.index >= start) & (probs.index < end)
        out[probs.index[span]] = probs[span].to_numpy()
    # 周五收盘时得到的概率，从那天起生效，周内沿用
    return out.reindex(ret.index.union(out.index)).ffill().reindex(ret.index)
