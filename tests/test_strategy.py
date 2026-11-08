"""策略计算（autotrader/strategy.py）和配置的基本性质：用当前 config.toml 的参数 + 人造价格。"""
from __future__ import annotations

import copy
import unittest

import numpy as np
import pandas as pd

import helpers  # noqa: F401  （把项目根目录加入 sys.path）
from autotrader import strategy
from autotrader.config import ibkr_port, load_config


def synthetic_closes(params: dict, up: list[str], days: int = 900, seed: int = 0) -> pd.DataFrame:
    """up 里的标的稳定上涨，其余稳定下跌；带少量随机波动。
    趋势要明显大于波动（每天 ±0.2%，波动 0.5%），否则随机游走的“下跌股”最后一段可能正好在均线之上，
    策略给它仓位反而是对的（第一版测试数据就是这样误报的）。"""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end="2026-10-30", periods=days)
    cols = {}
    for s in strategy.data_symbols(params):
        drift = 0.002 if s in up else -0.002
        cols[s] = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.005, days)))
    return pd.DataFrame(cols, index=index)


class TestSleeves(unittest.TestCase):
    def setUp(self):
        self.params = strategy.load(load_config())
        self.assertEqual(self.params["kind"], "sleeves")
        self.stocks = self.params["stocks"]

    def weights(self, up: list[str]) -> tuple[dict, dict]:
        return strategy.target_weights(synthetic_closes(self.params, up), self.params)

    def test_weights_are_valid(self):
        w, _ = self.weights(self.stocks[:9] + ["IEF", "SPY"])
        self.assertEqual(set(w), set(strategy.symbols(self.params)))
        self.assertTrue(all(v >= 0 for v in w.values()), w)
        self.assertLessEqual(sum(w.values()), 1 + 1e-9)

    def test_holds_at_most_max_holdings(self):
        w, info = self.weights(self.stocks + ["IEF", "GLD", "SPY"])  # 全部上涨
        k = strategy.max_holdings(self.params)
        self.assertLessEqual(sum(1 for s in self.stocks if w[s] > 0), k)
        self.assertEqual(sum(info["assets"][s]["chosen"] for s in self.stocks), k)
        cap = self.params["stock_share"] / k
        self.assertTrue(all(w[s] <= cap + 1e-9 for s in self.stocks), "单只个股不应超过满额")

    def test_downtrend_stocks_get_nothing(self):
        up = self.stocks[:3]
        w, _ = self.weights(up + ["IEF", "GLD", "SPY"])
        for s in self.stocks[3:]:
            self.assertEqual(w[s], 0.0, s)
        for s in up:
            self.assertGreater(w[s], 0.0, s)

    def test_defense_below_its_average_is_cash(self):
        w, _ = self.weights(self.stocks + ["IEF", "SPY"])  # GLD 下跌
        self.assertEqual(w["GLD"], 0.0)
        self.assertGreater(w["IEF"], 0.0)

    def test_everything_falling_means_mostly_cash(self):
        w, _ = self.weights([])
        self.assertEqual(sum(w.values()), 0.0)

    def test_not_enough_history_raises(self):
        closes = synthetic_closes(self.params, self.stocks, days=200)
        with self.assertRaises(ValueError):
            strategy.target_weights(closes, self.params)


class TestParams(unittest.TestCase):
    def test_rank_buffer(self):
        p = {"stocks": list("ABCDEFGHIJKL"), "max_holdings": 7, "rank_buffer": "auto"}
        self.assertEqual(strategy.max_holdings(p), 7)
        self.assertEqual(strategy.rank_buffer(p), 3)          # 12 // 4
        p["stocks"] = list("ABCDEFGH")                        # 只比持有数多 1 只：缓冲只能是 0
        self.assertEqual(strategy.rank_buffer(p), 0)
        p["stocks"] = list("ABCDE")                           # 候选少于 7 只：全部持有
        self.assertEqual(strategy.max_holdings(p), 5)

    def test_exit_symbols_skip_current_strategy(self):
        p = {"kind": "sleeves", "stocks": ["AAPL"], "defense": ["IEF"], "exit_symbols": ["SPY", "AAPL"]}
        self.assertEqual(strategy.exit_symbols(p), ["SPY"])


class TestConfigSafety(unittest.TestCase):
    """当前 config.toml 本身的安全检查：改配置时如果误开了实盘，这里会报出来。"""

    def test_current_config_is_not_live(self):
        cfg = load_config()
        self.assertFalse(cfg["risk"]["allow_live"], "allow_live 应保持 false")
        self.assertNotEqual(cfg["mode"], "live")

    def test_live_mode_refused_without_allow_live(self):
        cfg = copy.deepcopy(load_config())
        cfg["mode"], cfg["risk"]["allow_live"] = "live", False
        with self.assertRaises(SystemExit):
            ibkr_port(cfg)
        cfg["mode"] = "paper"
        self.assertEqual(ibkr_port(cfg), cfg["ibkr"]["paper_port"])

    def test_order_caps_are_set(self):
        risk = load_config()["risk"]
        self.assertGreater(risk["max_shares_per_order"], 0)
        self.assertGreater(risk["max_order_value_usd"], 0)


if __name__ == "__main__":
    unittest.main()
