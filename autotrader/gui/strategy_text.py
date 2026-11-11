"""“策略说明”页的文字。数字都从 config.toml 读取，改参数后说明会自动跟着变。原理和研究过程见 docs/防守策略研究报告.md。"""
from __future__ import annotations

from .. import strategy


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def markdown(cfg: dict) -> str:
    p = strategy.load(cfg)
    mode = {"paper": "模拟盘", "live": "实盘", "dryrun": "本地假券商"}.get(cfg["mode"], cfg["mode"])
    if p["kind"] != "sleeves":
        return _spy_ief(p, mode)
    n, k = len(p["stocks"]), strategy.max_holdings(p)
    buf = strategy.rank_buffer(p)
    each = p["stock_share"] / k
    windows = "、".join(str(w) for w in p["trend_windows"])
    weights = p.get("trend_weights") or [1 / len(p["trend_windows"])] * len(p["trend_windows"])
    weight_text = "、".join(f"{w} 日 {_pct(x)}" for w, x in zip(p["trend_windows"], weights))
    defense = "、".join(p["defense"])
    def_each = (1 - p["stock_share"]) / len(p["defense"])
    dip = p.get("dip_threshold")
    risk = cfg["risk"]

    text = f"""# 当前策略：个股为主 + 美债/黄金防守

运行环境：**{mode}**。每个交易日由计划任务运行两次 run.py（14:45 和 20:00，第二次是补跑，不会重复下单）。
策略只用收盘价计算，订单是市价单，在下一个交易日开盘成交。

## 1. 资金怎么分

| 部分 | 占比 | 说明 |
|---|---|---|
| 个股 | {_pct(p['stock_share'])} | 从 {n} 只候选股里持有排名前 {k} 只，每只满额 {each * 100:.0f}% |
| 防守资产 | {_pct(1 - p['stock_share'])} | {defense} 各 {def_each * 100:.0f}%，再加上个股没用完的钱 |
| 预留现金 | {_pct(p['cash_reserve'])} | 不投，应对开盘跳空和手续费 |

“100%”指美元现金加上策略里各标的的持仓市值。账户里的加元不参与策略。

## 2. 个股怎么打分

每只候选股每天算一个**满额比例**（0 ~ 100%）：

**满额比例 = 趋势信号 × 波动调整**

- **趋势信号**：价格在 {windows} 日五条均线上方的加权比例，权重为 {weight_text}。
  每条均线带 {_pct(p['trend_band'])} 缓冲：涨过均线 {_pct(p['trend_band'])} 才算在上方，跌破 {_pct(p['trend_band'])} 才算在下方，避免在均线附近来回切换。
- **波动调整**：min(1, {_pct(p['vol_target'])} ÷ 最近 {p['vol_lookback']} 个交易日的年化波动)。波动越大，持仓越小。

## 3. 选哪几只

- 候选股按满额比例排名，持有前 {k} 只；每只的目标仓位 = 满额 {each * 100:.0f}% × 满额比例。
- 已经持有的股票，只要还在前 {k + buf} 名就继续持有（排名缓冲 {buf} 名），避免在第 {k} 名附近来回换。
- 满额比例降到 0 的股票（目标仓位为 0）不受缓冲保护，马上让出名额给排在后面、满额比例大于 0 的股票；满额比例为 0 的股票也不会被选入。
- 候选池可以随时增加股票。新股票在下一次运行时参与排名，持有数量不变，每只的满额不会被摊薄。

## 4. 防守资产

- {defense} 只有在价格高于自己的 {p['defense_trend_window']} 日均线时才持有，否则那一份留作现金。"""
    if p.get("defense_vol_target"):
        text += f"""
- 也按波动缩放：min(1, {_pct(p['defense_vol_target'])} ÷ 最近 {p['vol_lookback']} 日年化波动)，缩下来的部分留作现金。"""
    if dip:
        text += f"""

## 5. 逢跌加仓

当 {p.get('dip_market', 'SPY')} 仍在 300 日均线之上（长期牛市），但比最近 63 个交易日的高点跌超 {_pct(dip)} 时，
仍在自己 300 日均线之上的股票按满额持有，不因短期下跌和波动升高而减仓（押注牛市中的急跌会较快反弹）。"""
        if p.get("dip_min_days"):
            text += f"""
- 触发后至少维持 {p['dip_min_days']} 个交易日（从最后一次满足条件那天算起），即使大盘已经反弹回阈值以内，
  避免反弹刚开始就减仓；{p.get('dip_market', 'SPY')} 跌破 300 日均线（判断为熊市）时立即结束。
- 单只股票跌破自己的 300 日均线时，这只股票随时恢复按正常规则计算。"""
    text += f"""

## {6 if dip else 5}. 什么时候交易

- 只调整实际占比和目标相差超过 **{p['rebalance_band'] * 100:.0f} 个百分点**的标的，其余不动，减少交易次数和手续费。
- 先卖后买；买入不超过可用的美元现金（现金账户不能借钱）。

## {7 if dip else 6}. 安全措施

- 单笔订单最多 {risk['max_shares_per_order']:,} 股、{risk['max_order_value_usd']:,} 美元。
- 实盘需要同时设置 mode = "live" 和 allow_live = true（当前 allow_live = {str(risk['allow_live']).lower()}）。
- 配置是模拟盘却连到了实盘账户时，立即停止，不下任何单。
- 这个窗口程序用只读方式连接盈透，不会下单。
"""
    return text


def _spy_ief(p: dict, mode: str) -> str:
    return f"""# 当前策略：SPY + 美债

运行环境：**{mode}**。

- SPY 仓位 = 趋势信号 × min(1, {_pct(p['vol_target'])} ÷ SPY 最近 {p['vol_lookback']} 日波动)。
- 剩余资金在 {p['bond']} 高于 {p['bond_trend_window']} 日均线时持有 {p['bond']}，否则留作现金。
- 经济过滤：{p['economy_filter']}。
- 任何一个标的偏离目标超过 {p['rebalance_band'] * 100:.0f} 个百分点，就全部调回目标。
"""
