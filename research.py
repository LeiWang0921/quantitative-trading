"""防守型策略研究：把整个研究过程从头跑一遍，结果写到 results/research/（CSV），供 notebooks 画图。

用法：
  python research.py                 全部重跑（约 5~10 分钟）
  python research.py long etf        只跑指定部分

需要先下载数据：python fetch_data.py --research 和 python fetch_macro.py

各部分：
  long      美国全市场 1927-2026（弗伦奇数据）：只在股票和现金之间切换的各种做法
  bond      美国股票 + 合成 10 年期国债，1963-2026
  etf       可以实际交易的 ETF：SPY + IEF，2005-2026，次日开盘成交
  markets   其他市场：QQQ / IWM / EFA / EEM，各自 + IEF
  stocks    你关注的 5 只股票：单只 + IEF，以及 5 只等权组合 + IEF
  models    训练出来的模型：波动率预测、危机概率分类器、隐马尔可夫状态模型
  robust    稳健性：参数敏感性、交易成本、成交时点
  frontier  有效前沿：同样的最大回撤下，哪类做法收益最高
  news      新闻 / 调查类指标能否区分“牛市回调”和“熊市开头”，以及用来调整激进程度的效果
  economy   经济周期指标（失业率、申请失业金、萨姆指标、收益率曲线）做同样的检验
  universe  选股参考：约 70 家公司的客观数据，生成 results/research/选股参考.md（先运行 fetch_data.py --universe）
            （docs/候选股票池.md 是当前候选股的持仓图，由 candidate_charts.py 生成）

研究最终选出的方案（autotrader/research/strategies.py 里的 defensive_core）：
  股票仓位 = 多周期趋势信号（100/150/200/250/300 日均线，各 1% 缓冲，取平均）× min(1, 10% / 最近 20 日波动)
  剩余资金 → 中期国债 IEF（只在 IEF 高于自己的 200 日均线时），否则留作现金
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from autotrader.config import RESULTS_DIR, load_config
from autotrader.research import data, engine, metrics, models
from autotrader.research import strategies as S

OUT = RESULTS_DIR / "research"
FINAL_TARGET = 0.10                     # 终选方案的波动率目标
MY_STOCKS = load_config()["research"]["my_stocks"]
SUMMARY_COLS = ["年化收益", "年化波动", "夏普", "索提诺", "最大回撤", "最大回撤日", "Calmar", "Ulcer",
                "最长水下(年)", "最差21日", "最差252日", "平均仓位", "年换手"]


def save(df: pd.DataFrame, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{name}.csv", encoding="utf-8-sig")  # utf-8-sig：Excel 打开中文不乱码


def evaluate(cands: dict[str, pd.DataFrame], m: data.Market, start: str, execution: str, cost: float,
             end: str | None = None, bootstrap: bool = True):
    """回测一组策略，返回 (指标表, 危机表, 每日净值表, 回测结果)。"""
    rows, crises, equity, results = {}, {}, {}, {}
    for name, target in cands.items():
        r = engine.run(target.fillna(0.0), m, execution=execution, cost_bps=cost, band=0.05)
        ret = r.ret.loc[start:end]
        s = metrics.summary(ret, m.rf, r.exposure.loc[start:end], r.turnover.loc[start:end])
        if bootstrap:
            mdd = metrics.bootstrap_mdd(ret, horizon_years=10)
            s["10年回撤>20%概率"] = float((mdd < -0.20).mean())
            s["10年回撤中位数"] = float(np.median(mdd))
        rows[name] = s
        crises[name] = metrics.crisis_returns(r.ret)
        equity[name] = (1 + ret).cumprod()
        results[name] = r
    summary = pd.DataFrame(rows).T
    cols = SUMMARY_COLS + [c for c in summary.columns if c not in SUMMARY_COLS]
    return summary[cols], pd.DataFrame(crises).T.dropna(axis=1, how="all"), pd.DataFrame(equity), results


def report(title: str, summary: pd.DataFrame, crises: pd.DataFrame | None = None) -> None:
    print(f"\n===== {title}")
    cols = ["年化收益", "年化波动", "夏普", "最大回撤", "Calmar", "最长水下(年)", "平均仓位", "年换手"]
    cols += [c for c in ("10年回撤>20%概率",) if c in summary.columns]
    print(summary[cols].to_string(float_format=lambda x: f"{x:.3f}"))
    if crises is not None:
        print(crises.to_string(float_format=lambda x: f"{x:.2f}"))


# ---------------------------------------------------------------- 各部分

def part_long() -> None:
    """美国全市场 1927-2026：没有国债数据，空仓时只能持有现金。"""
    m = data.french_market()
    A = "US_STOCK"
    cands = {
        "一直持有": S.buy_hold(m, A),
        "静态40%股票": S.static(m, {A: 0.4}),
        "200日均线": S.sma_trend(m, A, 200),
        "波动率目标10%": S.vol_target(m, A, 0.10),
        "200日均线×波动率10%": S.trend_vol(m, A, 200, 0.10),
        "终选方案(无国债)": S.defensive_core(m, A, None, FINAL_TARGET),
        "回撤控制TIPP20%": S.tipp(m, A, 0.20, 4.0),
    }
    summary, crises, equity, results = evaluate(cands, m, "1927-07-01", "next_close", 10)
    report("美国全市场 1927-2026（次日收盘成交，成本 10bp）", summary, crises)
    save(summary, "long_summary"), save(crises, "long_crises"), save(equity, "long_equity")
    sub = {}
    for name, r in results.items():
        for a, b in (("1927", "1949"), ("1950", "1989"), ("1990", "2026")):
            s = metrics.summary(r.ret.loc[a:b], m.rf)
            sub[(name, f"{a}-{b}")] = {k: s[k] for k in ("年化收益", "最大回撤", "夏普")}
    save(pd.DataFrame(sub).T, "long_subperiods")


def part_bond() -> None:
    m = data.french_market(with_bond=True)
    A, B = "US_STOCK", "US_BOND10"
    cands = {
        "一直持有股票": S.buy_hold(m, A),
        "60/40股债": S.static(m, {A: 0.6, B: 0.4}),
        "40/60股债": S.static(m, {A: 0.4, B: 0.6}),
        "200日均线×波动率10%(现金)": S.trend_vol(m, A, 200, 0.10, band=0.02),
        "终选方案": S.defensive_core(m, A, B, FINAL_TARGET),
        "终选方案(波动率12%)": S.defensive_core(m, A, B, 0.12),
    }
    summary, crises, equity, results = evaluate(cands, m, "1963-07-01", "next_close", 10)
    report("美国股票 + 合成 10 年期国债 1963-2026", summary, crises)
    save(summary, "bond_summary"), save(crises, "bond_crises"), save(equity, "bond_equity")


def part_etf() -> None:
    m = data.etf_market(["SPY", "IEF"], start="2004-01-01")
    cands = {
        "一直持有SPY": S.buy_hold(m, "SPY"),
        "60/40": S.static(m, {"SPY": 0.6, "IEF": 0.4}),
        "终选方案": S.defensive_core(m, "SPY", "IEF", FINAL_TARGET),
        "终选方案(波动率12%)": S.defensive_core(m, "SPY", "IEF", 0.12),
    }
    summary, crises, equity, results = evaluate(cands, m, "2005-01-01", "next_open", 5)
    report("SPY + IEF 2005-2026（次日开盘成交，成本 5bp）", summary, crises)
    save(summary, "etf_summary"), save(crises, "etf_crises"), save(equity, "etf_equity")
    final = results["终选方案"]
    save(final.holdings.loc["2005-01-01":], "etf_final_holdings")
    dd = pd.DataFrame({name: metrics.drawdown(equity[name].pct_change().fillna(0)) for name in equity})
    save(dd, "etf_drawdown")
    # 未来 10 年最大回撤的分布（自助法），画直方图用
    boot = {name: metrics.bootstrap_mdd(results[name].ret.loc["2005-01-01":], 10) for name in ("一直持有SPY", "60/40", "终选方案")}
    save(pd.DataFrame(boot), "etf_bootstrap_mdd")


def part_markets() -> None:
    rows = {}
    for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM"]:
        m = data.etf_market([sym, "IEF"], start="2004-01-01")
        start = "2005-01-01"
        cands = {"一直持有": S.buy_hold(m, sym), "终选方案": S.defensive_core(m, sym, "IEF", FINAL_TARGET)}
        summary, crises, _, _ = evaluate(cands, m, start, "next_open", 5, bootstrap=False)
        for name in cands:
            rows[(sym, name)] = {**summary.loc[name, ["年化收益", "年化波动", "夏普", "最大回撤", "Calmar", "年换手"]].to_dict(),
                                 **{k: crises.loc[name].get(k, np.nan) for k in ("2008 金融危机", "2020 新冠", "2022 加息熊市")}}
    df = pd.DataFrame(rows).T
    print("\n===== 其他市场（各自 + IEF，2005-2026）")
    print(df.to_string(float_format=lambda x: f"{x:.3f}"))
    save(df, "markets_summary")


def basket_market(symbols: list[str], bond: str) -> data.Market:
    """把几只股票做成每天再平衡的等权组合（当作一个资产 BASKET），再加上债券。"""
    m = data.etf_market(symbols + [bond])
    def ew(frame):
        return frame[symbols].mean(axis=1)
    close_ret = pd.DataFrame({"BASKET": ew(m.close_ret), bond: m.close_ret[bond]})
    gap = pd.DataFrame({"BASKET": ew(m.gap_ret), bond: m.gap_ret[bond]})
    intra = pd.DataFrame({"BASKET": ew(m.intra_ret), bond: m.intra_ret[bond]})
    close = (1 + close_ret).cumprod()
    return data.Market(close_ret, m.rf, close, gap, intra)


def part_stocks() -> None:
    rows, equity = {}, {}
    start = "2018-01-01"
    for sym in MY_STOCKS:
        m = data.etf_market([sym, "IEF"])
        cands = {"一直持有": S.buy_hold(m, sym), "终选方案": S.defensive_core(m, sym, "IEF", FINAL_TARGET)}
        summary, _, eq, _ = evaluate(cands, m, start, "next_open", 5, bootstrap=False)
        for name in cands:
            rows[(sym, name)] = summary.loc[name, ["年化收益", "年化波动", "夏普", "最大回撤", "Calmar", "平均仓位", "年换手"]].to_dict()
            equity[f"{sym}|{name}"] = eq[name]
    m = basket_market(MY_STOCKS, "IEF")
    cands = {"一直持有": S.buy_hold(m, "BASKET"), "终选方案": S.defensive_core(m, "BASKET", "IEF", FINAL_TARGET),
             "终选方案(波动率15%)": S.defensive_core(m, "BASKET", "IEF", 0.15)}
    summary, crises, eq, _ = evaluate(cands, m, start, "next_open", 5, bootstrap=False)
    for name in cands:
        rows[("5只等权", name)] = summary.loc[name, ["年化收益", "年化波动", "夏普", "最大回撤", "Calmar", "平均仓位", "年换手"]].to_dict()
        equity[f"5只等权|{name}"] = eq[name]
    df = pd.DataFrame(rows).T
    print(f"\n===== 你关注的 5 只股票（{start} 起，+ IEF）")
    print(df.to_string(float_format=lambda x: f"{x:.3f}"))
    save(df, "stocks_summary"), save(pd.DataFrame(equity), "stocks_equity")


def part_models() -> None:
    from sklearn.metrics import roc_auc_score

    ff = data.load_french()
    ret = ff["mkt"]
    close = (1 + ret).cumprod()
    vix = data.load_fred("VIXCLS")

    # 1. 波动率预测
    actual = models.future_vol(ret)
    fc = {meth: models.forecast_vol(ret, meth, 1950) for meth in ("rv20", "ewma", "har", "garch")}
    fc["har_vix"] = models.forecast_vol(ret, "har_vix", 1995, vix=vix)
    rows = {}
    for period, (a, b) in {"1950-2026": ("1950", "2026"), "1995-2026": ("1995", "2026")}.items():
        for meth, f in fc.items():
            if meth == "har_vix" and a == "1950":
                continue
            rows[(period, meth)] = models.vol_forecast_scores(f.loc[a:b], actual.loc[a:b], fc["rv20"].loc[a:b])
    vol_table = pd.DataFrame(rows).T
    print("\n===== 波动率预测（样本外）")
    print(vol_table.to_string(float_format=lambda x: f"{x:.3f}"))
    save(vol_table, "models_vol_forecast")

    m = data.french_market()
    trend = S.trend_signal(m.close["US_STOCK"], 200)
    strat = {}
    for meth in ("rv20", "ewma", "har", "garch"):
        w = (0.10 / fc[meth]).clip(upper=1.0).reindex(m.close.index) * trend
        strat[f"200日均线×波动率10%（{meth}）"] = w.to_frame("US_STOCK")
    summary, crises, _, _ = evaluate(strat, m, "1951-01-01", "next_close", 10)
    report("用不同的波动率预测做仓位（1951-2026）", summary)
    save(summary, "models_vol_strategy")

    # 2. 危机概率：各模型的样本外 AUC，和“只看一个简单指标”比较
    label = models.crash_label(close)
    feats = models.crash_features(close)
    probs = {name: models.walk_forward_classifier(feats, label, name, 1950) for name in ("logit", "gbm", "mlp")}
    probs["hmm"] = models.markov_turbulence(ret, 1950)
    probs["只看波动率"] = feats["vol_20"]
    probs["只看离200日均线"] = -feats["dist_sma200"]
    term = (data.load_fred("DGS10") - data.load_fred("DTB3")).dropna()
    rich = {"term": term, "credit": data.load_fred("BAA10Y"), "vix": vix}
    rich_feats = models.crash_features(close, rich).loc["1990-01-01":]
    for name in ("logit", "gbm", "mlp"):
        probs[f"{name}+宏观VIX"] = models.walk_forward_classifier(rich_feats, label.reindex(rich_feats.index), name, 2000)
    auc = {}
    for name, p in probs.items():
        for a, b in (("1950", "1989"), ("1990", "2026"), ("2000", "2026")):
            if "宏观" in name and a != "2000":
                continue  # 带宏观特征的模型 2000 年才开始有预测
            d = pd.DataFrame({"p": p, "y": label}).loc[a:b].dropna()
            auc[(name, f"{a}-{b}")] = roc_auc_score(d.y, d.p)
    auc_table = pd.Series(auc).unstack()
    print("\n===== 危机概率：样本外 AUC（0.5 = 瞎猜）")
    print(auc_table.to_string(float_format=lambda x: f"{x:.3f}"))
    save(auc_table, "models_auc")

    # 3. 把模型概率叠加到“200日均线×波动率10%”上，看能不能进一步减少回撤
    base = S.trend_signal(m.close["US_STOCK"], 200, 0.02) * S.vol_weight(m.close_ret["US_STOCK"], 0.10)
    overlay = {"不叠加": base}
    for name in ("logit", "gbm", "mlp"):
        overlay[f"×(1-2×{name}概率)"] = base * (1 - 2 * probs[name].reindex(base.index)).clip(0, 1)
    overlay["×(1-hmm动荡概率)"] = base * (1 - probs["hmm"].reindex(base.index))
    overlay["×(hmm动荡概率<0.5)"] = base * (probs["hmm"].reindex(base.index) < 0.5)
    summary, crises, _, _ = evaluate({k: v.to_frame("US_STOCK") for k, v in overlay.items()}, m, "1951-01-01", "next_close", 10)
    report("叠加模型概率（1951-2026）", summary)
    save(summary, "models_overlay")


def part_robust() -> None:
    rows = {}
    sets = {"美国1951-2026": (data.french_market(), "US_STOCK", None, "1951-01-01", "next_close", 10),
            "SPY+IEF 2005-2026": (data.etf_market(["SPY", "IEF"], start="2004-01-01"), "SPY", "IEF", "2005-01-01", "next_open", 5)}
    for label, (m, stock, bond, start, ex, cost) in sets.items():
        for windows_name, windows in {"单一200日": (200,), "多周期50-250": (50, 100, 150, 200, 250),
                                      "多周期100-300（终选）": (100, 150, 200, 250, 300)}.items():
            for target in (0.06, 0.08, 0.10, 0.12, 0.15, 0.20):
                tgt = S.defensive_core(m, stock, bond, target, windows=windows)
                r = engine.run(tgt.fillna(0), m, execution=ex, cost_bps=cost, band=0.05)
                s = metrics.summary(r.ret.loc[start:], m.rf, r.exposure.loc[start:], r.turnover.loc[start:])
                rows[(label, windows_name, target)] = {k: s[k] for k in ("年化收益", "最大回撤", "夏普", "年换手")}
    grid = pd.DataFrame(rows).T
    print("\n===== 参数敏感性")
    print(grid.to_string(float_format=lambda x: f"{x:.3f}"))
    save(grid, "robust_params")

    m = data.etf_market(["SPY", "IEF"], start="2004-01-01")
    tgt = S.defensive_core(m, "SPY", "IEF", FINAL_TARGET)
    rows = {}
    for ex in ("close", "next_open", "next_close"):
        for cost in (0, 5, 10, 20):
            for band in (0.02, 0.05, 0.10):
                r = engine.run(tgt.fillna(0), m, execution=ex, cost_bps=cost, band=band)
                s = metrics.summary(r.ret.loc["2005-01-01":], m.rf, None, r.turnover.loc["2005-01-01":])
                rows[(ex, cost, band)] = {k: s[k] for k in ("年化收益", "最大回撤", "夏普", "年换手")}
    costs = pd.DataFrame(rows).T
    costs.index.names = ["成交时点", "成本bp", "再平衡阈值"]
    print("\n===== 成交时点 / 交易成本 / 再平衡阈值（SPY+IEF 2005-2026）")
    print(costs.to_string(float_format=lambda x: f"{x:.3f}"))
    save(costs, "robust_costs")


def part_frontier() -> None:
    sets = {"us1963": (data.french_market(with_bond=True), "US_STOCK", "US_BOND10", "1963-07-01", "next_close", 10),
            "etf2005": (data.etf_market(["SPY", "IEF"], start="2004-01-01"), "SPY", "IEF", "2005-01-01", "next_open", 5)}
    for tag, (m, stock, bond, start, ex, cost) in sets.items():
        rows = []
        def add(family, param, target):
            r = engine.run(target.fillna(0), m, execution=ex, cost_bps=cost, band=0.05)
            s = metrics.summary(r.ret.loc[start:], m.rf)
            rows.append({"做法": family, "参数": param, "年化收益": s["年化收益"], "最大回撤": s["最大回撤"],
                         "年化波动": s["年化波动"], "夏普": s["夏普"]})
        for x in np.round(np.arange(0, 1.01, 0.1), 1):
            add("静态股票+国债", x, S.static(m, {stock: x, bond: 1 - x}))
            add("静态股票+现金", x, S.static(m, {stock: x}))
        for t in (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30):
            add("终选方案(不同波动率目标)", t, S.defensive_core(m, stock, bond, t))
        save(pd.DataFrame(rows), f"frontier_{tag}")
        print(f"\n===== 有效前沿 {tag}")
        print(pd.DataFrame(rows).to_string(float_format=lambda x: f"{x:.3f}"))


def part_news() -> None:
    """新闻 / 调查类指标能不能帮助判断“牛市里的回调”还是“熊市的开头”。"""
    from sklearn.metrics import roc_auc_score

    from autotrader.research import news

    windows = (100, 150, 200, 250, 300)
    ff = data.load_french()
    close = (1 + ff["mkt"]).cumprod()
    scores = {name: news.score(name, close.index) for name in news.INDICATORS}
    trend = S.trend_ensemble(close, windows, 0.01)
    vol20 = S.realized_vol(ff["mkt"], 20)
    dist = close / close.rolling(200).mean() - 1

    # 1. 事件研究：每次“趋势从满分开始转弱”时，新闻能否区分之后是恢复（回调）还是继续下跌
    breaks = news.trend_breaks(trend.loc["1988-07-01":])
    fwd = news.forward_excess(close, ff["rf"], 126).reindex(breaks)
    ev = pd.DataFrame({"未来半年超额收益": fwd, **{k: v.reindex(breaks) for k, v in scores.items()},
                       "低波动(-vol20)": -vol20.reindex(breaks), "离200日均线": dist.reindex(breaks)}).dropna()
    ev["之后恢复"] = ev["未来半年超额收益"] > 0
    rng = np.random.default_rng(0)
    rows = {}
    for col in [c for c in ev.columns if c not in ("未来半年超额收益", "之后恢复")]:
        auc = roc_auc_score(ev["之后恢复"], ev[col])
        perm = [roc_auc_score(rng.permutation(ev["之后恢复"].to_numpy()), ev[col]) for _ in range(5000)]
        rows[col] = {"AUC": auc, "p值(双侧)": float(np.mean(np.abs(np.array(perm) - 0.5) >= abs(auc - 0.5))),
                     "恢复组平均分": ev.loc[ev["之后恢复"], col].mean(), "下跌组平均分": ev.loc[~ev["之后恢复"], col].mean()}
    event_auc = pd.DataFrame(rows).T
    print(f"\n===== 事件研究：{len(ev)} 次趋势转弱（{ev.index[0]:%Y-%m} ~ {ev.index[-1]:%Y-%m}），"
          f"其中 {int(ev['之后恢复'].sum())} 次之后半年跑赢现金")
    print("AUC > 0.5：分数越高越可能恢复；p 值 < 0.05 才算统计上可信")
    print(event_auc.to_string(float_format=lambda x: f"{x:.3f}"))
    save(ev, "news_events"), save(event_auc, "news_event_auc")

    # 2. 预测：在“波动 + 离均线距离”之外加入新闻分数，样本外预测“未来一个月跌超 5%”的能力有没有提高
    label = models.crash_label(close)
    base = pd.DataFrame({"vol_20": vol20, "dist_sma200": dist}).loc["1989-01-01":]
    auc = {}
    for name in [None, *news.INDICATORS]:
        feats = base if name is None else base.join(scores[name])
        p = models.walk_forward_classifier(feats, label.reindex(feats.index), "logit", 1997)
        d = pd.DataFrame({"p": p, "y": label}).loc["1997":].dropna()
        auc["只用波动+均线" if name is None else f"+ {news.INDICATORS[name]['名称']}"] = roc_auc_score(d.y, d.p)
    auc = pd.Series(auc, name="样本外AUC 1997-2026")
    print("\n===== 预测未来一个月跌超 5%（逻辑回归，每年滚动训练）")
    print(auc.to_string(float_format=lambda x: f"{x:.3f}"))
    save(auc.to_frame(), "news_predict_auc")

    # 3. 策略：用新闻决定“牛市里更激进”或“回调时不减仓”，和不看新闻的版本对比
    sets = {"美国股票+国债 1990-2026": (data.french_market(with_bond=True), "US_STOCK", "US_BOND10", "1990-01-01", "next_close", 10),
            "SPY+IEF 2005-2026": (data.etf_market(["SPY", "IEF"], start="2004-01-01"), "SPY", "IEF", "2005-05-20", "next_open", 5)}
    rows = {}
    for label_set, (m, stock, bond, start, ex, cost) in sets.items():
        T = S.trend_ensemble(m.close[stock], windows, 0.01)
        vol = S.realized_vol(m.close_ret[stock], 20)
        B = S.trend_signal(m.close[bond], 200, 0.01)

        def build(trend, target):
            ws = trend * (target / vol).clip(upper=1.0)
            return S._frame(m, {stock: ws, bond: (1 - ws) * B})

        cands = {"现行（不看新闻）": build(T, 0.12),
                 "牛市一律放宽到20%": build(T, pd.Series(0.12, index=T.index).where(T < 1, 0.20))}
        for name, spec in news.INDICATORS.items():
            good = news.score(name, m.close.index) > 0
            cands[f"牛市且{spec['名称']}好→20%"] = build(T, pd.Series(0.12, index=T.index).where(~((T == 1) & good), 0.20))
            cands[f"转弱时{spec['名称']}好→不减仓"] = build(T.where(~((T < 1) & good), 1.0), 0.12)
        for name, target in cands.items():
            r = engine.run(target.fillna(0), m, execution=ex, cost_bps=cost, band=0.05)
            ret = r.ret.loc[start:]
            s = metrics.summary(ret, m.rf, r.exposure.loc[start:])
            mdd = metrics.bootstrap_mdd(ret, 10)
            c = metrics.crisis_returns(r.ret)
            rows[(label_set, name)] = {
                "年化收益": s["年化收益"], "最大回撤": s["最大回撤"], "10年回撤>20%概率": float((mdd < -0.2).mean()),
                "平均仓位": s["平均仓位"],
                "2013-19牛市年化": metrics.summary(r.ret.loc["2013":"2019"])["年化收益"],
                "2023-26牛市年化": metrics.summary(r.ret.loc["2023":"2026"])["年化收益"],
                **{k[:4]: c[k] for k in ("2000 互联网泡沫", "2008 金融危机", "2020 新冠", "2022 加息熊市")}}
    table = pd.DataFrame(rows).T
    print("\n===== 策略：用新闻调整激进程度")
    print(table.to_string(float_format=lambda x: f"{x:.3f}"))
    save(table, "news_strategy")


def part_economy() -> None:
    """经济周期指标（失业率、首次申请失业金、萨姆指标、收益率曲线）：
    趋势转弱时，经济没有转差就不减仓（Growth-Trend Timing 的思路）。"""
    from sklearn.metrics import roc_auc_score

    from autotrader.research import economy, news

    windows = (100, 150, 200, 250, 300)
    names = list(economy.NAMES)

    # 1. 事件研究：趋势转弱那天的经济状况，能否区分之后恢复还是继续下跌
    ff = data.load_french()
    close = (1 + ff["mkt"]).cumprod()
    trend = S.trend_ensemble(close, windows, 0.01)
    breaks = news.trend_breaks(trend.loc["1950-01-01":])
    fwd = news.forward_excess(close, ff["rf"], 126).reindex(breaks)
    ev = pd.DataFrame({"未来半年超额收益": fwd, **{n: economy.economy_good(n, close.index).reindex(breaks) for n in names}})
    ev = ev.dropna(subset=["未来半年超额收益"])
    ev["之后恢复"] = ev["未来半年超额收益"] > 0
    rows = {}
    for n in names:
        for a, b in (("1950", "1988"), ("1989", "2026")):
            d = ev.loc[a:b, [n, "之后恢复", "未来半年超额收益"]].dropna()
            if d[n].nunique() < 2:
                continue
            rows[(economy.NAMES[n], f"{a}-{b}")] = {
                "事件数": len(d), "经济好的次数": int(d[n].sum()),
                "经济好时·之后恢复比例": d.loc[d[n] == 1, "之后恢复"].mean(),
                "经济差时·之后恢复比例": d.loc[d[n] == 0, "之后恢复"].mean(),
                "经济好时·半年超额收益": d.loc[d[n] == 1, "未来半年超额收益"].mean(),
                "经济差时·半年超额收益": d.loc[d[n] == 0, "未来半年超额收益"].mean(),
                "AUC": roc_auc_score(d["之后恢复"], d[n])}
    event_table = pd.DataFrame(rows).T
    print(f"\n===== 事件研究：{len(ev)} 次趋势转弱（{ev.index[0]:%Y-%m} ~ {ev.index[-1]:%Y-%m}）")
    print(event_table.to_string(float_format=lambda x: f"{x:.3f}"))
    save(ev, "economy_events"), save(event_table, "economy_event_table")

    # 2. 策略：趋势转弱时经济好 → 不减仓
    sets = {"美国股票+国债": (data.french_market(with_bond=True), "US_STOCK", "US_BOND10", "next_close", 10,
                         [("1963-07-01", "1989-12-31"), ("1990-01-01", "2015-12-31"), ("2016-01-01", "2026-12-31")]),
            "SPY+IEF": (data.etf_market(["SPY", "IEF"], start="2004-01-01"), "SPY", "IEF", "next_open", 5,
                        [("2005-05-20", "2026-12-31")])}
    rows, placebo_rows = {}, {}
    for set_name, (m, stock, bond, ex, cost, periods) in sets.items():
        T = S.trend_ensemble(m.close[stock], windows, 0.01)
        vol = S.realized_vol(m.close_ret[stock], 20)
        B = S.trend_signal(m.close[bond], 200, 0.01)

        def run_rule(good: pd.Series | None):
            t = T if good is None else T.where(~((T < 1) & (good == 1)), 1.0)
            ws = t * (0.12 / vol).clip(upper=1.0)
            return engine.run(S._frame(m, {stock: ws, bond: (1 - ws) * B}).fillna(0), m,
                              execution=ex, cost_bps=cost, band=0.05).ret

        goods = {n: economy.economy_good(n, m.close.index) for n in names}
        goods["两项就业指标都好"] = ((goods["unemployment"] == 1) & (goods["claims"] == 1)).astype(float).where(
            goods["claims"].notna())
        goods["消费者信心（上次）"] = (news.score("UMCSENT", m.close.index) > 0).astype(float)
        results = {"现行（不看经济）": run_rule(None), **{economy.NAMES.get(k, k): run_rule(g) for k, g in goods.items()}}
        for name, ret in results.items():
            for a, b in periods:
                s = metrics.summary(ret.loc[a:b], m.rf)
                c = metrics.crisis_returns(ret.loc[a:b])
                rows[(set_name, f"{a[:4]}-{b[:4]}", name)] = {
                    "年化收益": s["年化收益"], "最大回撤": s["最大回撤"],
                    **{k[:4]: c[k] for k in ("1973 石油危机", "1987 黑色星期一", "2000 互联网泡沫", "2008 金融危机",
                                             "2020 新冠", "2022 加息熊市") if not np.isnan(c[k])}}
        # 安慰剂：把经济信号的时间轴错开 3~10 年，看真实信号是否比“随机时机”更好
        if set_name == "美国股票+国债":
            a, b = "1963-07-01", "2026-12-31"
            base = metrics.summary(results["现行（不看经济）"].loc[a:b], m.rf)
            for k, g in goods.items():
                label = economy.NAMES.get(k, k)
                real = metrics.summary(results[label].loc[a:b], m.rf)
                plc = []
                for years in range(3, 11):
                    shifted = g.shift(252 * years).fillna(g.shift(-252 * years))
                    plc.append(metrics.summary(run_rule(shifted).loc[a:b], m.rf))
                gains = np.array([p["年化收益"] - base["年化收益"] for p in plc])
                placebo_rows[label] = {
                    "真实·收益提升": real["年化收益"] - base["年化收益"], "真实·最大回撤": real["最大回撤"],
                    "安慰剂·平均收益提升": gains.mean(), "安慰剂·最大回撤(中位)": float(np.median([p["最大回撤"] for p in plc])),
                    "安慰剂比真实好的比例": float((gains >= real["年化收益"] - base["年化收益"]).mean())}
            placebo_rows["（现行）"] = {"真实·最大回撤": base["最大回撤"]}
    table = pd.DataFrame(rows).T
    print("\n===== 策略：趋势转弱时经济好 → 不减仓（波动率目标 12%）")
    print(table.to_string(float_format=lambda x: f"{x:.3f}"))
    placebo = pd.DataFrame(placebo_rows).T
    print("\n===== 安慰剂对照（美国股票+国债 1963-2026，经济信号错开 3~10 年）")
    print(placebo.to_string(float_format=lambda x: f"{x:.3f}"))
    save(table, "economy_strategy"), save(placebo, "economy_placebo")


def part_universe() -> None:
    """选股参考：约 70 家公司的客观数据，写成 results/research/选股参考.md（先运行 fetch_data.py --universe）。"""
    from autotrader.config import ROOT
    from autotrader.research.universe import CHINA_UNIVERSE, UNIVERSE

    spy = data.load_etf("SPY")["close"]
    end = spy.index[-1]                      # 所有股票都截到同一天，避免混入盘中未完成的日线
    start10 = end - pd.DateOffset(years=10)
    china_crash = ("2021-02-16", "2022-10-31")  # 中概股大跌：教育“双减”、平台监管、退市风险
    entries = ([(s, n, sec, d, "") for s, n, sec, d in UNIVERSE]
               + [(s, n, f"中国·{ind}", d, ex) for s, n, ind, d, ex in CHINA_UNIVERSE]
               + [("SPY", "标普 500 ETF（对照）", "对照", "美国 500 家大公司", "NYSE")])
    rows, daily = [], {}
    for sym, name, sector, desc, exchange in entries:
        try:
            close = data.load_etf(sym)["close"].loc[:end]
        except FileNotFoundError:
            print(f"{sym}: 没有数据，跳过")
            continue
        ret = close.pct_change().dropna()
        r10 = ret.loc[start10:]
        daily[sym] = r10
        s10 = metrics.summary(r10)
        crisis = metrics.crisis_returns(ret)
        window = ret.loc[china_crash[0]:china_crash[1]].iloc[1:]
        trend = S.trend_ensemble(close, (100, 150, 200, 250, 300), 0.01)
        rows.append({
            "代码": sym, "公司": name, "行业": sector, "简介": desc, "交易所": exchange,
            "数据起始": close.index[0].strftime("%Y"),
            "10年年化": s10["年化收益"], "10年波动": s10["年化波动"], "10年最大回撤": s10["最大回撤"],
            "2008危机": crisis["2008 金融危机"] if close.index[0] < pd.Timestamp("2007-10-01") else np.nan,
            "2020新冠": crisis["2020 新冠"], "2022熊市": crisis["2022 加息熊市"],
            "2021-22中概暴跌": float((1 + window).prod() - 1) if close.index[0] < pd.Timestamp(china_crash[0]) else np.nan,
            "与SPY相关": r10.corr(spy.pct_change().reindex(r10.index)),
            "当前趋势": trend.iloc[-1], "近20日波动": S.realized_vol(ret, 20).iloc[-1],
        })
    df = pd.DataFrame(rows)
    save(df.set_index("代码"), "universe")

    # 组内平均相关性：同一组股票之间一起涨跌的程度（越高，组合起来越分散不了风险）
    rets = pd.DataFrame(daily)
    def avg_corr(symbols):
        c = rets[symbols].dropna(how="all").corr().to_numpy()
        return float(c[np.triu_indices_from(c, k=1)].mean())
    us_syms = [u[0] for u in UNIVERSE if u[0] in rets]
    cn_syms = [u[0] for u in CHINA_UNIVERSE if u[0] in rets]
    corr_us, corr_cn = avg_corr(us_syms), avg_corr(cn_syms)
    corr_cross = float(rets[us_syms].corrwith(rets[cn_syms].mean(axis=1)).mean())

    def pct(x, digits=0):
        return "—" if pd.isna(x) else f"{x:.{digits}%}"

    lines = [
        "# 选股参考", "",
        f"> 数据截至 {end:%Y-%m-%d}，来源：盈透复权日线（含分红）。由 `python research.py universe` 生成。",
        "> **这是供研究的候选池，不是买入推荐。** 美股部分：每个行业里规模最大、历史较长的公司，覆盖全部 11 个行业；"
        "中国部分：在美国交易所上市、处于国内各行业头部的公司。", "",
        "## 怎么看这些数字", "",
        "| 列 | 含义 |", "|---|---|",
        "| 10年年化 | 过去 10 年平均每年涨多少（含分红）；上市不满 10 年的按上市以来计算（见“数据起始”） |",
        "| 10年波动 | 每年的涨跌幅度有多大（年化标准差）。SPY 约 18%；越高，在策略里分到的仓位越小 |",
        "| 10年最大回撤 | 过去 10 年里从高点最多跌了多少 |",
        "| 2008 / 2020 / 2022 | 三次大跌期间（市场高点到低点）这只股票的涨跌 |",
        "| 与SPY相关 | 和美国大盘一起涨跌的程度：1 = 完全同步，越低越能分散风险 |",
        "| 当前趋势 | 策略的多周期趋势信号：100% = 价格在五条均线之上（上升趋势），0% = 五条全部跌破（下跌趋势） |", "",
        "**重要提醒**：",
        "- 这些是**今天**的大公司，它们能成为大公司，本身就说明过去涨得好（幸存者偏差）。过去 10 年的高收益**不能**说明未来也会这样——"
        "回顾 2000 年和 2010 年的头部公司，大约一半之后跑输了 SPY。",
        "- 选几只组合时，尽量**跨行业、相关性低**，否则会一起涨一起跌，防守效果变差。", "",
        "# 一、美国公司", "",
    ]
    us = df[~df["行业"].str.startswith("中国") & (df["行业"] != "对照")]
    for sector in dict.fromkeys(us["行业"]):
        sub = us[us["行业"] == sector]
        lines += [f"## {sector}", "",
                  "| 代码 | 公司 | 简介 | 10年年化 | 10年波动 | 10年最大回撤 | 2008 | 2020 | 2022 | 与SPY相关 | 当前趋势 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in sub.iterrows():
            lines.append(f"| {r['代码']} | {r['公司']} | {r['简介']} | {pct(r['10年年化'], 1)} | {pct(r['10年波动'])} | "
                         f"{pct(r['10年最大回撤'])} | {pct(r['2008危机'])} | {pct(r['2020新冠'])} | {pct(r['2022熊市'])} | "
                         f"{r['与SPY相关']:.2f} | {pct(r['当前趋势'])} |")
        lines.append("")

    cn = df[df["行业"].str.startswith("中国")]
    lines += [
        "# 二、中国公司（在美国交易所上市）", "",
        "大多是**存托凭证（ADR）**：在美国交易、以美元计价的“凭证”，代表公司在海外的股份。",
        "腾讯、小米、比亚迪、宁德时代、美团、茅台等**没有在美国交易所上市**（最多是场外交易），所以不在这里。", "",
        "**中国公司特有的风险**（和美国公司很不一样）：",
        "- **监管风险**：政策变化可以在一夜之间改变一个行业。2021 年“双减”让新东方一天跌 54%、累计跌约 90%；平台经济监管让阿里巴巴、"
        "京东等从高点跌了 70%~80%。",
        "- **VIE 结构**：多数公司是通过开曼群岛等地的离岸公司上市，投资者持有的是离岸公司的股份，"
        "并不直接拥有中国境内的经营实体，法律保障比美国公司弱。",
        "- **地缘政治和退市风险**：中美关系紧张时（例如 2020-2022 年的审计争议，《外国公司问责法案》），存在被要求从美国退市的风险。",
        f"- **彼此高度相关**：这 {len(cn_syms)} 家中国公司两两之间的平均相关系数是 **{corr_cn:.2f}**，"
        f"而 {len(us_syms)} 家美国公司之间只有 {corr_us:.2f}——中概股常常一起暴涨暴跌（例如 2022-03-16 集体上涨 30%~64%），"
        "买好几只中概股的分散效果远不如买几只不同行业的美国公司。",
        f"- 反过来，中概股**和美股的相关性较低**（美国公司与中概股整体的平均相关只有约 {corr_cross:.2f}），"
        "少量配置可以起到一定的分散作用。", "",
        "| 代码 | 公司 | 国内行业 | 简介 | 交易所 | 数据起始 | 年化 | 波动 | 最大回撤 | 2020 | 2021-22 中概暴跌 | 2022 | 与SPY相关 | 当前趋势 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in cn.iterrows():
        lines.append(f"| {r['代码']} | {r['公司']} | {r['行业'].removeprefix('中国·')} | {r['简介']} | {r['交易所']} | "
                     f"{r['数据起始']} | {pct(r['10年年化'], 1)} | {pct(r['10年波动'])} | {pct(r['10年最大回撤'])} | "
                     f"{pct(r['2020新冠'])} | {pct(r['2021-22中概暴跌'])} | {pct(r['2022熊市'])} | "
                     f"{r['与SPY相关']:.2f} | {pct(r['当前趋势'])} |")
    lines += ["", "“年化”“波动”“最大回撤”：2016 年以后上市的公司按上市以来计算。“2021-22 中概暴跌”指 2021-02-16 到 2022-10-31。", ""]

    spy_row = df[df["代码"] == "SPY"].iloc[0]
    lines += ["# 对照：SPY", "",
              f"10 年年化 {pct(spy_row['10年年化'], 1)}，10 年波动 {pct(spy_row['10年波动'])}，10 年最大回撤 {pct(spy_row['10年最大回撤'])}，"
              f"2008 {pct(spy_row['2008危机'])}，2020 {pct(spy_row['2020新冠'])}，2021-22 中概暴跌期间 {pct(spy_row['2021-22中概暴跌'])}，"
              f"2022 {pct(spy_row['2022熊市'])}，当前趋势 {pct(spy_row['当前趋势'])}。", ""]
    path = ROOT / "results" / "research" / "选股参考.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n===== 选股参考：美国 {len(us)} 家 + 中国 {len(cn)} 家 → {path}")
    print(f"组内平均相关：美国 {corr_us:.2f}，中国 {corr_cn:.2f}；美国与中概整体 {corr_cross:.2f}")
    print(cn.drop(columns=["简介"]).to_string(float_format=lambda x: f"{x:.2f}"))


PARTS = {"long": part_long, "bond": part_bond, "etf": part_etf, "markets": part_markets,
         "stocks": part_stocks, "models": part_models, "robust": part_robust, "frontier": part_frontier,
         "news": part_news, "economy": part_economy, "universe": part_universe}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("parts", nargs="*", help=f"要跑的部分，默认全部：{' '.join(PARTS)}")
    args = parser.parse_args()
    unknown = [p for p in args.parts if p not in PARTS]
    if unknown:
        parser.error(f"不认识的部分：{unknown}，可选：{list(PARTS)}")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    for name in args.parts or PARTS:
        t0 = time.time()
        PARTS[name]()
        print(f"[{name} 用时 {time.time() - t0:.0f} 秒]")
    print(f"\n结果已保存到 {OUT}")


if __name__ == "__main__":
    main()
