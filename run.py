"""运行一次：取数据 → 算目标仓位 → 换算成股数 → 风控检查 → 下单。每个交易日收盘后运行一次。

用法：
  python run.py              按 config.toml 的 mode 运行一次
  python run.py --no-trade   只打印决策，不下单
  python run.py --reset      清空 dryrun 的本地状态

策略见 autotrader/strategy.py，由 config.toml 的 strategy.kind 选择：
  "sleeves"：个股为主（每只各自按趋势和波动调整）+ 美债 / 黄金防守（当前）
  "spy_ief"：SPY + 美债（之前的方案）
收盘后运行时，市价单会在下一个交易日开盘成交，和回测的假设一致。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from autotrader import records, strategy
from autotrader.broker import Broker
from autotrader.config import DATA_DIR, LOGS_DIR, ibkr_port, load_config
from autotrader.research import economy
from autotrader.research.data import clean_prices, download_fred, load_fred

DRYRUN_STATE = DATA_DIR / "dryrun_state.json"
NEW_YORK = ZoneInfo("America/New_York")

log = logging.getLogger("autotrader")


def setup_logging() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOGS_DIR / "trader.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def make_broker(cfg: dict) -> Broker:
    mode = cfg["mode"]
    if mode == "dryrun":
        from autotrader.broker.dryrun import DryRunBroker
        return DryRunBroker(DRYRUN_STATE)
    if mode in ("paper", "live"):
        from autotrader.broker.ibkr import IBKRBroker
        ib = cfg["ibkr"]
        return IBKRBroker(ib["host"], ibkr_port(cfg), ib["client_id"], ib["what_to_show"])
    sys.exit(f"未知 mode: {mode}")


def latest_closes(broker: Broker, symbols: list[str], days: int, save: bool = False) -> pd.DataFrame:
    """复权收盘价表（行 = 日期，列 = 标的）。盘中运行时去掉当天还没走完的那根日线。
    save：顺便把下载到的日线存到 data/bars/，给窗口程序画“近 1 年 K 线”（不需要额外的盈透请求）。"""
    closes = {}
    now = datetime.now(NEW_YORK)
    unfinished = now.strftime("%Y-%m-%d") if now.time() < time(16, 15) else None  # 盘中：当天的日线还没走完
    for symbol in symbols:
        bars = broker.history(symbol, days)
        if not bars:
            raise SystemExit(f"取不到 {symbol} 的历史数据，今天不运行。最常见的原因：实盘账户正在网页版 / 手机 App / TWS "
                             f"上登录，占用了行情（盈透错误 162）。退出实盘登录后，晚上 20:00 会自动补跑，也可以手动运行 run.py")
        df = pd.DataFrame([vars(b) for b in bars])
        df.index = pd.to_datetime(df.pop("date"))
        cleaned = clean_prices(df, symbol)  # 修正偶尔漏掉的拆股复权
        closes[symbol] = cleaned["close"]
        if save:
            try:
                records.save_recent_bars(symbol, [{"date": d.strftime("%Y-%m-%d"), **row}
                                                  for d, row in cleaned.to_dict("index").items()
                                                  if d.strftime("%Y-%m-%d") != unfinished])
            except Exception as e:  # 只影响窗口程序的 K 线图，不影响交易
                log.warning("保存 %s 的日线到 data/bars/ 失败：%s", symbol, e)
    # 场外股票（如 XIACY）偶尔有一两天没有成交：沿用前一天收盘价（最多 3 天），再去掉仍有缺失的日期
    table = pd.DataFrame(closes).sort_index().ffill(limit=3).dropna()
    if table.index[-1].date() == now.date() and now.time() < time(16, 15):
        log.warning("美股还没收盘（纽约时间 %s），去掉今天未完成的日线，用昨天的收盘价算信号", now.strftime("%H:%M"))
        table = table.iloc[:-1]
    return table


def economy_signal(cfg: dict, params: dict, index: pd.DatetimeIndex) -> pd.Series | None:
    """经济过滤用的信号（1 = 经济没有转差），只有 spy_ief 策略使用。
    下载失败、数据过旧或不使用时返回 None：按趋势照常减仓。"""
    name = params.get("economy_filter", "none")
    if name == "none" or cfg["mode"] == "dryrun":  # dryrun 的价格是随机生成的，日期和真实经济数据对不上
        return None
    try:
        for series_id in economy.SOURCES[name]:
            download_fred(series_id)
    except Exception as e:  # 网络问题、FRED 维护等：改用本地上次下载的数据（下面会检查是否过旧）
        log.warning("下载经济数据失败（%s），改用本地已有的数据", str(e).strip())
    try:
        latest = load_fred(economy.SOURCES[name][0]).index[-1]
    except FileNotFoundError:
        log.warning("本地也没有经济数据，今天不使用经济过滤，按趋势照常减仓")
        return None
    age = (pd.Timestamp.now().normalize() - latest).days
    if age > economy.MAX_AGE_DAYS[name]:
        log.warning("%s 最新数据是 %s，已经 %d 天没更新，今天不使用经济过滤", economy.NAMES[name], latest.date(), age)
        return None
    good = economy.economy_good(name, index)
    if name == "claims":
        st = economy.claims_status()
        log.info("经济过滤：首次申请失业金（截至 %s 的一周）4 周平均 %.0f，52 周平均 %.0f → 经济%s",
                 st["week"].date(), st["avg4"], st["avg52"], "没有转差" if good.iloc[-1] == 1 else "在转差")
    else:
        log.info("经济过滤：%s → 经济%s", economy.NAMES[name], "没有转差" if good.iloc[-1] == 1 else "在转差")
    return good


def log_signal(params: dict, weights: dict[str, float], info: dict) -> None:
    if params["kind"] == "sleeves":
        k, n = info["holdings"], len(params["stocks"])
        log.info("信号（%s 收盘）：%d 只候选股%s", info["date"], n,
                 f"，按满额比例排名持有前 {k} 只（已持有的跌出前 {k + info['buffer']} 名才换）" if k < n else "，全部持有")
        for s in sorted(params["stocks"], key=lambda x: info["assets"][x]["rank"]):
            a = info["assets"][s]
            log.info("  %-5s 第%2d名 %s 趋势=%3.0f%% 近20日波动=%4.1f%% 满额比例=%3.0f%% → 目标 %4.1f%%（满额 %.0f%%）",
                     s, a["rank"], "入选" if a["chosen"] else "    ", a["trend"] * 100, a["vol"] * 100, a["size"] * 100,
                     weights[s] * 100, params["stock_share"] / k * 100)
        if "dip" in info:
            dp = info["dip"]
            log.info("  逢跌加仓：%s 距最近 63 日高点 %+.1f%%，%s → %s", dp["market"], dp["drawdown"] * 100,
                     "长期牛市（300 日均线之上）" if dp["bull"] else "跌破 300 日均线",
                     "触发：长期趋势完好的股票按满额持有" if dp["on"] else f"未触发（需要跌超 {dp['threshold']:.0%} 且在长期牛市中）")
        dvt = params.get("defense_vol_target")
        for d in params["defense"]:
            a = info["assets"][d]
            scale = "" if dvt is None else f" 近20日波动={a['vol'] * 100:4.1f}% 波动缩放={min(1, dvt / a['vol']) * 100:3.0f}%"
            log.info("  %-5s 趋势=%s%s → 目标 %4.1f%%", d, "向上" if a["trend"] else "向下（这份留现金）", scale,
                     weights[d] * 100)
    else:
        trend_note = "" if info["trend"] == info["raw_trend"] else f"（原始 {info['raw_trend']:.0%}，经济没有转差，不减仓）"
        log.info("信号（%s 收盘）：%s 趋势=%.0f%%%s 近20日波动=%.1f%% 波动缩放=%.0f%% | %s 趋势=%s",
                 info["date"], params["stock"], info["trend"] * 100, trend_note, info["vol"] * 100,
                 info["vol_scale"] * 100, params["bond"], "向上" if info["bond_trend"] else "向下")
    log.info("目标权重合计 %.0f%%，现金 %.0f%%", sum(weights.values()) * 100, (1 - sum(weights.values())) * 100)


def check_order(cfg: dict, qty: int, price: float, sellable: int) -> str | None:
    """风控：返回拒单原因；通过检查返回 None。
    sellable：现在能卖的股数 = 持仓 - 已挂的卖单（还没成交的买单不算，不能先卖还没买到的股票）。"""
    risk = cfg["risk"]
    if qty == 0:
        return "数量为 0"
    if qty < 0 and -qty > sellable:
        # 不做空：卖出超过持仓就是卖空（现金账户会被拒单，保证金账户则真的会做空）
        return f"卖出 {-qty} 股超过可卖持仓 {max(sellable, 0)} 股（不做空）"
    if abs(qty) > risk["max_shares_per_order"]:
        return f"数量 {abs(qty)} 超过单笔上限 {risk['max_shares_per_order']}"
    value = abs(qty) * price
    if value > risk["max_order_value_usd"]:
        return f"金额 {value:,.0f} USD 超过单笔上限 {risk['max_order_value_usd']:,}"
    return None


def run(cfg: dict, trade: bool) -> None:
    broker = make_broker(cfg)
    broker.connect()
    record = None  # 这次运行的决策记录，结束时写到 data/last_run.json 给窗口程序看
    try:
        # 端口和账户必须一致：mode 不是 live 却连到了实盘账户，立刻停止
        if broker.is_live and cfg["mode"] != "live":
            sys.exit("连接到的是实盘账户，但 mode 不是 live。已中止，未下任何单。")

        params = strategy.load(cfg)
        symbols = strategy.symbols(params)

        # 1. 信号
        closes = latest_closes(broker, strategy.data_symbols(params), strategy.history_days(params),
                               save=cfg["mode"] in ("paper", "live"))  # dryrun 的价格是随机生成的，不保存
        weights, info = strategy.target_weights(closes, params, economy_signal(cfg, params, closes.index))
        log_signal(params, weights, info)
        prices = closes.iloc[-1].to_dict()

        # 切换策略后要清掉的旧持仓：目标 0
        for s in strategy.exit_symbols(params):
            if broker.position(s) or broker.pending(s):
                prices[s] = float(latest_closes(broker, [s], 30)[s].iloc[-1])
                weights[s] = 0.0
                symbols = symbols + [s]
                log.info("  %-5s 不属于当前策略 → 目标 0%%（清仓）", s)

        # 2. 账户：满仓 100% = 美元现金 + 这些标的的持仓市值，再留 cash_reserve 不投
        positions = {s: broker.position(s) for s in symbols}
        pending = {s: broker.pending(s) for s in symbols}
        cash = broker.cash("USD")
        equity = cash + sum(positions[s] * prices[s] for s in symbols)
        investable = equity * (1 - params["cash_reserve"])
        value, ccy = broker.net_value()
        log.info("账户：模式=%s 实盘=%s 净值=%.2f %s 美元现金=%.2f 策略资产=%.2f USD",
                 cfg["mode"], broker.is_live, value, ccy, cash, equity)
        records.append_nav(cfg["mode"], value, ccy, cash, equity)
        record = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mode": cfg["mode"], "no_trade": not trade,
                  "kind": params["kind"], "signal": info, "weights": weights,
                  "prices": {s: prices[s] for s in symbols}, "positions": positions, "pending": pending,
                  "account": {"nav": value, "currency": ccy, "usd_cash": cash, "strategy_usd": equity},
                  "plan": {}, "orders": []}

        # 3. 目标股数，以及哪些标的需要交易
        band = params["rebalance_band"]
        plan, drift = {}, {}
        for s in symbols:
            target = int(weights[s] * investable / prices[s])
            held = (positions[s] + pending[s]) * prices[s] / investable  # 当前占比（含挂单）
            plan[s] = target - positions[s] - pending[s]
            drift[s] = abs(held - weights[s])
            record["plan"][s] = {"target_shares": target, "held_weight": held, "delta": plan[s]}
            log.info("%-5s 价格=%8.2f 持仓=%5d 挂单=%+5d 占比 %5.1f%% → 目标 %5.1f%%（%d 股），差 %+d 股",
                     s, prices[s], positions[s], pending[s], held * 100, weights[s] * 100, target, plan[s])
        holding = {s: positions[s] + pending[s] for s in symbols}
        if params.get("drift_only", False):
            # 只调整偏离超过阈值的标的，以及目标为 0 但还持有的标的
            to_trade = [s for s in symbols if drift[s] > band or (weights[s] == 0 and holding[s] > 0)]
        else:
            exit_all = sum(weights.values()) == 0 and any(holding.values())
            to_trade = symbols if max(drift.values()) > band or exit_all else []
        to_trade = [s for s in to_trade if plan[s] != 0]
        if not to_trade:
            log.info("各标的占比和目标最多相差 %.1f 个百分点，未超过再平衡阈值 %.0f 个百分点，今天不交易",
                     max(drift.values()) * 100, band * 100)
            return
        log.info("需要调整：%s", "、".join(to_trade))

        # 4. 先卖后买：卖单先下；买单不能超过可用现金（已挂的买单会占用现金）
        for s in sorted(to_trade, key=lambda x: plan[x]):
            qty = plan[s]
            if qty > 0:
                reserved = sum(max(pending[x], 0) * prices[x] for x in symbols)
                affordable = int((broker.cash("USD") - reserved) / prices[s])
                if qty > affordable:
                    log.warning("%s 现金只够买 %d 股（需要 %d）。卖单成交后的资金下次运行再买入", s, max(affordable, 0), qty)
                    qty = max(affordable, 0)
                if qty == 0:
                    continue
            reason = check_order(cfg, qty, prices[s], positions[s] + min(pending[s], 0))
            if reason:
                log.warning("%s 拒单：%s", s, reason)
                record["orders"].append({"symbol": s, "qty": qty, "result": f"拒单：{reason}"})
            elif not trade:
                log.info("%s 本应下单 %+d 股（--no-trade，跳过）", s, qty)
                record["orders"].append({"symbol": s, "qty": qty, "result": "试运行，未下单"})
                if qty > 0:  # 试运行时也要模拟占用现金，后面的买单才算得准
                    pending[s] += qty
            else:
                result = broker.market_order(s, qty)
                log.info("%s 下单 %+d 股 → %s", s, qty, result)
                record["orders"].append({"symbol": s, "qty": qty, "result": result})
                pending[s] = broker.pending(s)  # 重新查询：盈透收盘后的单会挂着，dryrun 则已立即成交
    finally:
        broker.disconnect()
        if record is not None:
            records.save_last_run(record)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-trade", action="store_true", help="只打印决策，不下单")
    parser.add_argument("--reset", action="store_true", help="清空 dryrun 本地状态")
    args = parser.parse_args()

    if args.reset:
        DRYRUN_STATE.unlink(missing_ok=True)
        print("dryrun 状态已清空")
        return

    setup_logging()
    run(load_config(), trade=not args.no_trade)


if __name__ == "__main__":
    main()
