"""候选股票池图表：每个候选股和防守资产一张日 K 线图，用蓝色阴影标出按当前规则持有的日子和仓位。
生成 docs/候选股票池.md 和 docs/candidate_charts/*.png。

用法（改了 config.toml 的候选股之后，先更新数据再生成）：
    .venv\\Scripts\\python fetch_data.py
    .venv\\Scripts\\python candidate_charts.py

持仓来自回测：用 config.toml 当前的规则，从 2004 年开始逐日模拟（和 backtest.py、模拟盘是同一套函数），
所以排名的“记忆”（缓冲）和实盘一致。图只显示 CHART_START 以后的部分。
"""
from __future__ import annotations

from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from autotrader import strategy  # noqa: E402
from autotrader.config import ROOT, load_config  # noqa: E402
from autotrader.research import data, engine, metrics  # noqa: E402
from autotrader.research import strategies as S  # noqa: E402
from autotrader.research.universe import CHINA_UNIVERSE, UNIVERSE  # noqa: E402

# 展示的起点：2025-04-02 美国宣布“解放日”对等关税，之后的市场环境是展示重点
CHART_START = "2025-04-01"
EVENTS = {"2025-04-02": "Liberation Day tariffs"}
DOC = ROOT / "docs" / "候选股票池.md"
IMG_DIR = ROOT / "docs" / "candidate_charts"
HELD = 0.001                      # 仓位超过 0.1% 才算持有
UP, DOWN = "#26a69a", "#ef5350"   # 涨 绿，跌 红（美股习惯）
SHADE = "#1f6fd1"                 # 持仓阴影的颜色：仓位越大越不透明

EXTRA = {   # 不在 universe.py 里的标的：(中文名, 行业, 简介)
    "VZ": ("Verizon", "电信", "按营收和用户数是美国最大的电信运营商"),
    "IEF": ("7–10 年期美国国债 ETF", "防守资产", "股市下跌时常常上涨；自己低于 200 日均线时那一份改持现金"),
    "GLD": ("黄金 ETF", "防守资产", "与股票相关性低的避险资产；自己低于 200 日均线时那一份改持现金"),
    "GOOGL": ("谷歌（Alphabet A 类股）", "通信服务", "搜索广告、YouTube、谷歌云、Waymo 自动驾驶"),
    "NFLX": ("网飞", "通信服务", "流媒体视频订阅"),
}


def describe(sym: str) -> tuple[str, str, str]:
    if sym in EXTRA:
        return EXTRA[sym]
    for row in UNIVERSE + CHINA_UNIVERSE:
        if row[0] == sym:
            return row[1], row[2], row[3]
    return sym, "", ""


def simulate(cfg: dict, p: dict):
    """按当前规则从 2004 年开始模拟，返回 (每天收盘后的实际持仓占比, 目标权重, 满额比例, 排名得分, 是否入选, 每日收益, 市场数据)。"""
    syms = strategy.data_symbols(p)
    m = data.etf_market(syms, start="2004-01-01", how="outer")
    frame, size, score, chosen = S.sleeves_core(m, **strategy.sleeves_kwargs(p), detail=True)
    r = engine.run(frame.fillna(0), m, execution="next_open", cost_bps=cfg["backtest"]["cost_bps"],
                   band=p["rebalance_band"], drift_only=p.get("drift_only", False))
    return r.holdings, frame, size, score, chosen, r.ret, m


def plot(sym: str, ohlc: pd.DataFrame, w: pd.Series, full: float, path) -> None:
    """上：日 K 线 + 持仓阴影（越深仓位越大）；下：每天的持仓占比。"""
    x = np.arange(len(ohlc))
    o, h, lo, c = (ohlc[k].to_numpy() for k in ("open", "high", "low", "close"))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 5.4), sharex=True,
                                  gridspec_kw={"height_ratios": [4, 1.1], "hspace": 0.06})
    norm = Normalize(0, full)
    for i, wi in enumerate(w.to_numpy()):
        if wi > HELD:
            ax.axvspan(i - 0.5, i + 0.5, color=SHADE, alpha=0.08 + 0.32 * norm(min(wi, full)), lw=0)
    up = c >= o
    ax.vlines(x, lo, h, color=np.where(up, UP, DOWN), linewidth=0.8)
    for i in x:
        bottom, height = min(o[i], c[i]), max(abs(c[i] - o[i]), (h[i] - lo[i]) * 0.02)
        ax.add_patch(Rectangle((i - 0.35, bottom), 0.7, height, color=UP if up[i] else DOWN, lw=0))
    ax.set_xlim(-1, len(x))
    ax.set_ylim(lo.min() * 0.97, h.max() * 1.03)
    ax.set_ylabel("Price (adjusted, USD)")
    for day, label in EVENTS.items():
        if ohlc.index[0] <= pd.Timestamp(day) <= ohlc.index[-1]:
            i = ohlc.index.searchsorted(pd.Timestamp(day))
            ax.axvline(i, color="0.3", ls="--", lw=1)
            ax.text(i + 1, ax.get_ylim()[1], label, va="top", fontsize=8, color="0.3")
    held = (w > HELD).mean()
    ax.set_title(f"{sym}   |   held on {held:.0%} of trading days since {ohlc.index[0]:%Y-%m-%d}   |   "
                 f"latest weight {w.iloc[-1]:.1%} of portfolio", fontsize=11, loc="left")
    ax.grid(alpha=0.25)

    ax2.fill_between(x, w.to_numpy() * 100, step="mid", color=SHADE, alpha=0.7, lw=0)
    ax2.set_ylim(0, full * 100 * 1.15)
    ax2.axhline(full * 100, color="0.5", lw=0.6, ls=":")
    ax2.set_ylabel("Weight %")
    ax2.grid(alpha=0.25)
    months = [i for i in range(len(x)) if i == 0 or ohlc.index[i].month != ohlc.index[i - 1].month]
    ax2.set_xticks(months)
    ax2.set_xticklabels([f"{ohlc.index[i]:%Y-%m}" for i in months], rotation=45, fontsize=8)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def plot_vs_spy(ret: pd.Series, holdings: pd.DataFrame, stocks: list[str], defense: list[str],
                stock_share: float, path) -> dict:
    """上：本方案、全部标的等额买入持有、SPY 一直持有的净值（起点 = 100），背景阴影 = 股票总仓位；
    下：每天的资产构成。返回区间统计。"""
    spy = data.load_etf("SPY")["close"]
    r = ret.loc[CHART_START:]
    idx = r.index
    if spy.index[-1] < idx[-1]:
        print(f"注意：SPY 数据只到 {spy.index[-1]:%Y-%m-%d}，先运行 fetch_data.py 更新")
    spy_ret = spy.reindex(idx.union(spy.index)).ffill().pct_change().reindex(idx).fillna(0.0)
    # 本金按标的数等分，在起点前一个交易日收盘买入，之后不再调整（还没上市的那一份当作现金）
    prev = spy.index[spy.index < idx[0]][-1]
    rel = pd.DataFrame({a: (lambda c: c.reindex(idx.union([prev])).ffill() / c.asof(prev))(data.load_etf(a)["close"])
                        for a in stocks + defense}).reindex(idx).fillna(1.0)
    eq = pd.DataFrame({"plan": 100 * (1 + r).cumprod(), "ew": 100 * rel.mean(axis=1), "spy": 100 * (1 + spy_ret).cumprod()})
    h = holdings.loc[idx]
    parts = pd.DataFrame({"Stocks": h[stocks].sum(axis=1), **{d: h[d] for d in defense}})
    parts["Cash"] = (1 - parts.sum(axis=1)).clip(lower=0)

    x = np.arange(len(idx))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 6.2), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.4], "hspace": 0.06})
    norm = Normalize(0, stock_share)
    for i, wi in enumerate(parts["Stocks"].to_numpy()):
        if wi > HELD:
            ax.axvspan(i - 0.5, i + 0.5, color=SHADE, alpha=0.03 + 0.40 * norm(min(wi, stock_share)), lw=0)
    ax.plot(x, eq["plan"], color="#0b3d91", lw=2, label="This strategy")
    ax.plot(x, eq["ew"], color="#2e8b57", lw=1.6,
            label=f"Equal-weight buy & hold\n({len(stocks) + len(defense)} assets above)")
    ax.plot(x, eq["spy"], color="#e07b00", lw=1.6, label="SPY buy & hold")
    ax.axhline(100, color="0.5", lw=0.6, ls=":")
    for day, label in EVENTS.items():
        if idx[0] <= pd.Timestamp(day) <= idx[-1]:
            i = idx.searchsorted(pd.Timestamp(day))
            ax.axvline(i, color="0.3", ls="--", lw=1)
            ax.text(i + 1, ax.get_ylim()[1], label, va="top", fontsize=8, color="0.3")

    def stats(e: pd.Series) -> dict:
        years = (idx[-1] - idx[0]).days / 365.25
        dd = (e / np.maximum(e.cummax(), 100) - 1).min()
        return {"total": e.iloc[-1] / 100 - 1, "cagr": (e.iloc[-1] / 100) ** (1 / years) - 1, "mdd": dd}
    st = {k: stats(eq[k]) for k in eq.columns}
    ax.set_title(f"Since {idx[0]:%Y-%m-%d} (max drawdown in brackets):  strategy {st['plan']['total']:+.1%} ({st['plan']['mdd']:.1%})  |  "
                 f"equal-weight {st['ew']['total']:+.1%} ({st['ew']['mdd']:.1%})  |  SPY {st['spy']['total']:+.1%} ({st['spy']['mdd']:.1%})"
                 f"  |  shading = stock weight", fontsize=10, loc="left")
    ax.set_ylabel("Growth of 100")
    ax.legend(loc="center left", frameon=False, bbox_to_anchor=(1.005, 0.5))
    ax.grid(alpha=0.25)

    colors = {"Stocks": SHADE, "IEF": "#7b68ae", "GLD": "#d4a017", "Cash": "#c8c8c8"}
    ax2.stackplot(x, *(parts[c].to_numpy() * 100 for c in parts.columns), labels=list(parts.columns),
                  colors=[colors.get(c, "0.6") for c in parts.columns], alpha=0.85, step="mid")
    ax2.set_ylim(0, 100)
    ax2.set_xlim(-1, len(x))
    ax2.set_ylabel("Allocation %")
    ax2.legend(loc="center left", fontsize=8, frameon=False, bbox_to_anchor=(1.005, 0.5))
    months = [i for i in range(len(x)) if i == 0 or idx[i].month != idx[i - 1].month]
    ax2.set_xticks(months)
    ax2.set_xticklabels([f"{idx[i]:%Y-%m}" for i in months], rotation=45, fontsize=8)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    st["stocks_avg"] = parts["Stocks"].mean()
    st["mix"] = parts.mean().to_dict()
    return st


CRISIS_LABELS = {"2008 金融危机": "2008 GFC", "2011 欧债危机": "2011", "2018 四季度": "2018 Q4",
                 "2020 新冠": "2020 COVID", "2022 加息熊市": "2022"}


def plot_long(m: data.Market, ret: pd.Series, holdings: pd.DataFrame, stocks: list[str], defense: list[str],
              k: int, cost_bps: float, path) -> dict:
    """长期比较：本方案、全部标的等分持有（偏离 5% 再平衡）、SPY 一直持有。
    上：净值（对数刻度）+ 危机区块；中：回撤；下：本方案的资产构成。返回统计。"""
    first = m.close.apply(lambda c: c.first_valid_index())
    enough = first.loc[stocks].sort_values().iloc[min(k, len(stocks)) - 1]
    start = max(first.loc[defense].max(), enough) + pd.Timedelta(days=500)     # 300 日均线的预热时间
    assets = stocks + defense
    listed = m.close[assets].notna().astype(float)
    ew_w = listed.div(listed.sum(axis=1), axis=0).reindex(columns=m.assets).fillna(0.0)
    ew = engine.run(ew_w, m, execution="next_open", cost_bps=cost_bps, band=0.05).ret
    spy = engine.run(S.buy_hold(m, "SPY").fillna(0), m, execution="next_open", cost_bps=cost_bps, band=0.05).ret
    rets = pd.DataFrame({"plan": ret, "ew": ew, "spy": spy}).loc[start:]
    eq = 100 * (1 + rets).cumprod()
    dd = eq / np.maximum(eq.cummax(), 100) - 1
    idx = eq.index
    x = np.arange(len(idx))
    h = holdings.loc[idx]
    parts = pd.DataFrame({"Stocks": h[stocks].sum(axis=1), **{d: h[d] for d in defense}})
    parts["Cash"] = (1 - parts.sum(axis=1)).clip(lower=0)

    fig, (ax, axd, ax2) = plt.subplots(3, 1, figsize=(13, 8.6), sharex=True,
                                       gridspec_kw={"height_ratios": [3, 1.5, 1.2], "hspace": 0.06})
    styles = {"plan": ("#0b3d91", 2.0, "This strategy"),
              "ew": ("#2e8b57", 1.4, f"Equal-weight {len(assets)} assets\n(rebalanced)"),
              "spy": ("#e07b00", 1.4, "SPY buy & hold")}
    for name, (a, b) in metrics.CRISES.items():
        if name in CRISIS_LABELS and idx[0] <= pd.Timestamp(a) <= idx[-1]:
            i0, i1 = idx.searchsorted(pd.Timestamp(a)), idx.searchsorted(pd.Timestamp(b))
            for axis in (ax, axd):
                axis.axvspan(i0, i1, color="#f2c4c4", alpha=0.5, lw=0)
            ax.text(i0, 1.0, CRISIS_LABELS[name], transform=ax.get_xaxis_transform(), va="bottom", fontsize=8, color="#a33")
    for col, (color, lw, label) in styles.items():
        ax.plot(x, eq[col], color=color, lw=lw, label=label)
        axd.plot(x, dd[col] * 100, color=color, lw=lw * 0.8)
    ax.set_yscale("log")
    ticks = [t for t in (50, 100, 200, 400, 800, 1600, 3200, 6400) if eq.min().min() * 0.9 <= t <= eq.max().max() * 1.1]
    ax.set_yticks(ticks)
    ax.set_yticklabels([str(t) for t in ticks])
    ax.minorticks_off()
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.legend(loc="center left", frameon=False, bbox_to_anchor=(1.005, 0.5))
    ax.grid(alpha=0.25)
    axd.axhline(-20, color="0.4", lw=0.8, ls="--")
    axd.text(len(x) * 0.005, -19, "-20%", fontsize=8, color="0.3", va="bottom")
    axd.set_ylabel("Drawdown %")
    axd.grid(alpha=0.25)
    colors = {"Stocks": SHADE, "IEF": "#7b68ae", "GLD": "#d4a017", "Cash": "#c8c8c8"}
    ax2.stackplot(x, *(parts[c].to_numpy() * 100 for c in parts.columns), labels=list(parts.columns),
                  colors=[colors.get(c, "0.6") for c in parts.columns], alpha=0.85, step="mid")
    ax2.set_ylim(0, 100)
    ax2.set_xlim(-1, len(x))
    ax2.set_ylabel("Allocation %")
    ax2.legend(loc="center left", fontsize=8, frameon=False, bbox_to_anchor=(1.005, 0.5))
    years = [i for i in range(len(x)) if i == 0 or idx[i].year != idx[i - 1].year]
    ax2.set_xticks(years)
    ax2.set_xticklabels([f"{idx[i]:%Y}" for i in years], rotation=45, fontsize=8)
    st = {k_: metrics.summary(rets[k_], m.rf) for k_ in rets}
    ax.set_title(f"Since {idx[0]:%Y-%m-%d}:  strategy {st['plan']['年化收益']:.1%}/yr (max drawdown {st['plan']['最大回撤']:.1%})  |  "
                 f"equal-weight {st['ew']['年化收益']:.1%}/yr ({st['ew']['最大回撤']:.1%})  |  "
                 f"SPY {st['spy']['年化收益']:.1%}/yr ({st['spy']['最大回撤']:.1%})", fontsize=10, loc="left", pad=14)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    crises = {k_: metrics.crisis_returns(rets[k_]) for k_ in rets}
    return {"start": idx[0], "summary": st, "crises": crises, "mix": parts.mean().to_dict()}


def main() -> None:
    cfg = load_config()
    p = strategy.load(cfg)
    if p["kind"] != "sleeves":
        raise SystemExit("当前策略不是个股方案（strategy.kind 不是 sleeves），没有候选股")
    holdings, frame, size, score, chosen, ret, m = simulate(cfg, p)
    stocks, defense = p["stocks"], p["defense"]
    k = strategy.max_holdings(p)
    full_stock = p["stock_share"] / k
    full_def = max(holdings.loc[CHART_START:, defense].max().max(), (1 - p["stock_share"]) / len(defense))
    last = score.index[-1]
    rank = score.loc[last, stocks].rank(ascending=False, method="first")
    IMG_DIR.mkdir(parents=True, exist_ok=True)

    rows = {}
    for sym in stocks + defense:
        ohlc = data.load_etf(sym).loc[CHART_START:, ["open", "high", "low", "close"]]
        w = holdings[sym].reindex(ohlc.index).ffill().fillna(0.0)
        plot(sym, ohlc, w, full_stock if sym in stocks else full_def, IMG_DIR / f"{sym}.png")
        held = w > HELD
        rows[sym] = {"held": held.mean(), "avg": w[held].mean() if held.any() else 0.0,
                     "entries": int((held & ~held.shift(1, fill_value=False)).sum()), "now": w.iloc[-1]}
        print(f"{sym}: {ohlc.index[0]:%Y-%m-%d} ~ {ohlc.index[-1]:%Y-%m-%d}，持有 {held.mean():.0%} 的交易日")

    order = sorted(stocks, key=lambda s: rank[s])
    lines = [
        "# 候选股票池", "",
        f"> 由 `python candidate_charts.py` 自动生成（{datetime.now():%Y-%m-%d %H:%M}），数据截至 {last:%Y-%m-%d} 收盘。"
        "改动 `config.toml` 的候选股后，先运行 `fetch_data.py` 更新数据，再运行这个脚本。",
        f"> 当前规则：{len(stocks)} 只候选股中，每天按满额比例排名，持有前 {k} 只（已持有的跌出前 "
        f"{k + strategy.rank_buffer(p)} 名才换），每只满额 {full_stock:.0%}；防守资产 {'、'.join(defense)}。"
        "规则和原理见 [防守策略研究报告](防守策略研究报告.md)。", "",
        "## 怎么看这些图", "",
        f"- 每个候选股和防守资产各一张日 K 线图，从 {CHART_START} 开始。虚线是 2025-04-02 美国宣布“解放日”关税。",
        "- **K 线**：绿色 = 当天上涨，红色 = 当天下跌（美股习惯，和 A 股的红涨绿跌相反）。价格是复权价（按分红和拆股调整），"
        "和行情软件显示的报价会略有差别。",
        f"- **蓝色阴影** = 按当前规则持有的日子，颜色越深（越不透明）仓位越大。股票以满额 {full_stock:.0%} 为最深，"
        f"防守资产以 {full_def:.0%} 为最深。",
        "- **下方小图** = 每天收盘后的持仓占比（占总资产的百分比），虚线是满额。",
        "- 持仓来自回测：用当前规则从 2004 年开始逐日模拟，和模拟盘使用同一套函数。模拟账户 2026-10 才开始运行，"
        "所以阴影表示的是“如果一直按当前规则运行，会在什么时候持有、持有多少”。", "",
        f"## 当前状态（{last:%Y-%m-%d} 收盘）", "",
        f"| 排名 | 代码 | 名称 | 行业 | 满额比例 | 今天的目标仓位 | {CHART_START[:7]} 以来持有的交易日 | 持有期间平均仓位 | 买入次数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in order:
        name, sector, _ = describe(s)
        mark = "**入选**" if chosen.loc[last, s] > 0 else ""
        lines.append(f"| {int(rank[s])} {mark} | {s} | {name} | {sector} | {size.loc[last, s]:.0%} | {frame.loc[last, s]:.1%} | "
                     f"{rows[s]['held']:.0%} | {rows[s]['avg']:.1%} | {rows[s]['entries']} |")
    for d in defense:
        name, sector, _ = describe(d)
        trend = "趋势向上" if frame.loc[last, d] > 0 else "趋势向下（持有现金）"
        lines.append(f"| — | {d} | {name} | {sector} | {trend} | {frame.loc[last, d]:.1%} | "
                     f"{rows[d]['held']:.0%} | {rows[d]['avg']:.1%} | {rows[d]['entries']} |")
    lines += ["", "“满额比例”= 趋势信号 × min(1, 20% ÷ 最近 20 日波动)；“今天的目标仓位”= 满额 × 满额比例（只有入选的股票才有）。"
              "目标和实际持仓相差不到 3 个百分点时不调仓，所以图中的实际仓位和目标会略有差别。", ""]

    lines += ["## 候选股（按今天的排名）", ""]
    for s in order:
        name, sector, intro = describe(s)
        lines += [f"### {int(rank[s])}. {s} {name}（{sector}）", "", intro, "", f"![{s}](candidate_charts/{s}.png)", ""]
    lines += ["## 防守资产", ""]
    for d in defense:
        name, sector, intro = describe(d)
        lines += [f"### {d} {name}", "", intro, "", f"![{d}](candidate_charts/{d}.png)", ""]
    st = plot_vs_spy(ret, holdings, stocks, defense, p["stock_share"], IMG_DIR / "_strategy_vs_SPY.png")
    mix = "、".join(f"{'股票' if k == 'Stocks' else '现金' if k == 'Cash' else k} {v:.0%}" for k, v in st["mix"].items())
    lines += ["## 本方案与 SPY 比较", "",
              f"同一时间段（{CHART_START} 起），起点都是 100：",
              "- **本方案**：按当前规则回测的净值；",
              f"- **等额买入持有**：本金 {len(stocks) + len(defense)} 等分，在起点买入上面全部候选股和防守资产，之后不再调整；",
              "- **SPY 一直持有**。", "",
              f"上图背景的蓝色阴影是本方案的股票总仓位（越深越高，最深 = 个股份额上限 {p['stock_share']:.0%}）；下图是本方案每天的资产构成。", "",
              "| | 区间收益 | 年化 | 最大回撤 |", "|---|---|---|---|",
              f"| 本方案 | {st['plan']['total']:+.1%} | {st['plan']['cagr']:+.1%} | {st['plan']['mdd']:.1%} |",
              f"| 全部 {len(stocks) + len(defense)} 个标的等额买入持有 | {st['ew']['total']:+.1%} | {st['ew']['cagr']:+.1%} | {st['ew']['mdd']:.1%} |",
              f"| SPY 一直持有 | {st['spy']['total']:+.1%} | {st['spy']['cagr']:+.1%} | {st['spy']['mdd']:.1%} |", "",
              f"期间平均资产构成：{mix}。", "",
              "![本方案与 SPY 比较](candidate_charts/_strategy_vs_SPY.png)", ""]
    lt = plot_long(m, ret, holdings, stocks, defense, k, cfg["backtest"]["cost_bps"], IMG_DIR / "_long_term.png")
    names = {"plan": "本方案", "ew": f"全部 {len(stocks) + len(defense)} 个标的等分持有", "spy": "SPY 一直持有"}
    cols = [c for c in CRISIS_LABELS if not np.isnan(lt["crises"]["plan"][c])]
    mix = "、".join(f"{'股票' if k_ == 'Stocks' else '现金' if k_ == 'Cash' else k_} {v:.0%}" for k_, v in lt["mix"].items())
    lines += ["## 长期比较（含 2008、2022 年等熊市）", "",
              f"{lt['start']:%Y-%m-%d} 起（受黄金 ETF 上市时间和 300 日均线预热限制），起点都是 100。"
              "上图是净值（对数刻度：同样的涨跌幅在图上高度相同），粉色区块是历次危机；"
              "中图是回撤（离前期最高点跌了多少，虚线是 −20%）；下图是本方案每天的资产构成。", "",
              f"“等分持有”在这里每当偏离超过 5% 就调回等分，已经上市的标的才参与（拼多多 2018 年、百胜中国 2016 年上市）。"
              "候选股是现在选定的，长期结果含有后见之明（例如英伟达）。", "",
              "| | 年化 | 最大回撤 | 夏普 | " + " | ".join(c[:4] for c in cols) + " |",
              "|---|---|---|---|" + "---|" * len(cols)]
    for key, label in names.items():
        sm = lt["summary"][key]
        lines.append(f"| {label} | {sm['年化收益']:.1%} | {sm['最大回撤']:.1%} | {sm['夏普']:.2f} | "
                     + " | ".join(f"{lt['crises'][key][c]:+.1%}" for c in cols) + " |")
    lines += ["", "危机列是各次大跌期间（从高点到低点）的累计收益。" f"本方案期间平均资产构成：{mix}。", "",
              "![长期比较](candidate_charts/_long_term.png)", ""]
    # 删除已经不在候选股里的旧图
    for f in IMG_DIR.glob("*.png"):
        if f.stem not in stocks + defense and not f.stem.startswith("_"):
            f.unlink()
    DOC.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n→ {DOC}")


if __name__ == "__main__":
    main()
