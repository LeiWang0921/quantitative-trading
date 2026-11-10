"""窗口程序修改 config.toml（加入 / 删除候选股）：在临时文件上测试，不动真实的 config.toml。"""
from __future__ import annotations

import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT

from autotrader.gui import config_edit


class TestConfigEdit(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "config.toml"
        shutil.copyfile(ROOT / "config.toml", self.cfg)
        self.original = self.cfg.read_text(encoding="utf-8")
        self.patch = mock.patch.object(config_edit, "CONFIG", self.cfg)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self) -> dict:
        return tomllib.loads(self.cfg.read_text(encoding="utf-8"))

    def stocks(self) -> list[str]:
        return self.load()["strategy"]["sleeves"]["stocks"]

    def test_remove_candidate(self):
        before = self.stocks()
        victim = before[0]
        config_edit.remove_candidate(victim)
        self.assertEqual(self.stocks(), before[1:])
        self.assertIn(victim, self.load()["strategy"]["exit_symbols"])  # 还持有的话，下次运行会卖出
        self.assertTrue((self.dir / "config.toml.bak").exists())

    def test_remove_keeps_everything_else(self):
        victim = self.stocks()[-1]
        config_edit.remove_candidate(victim)
        old, new = self.original.splitlines(), self.cfg.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(old), len(new))
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(changed), 2, changed)  # 只改了 stocks 和 exit_symbols 两行，注释都在
        self.assertTrue(changed[0][1].startswith("exit_symbols = ["))
        self.assertTrue(changed[1][1].startswith("stocks = ["))

    def test_remove_twice_does_not_duplicate_exit(self):
        victim = self.stocks()[0]
        config_edit.remove_candidate(victim)
        config_edit.add_candidate(victim)
        self.assertNotIn(victim, self.load()["strategy"]["exit_symbols"])  # 加回来就不再清仓
        config_edit.remove_candidate(victim)
        self.assertEqual(self.load()["strategy"]["exit_symbols"].count(victim), 1)

    def test_remove_unknown_or_last_fails_without_change(self):
        with self.assertRaises(ValueError):
            config_edit.remove_candidate("NOT_A_STOCK")
        for s in self.stocks()[:-1]:
            config_edit.remove_candidate(s)
        last = self.stocks()
        self.assertEqual(len(last), 1)
        text = self.cfg.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            config_edit.remove_candidate(last[0])
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), text)

    def test_add_candidate(self):
        config_edit.add_candidate("MSFT")
        self.assertEqual(self.stocks()[-1], "MSFT")
        with self.assertRaises(ValueError):
            config_edit.add_candidate("MSFT")


if __name__ == "__main__":
    unittest.main()
