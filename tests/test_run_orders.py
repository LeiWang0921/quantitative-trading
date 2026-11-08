"""run.py 的下单流程：目标股数、再平衡阈值、先卖后买、现金限制、不重复下单、风控、不做空、实盘保护。

用假券商（tests/helpers.py），目标权重直接指定，不连盈透、不写 data/ 里的真实记录。
"""
from __future__ import annotations

import logging
import unittest
from unittest import mock

from helpers import FakeBroker, make_cfg

import run

logging.getLogger("autotrader").addHandler(logging.NullHandler())
logging.getLogger("autotrader").propagate = False

PRICES = {"AAA": 100.0, "BBB": 50.0, "IEF": 95.0, "SPY": 600.0}


class RunTestCase(unittest.TestCase):
    def go(self, broker: FakeBroker, weights: dict[str, float], cfg: dict | None = None, trade: bool = True) -> dict:
        """用指定的目标权重运行一次 run.run()，返回写给窗口程序的决策记录。"""
        cfg = cfg or make_cfg()
        saved = {}
        with mock.patch.object(run, "make_broker", return_value=broker), \
                mock.patch.object(run.strategy, "target_weights", return_value=(dict(weights), {})), \
                mock.patch.object(run, "log_signal"), \
                mock.patch.object(run.records, "append_nav"), \
                mock.patch.object(run.records, "save_last_run", side_effect=lambda r: saved.update(r)):
            run.run(cfg, trade=trade)
        self.assertFalse(broker.connected, "运行结束后应断开连接")
        return saved


class TestTargets(RunTestCase):
    def test_initial_buy_matches_target_shares(self):
        broker = FakeBroker(PRICES, cash=100_000)
        self.go(broker, {"AAA": 0.5, "BBB": 0.3, "IEF": 0.2})
        # 目标股数 = int(权重 × 可投资金额 ÷ 价格)；IEF：20000 / 95 = 210.5 → 210
        self.assertEqual(dict(broker.orders), {"AAA": 500, "BBB": 600, "IEF": 210})

    def test_cash_reserve_is_not_invested(self):
        broker = FakeBroker(PRICES, cash=100_000)
        self.go(broker, {"AAA": 1.0, "BBB": 0.0, "IEF": 0.0}, make_cfg(cash_reserve=0.02))
        self.assertEqual(broker.orders, [("AAA", 980)])

    def test_never_spends_more_than_cash(self):
        broker = FakeBroker(PRICES, cash=100_000)
        self.go(broker, {"AAA": 0.5, "BBB": 0.3, "IEF": 0.2})
        spent = sum(q * PRICES[s] for s, q in broker.orders if q > 0)
        self.assertLessEqual(spent, 100_000)


class TestRebalance(RunTestCase):
    def test_small_drift_does_not_trade(self):
        # AAA 占 52%（目标 50%），差 2 个百分点 < 3 个百分点阈值
        broker = FakeBroker(PRICES, cash=48_000, positions={"AAA": 520})
        record = self.go(broker, {"AAA": 0.5, "BBB": 0.0, "IEF": 0.0})
        self.assertEqual(broker.orders, [])
        self.assertEqual(record["orders"], [])

    def test_drift_only_trades_just_the_drifted_symbol(self):
        # AAA 60%（目标 50%，超出阈值），BBB 20%（目标 21%，在阈值内）
        broker = FakeBroker(PRICES, cash=20_000, positions={"AAA": 600, "BBB": 400})
        self.go(broker, {"AAA": 0.5, "BBB": 0.21, "IEF": 0.0})
        self.assertEqual(broker.orders, [("AAA", -100)])

    def test_sells_are_placed_before_buys(self):
        broker = FakeBroker(PRICES, cash=0, positions={"AAA": 1000})
        self.go(broker, {"AAA": 0.0, "BBB": 1.0, "IEF": 0.0})
        self.assertEqual(broker.orders[0], ("AAA", -1000))

    def test_buy_waits_for_sell_proceeds(self):
        # 收盘后卖单还没成交，现金是 0：买单不应超出现金（下次运行再买）
        broker = FakeBroker(PRICES, cash=0, positions={"AAA": 1000})
        with self.assertLogs("autotrader", "WARNING") as logs:
            self.go(broker, {"AAA": 0.0, "BBB": 1.0, "IEF": 0.0})
        self.assertEqual(broker.orders, [("AAA", -1000)])
        self.assertTrue(any("现金只够买" in line for line in logs.output))

    def test_pending_buys_reserve_cash(self):
        # 第一笔买单挂着时占用现金，第二笔只能用剩下的
        broker = FakeBroker(PRICES, cash=10_000)
        self.go(broker, {"AAA": 0.8, "BBB": 0.8, "IEF": 0.0})  # 权重故意超过 100%
        spent = sum(q * PRICES[s] for s, q in broker.orders)
        self.assertLessEqual(spent, 10_000)

    def test_target_zero_sells_exactly_the_position(self):
        broker = FakeBroker(PRICES, cash=50_000, positions={"AAA": 300, "BBB": 400})
        self.go(broker, {"AAA": 0.0, "BBB": 0.2, "IEF": 0.0})
        self.assertIn(("AAA", -300), broker.orders)


class TestNoDuplicates(RunTestCase):
    def test_second_run_same_day_places_nothing(self):
        # 14:45 下了单（收盘后挂着），20:00 补跑时应把挂单算进去，不再下单
        broker = FakeBroker(PRICES, cash=100_000)
        weights = {"AAA": 0.5, "BBB": 0.3, "IEF": 0.2}
        self.go(broker, weights)
        first = list(broker.orders)
        broker.connect()
        self.go(broker, weights)
        self.assertEqual(broker.orders, first)

    def test_no_trade_places_no_orders(self):
        broker = FakeBroker(PRICES, cash=100_000)
        record = self.go(broker, {"AAA": 0.5, "BBB": 0.3, "IEF": 0.2}, trade=False)
        self.assertEqual(broker.orders, [])
        self.assertEqual({o["result"] for o in record["orders"]}, {"试运行，未下单"})
        self.assertEqual(len(record["orders"]), 3)


class TestRisk(RunTestCase):
    def test_share_cap_rejects(self):
        broker = FakeBroker(PRICES, cash=100_000)
        record = self.go(broker, {"AAA": 0.0, "BBB": 1.0, "IEF": 0.0}, make_cfg(max_shares=1000))
        self.assertEqual(broker.orders, [])  # 需要 2000 股，超过 1000 股上限
        self.assertTrue(record["orders"][0]["result"].startswith("拒单："))

    def test_value_cap_rejects(self):
        broker = FakeBroker(PRICES, cash=100_000)
        self.go(broker, {"AAA": 0.6, "BBB": 0.3, "IEF": 0.0}, make_cfg(max_value=40_000))
        self.assertEqual(broker.orders, [("BBB", 600)])  # AAA 6 万被拒，BBB 3 万通过

    def test_never_sells_shares_not_held(self):
        # 早先挂了 100 股买单还没成交、持仓为 0，现在目标变成 0：
        # 计划是 -100 股，但那就成了卖空，必须拒绝（应去撤掉买单，而不是卖）
        broker = FakeBroker(PRICES, cash=100_000, pending={"AAA": 100})
        record = self.go(broker, {"AAA": 0.0, "BBB": 0.0, "IEF": 0.0})
        self.assertEqual(broker.orders, [])
        self.assertIn("不做空", record["orders"][0]["result"])

    def test_pending_sell_counts_against_position(self):
        # 持仓 100、已挂卖单 100：不能再卖
        cfg = make_cfg()
        self.assertIsNotNone(run.check_order(cfg, -1, 100.0, sellable=100 + -100))
        self.assertIsNone(run.check_order(cfg, -100, 100.0, sellable=100))
        self.assertIsNotNone(run.check_order(cfg, -101, 100.0, sellable=100))
        self.assertIsNotNone(run.check_order(cfg, 0, 100.0, sellable=100))


class TestSafety(RunTestCase):
    def test_live_account_in_paper_mode_aborts(self):
        broker = FakeBroker(PRICES, cash=100_000, is_live=True)
        with self.assertRaises(SystemExit):
            self.go(broker, {"AAA": 0.5, "BBB": 0.3, "IEF": 0.2})
        self.assertEqual(broker.orders, [])

    def test_exit_symbols_are_liquidated(self):
        broker = FakeBroker(PRICES, cash=10_000, positions={"SPY": 50, "AAA": 100})
        self.go(broker, {"AAA": 0.25, "BBB": 0.0, "IEF": 0.0}, make_cfg(exit_symbols=["SPY"]))
        self.assertIn(("SPY", -50), broker.orders)

    def test_exit_symbol_not_held_is_ignored(self):
        broker = FakeBroker(PRICES, cash=100_000)
        self.go(broker, {"AAA": 0.5, "BBB": 0.3, "IEF": 0.2}, make_cfg(exit_symbols=["SPY"]))
        self.assertNotIn("SPY", [s for s, _ in broker.orders])


if __name__ == "__main__":
    unittest.main()
