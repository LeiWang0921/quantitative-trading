"""tools/daily_run.py 的提醒逻辑：失败 → 弹窗 + 邮件；拒单 → 邮件；失败后补跑成功 → “已恢复”邮件。
不真的运行 run.py、不弹窗、不发邮件。"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT

from autotrader import notify

spec = importlib.util.spec_from_file_location("daily_run", ROOT / "tools" / "daily_run.py")
daily_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily_run)


class TestDailyRun(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(daily_run, "STATE", tmp / "notify_state.json"),
                        mock.patch.object(daily_run, "LOG", tmp / "scheduled_run.log"),
                        mock.patch.object(daily_run.subprocess, "run")]  # 弹窗 msg
        for p in self.patches:
            p.start()
        self.sent = []
        self.patches.append(mock.patch.object(daily_run.notify, "send_email",
                                              side_effect=lambda s, b: self.sent.append((s, b)) or (True, "ok")))
        self.patches[-1].start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def day(self, code: int, output: str = "INFO 今天不交易\n") -> int:
        with mock.patch.object(daily_run, "run_once", return_value=(code, output)), \
                mock.patch.object(daily_run.sys, "argv", ["daily_run.py"]):
            return daily_run.main()

    def test_success_sends_nothing(self):
        self.assertEqual(self.day(0), 0)
        self.assertEqual(self.sent, [])

    def test_failure_emails_with_output_and_pops_up(self):
        self.assertEqual(self.day(1, "ERROR 连不上 IB Gateway\n"), 1)
        self.assertEqual(len(self.sent), 1)
        subject, body = self.sent[0]
        self.assertIn("运行失败", subject)
        self.assertIn("连不上 IB Gateway", body)
        daily_run.subprocess.run.assert_called_once()  # 弹窗

    def test_recovery_after_failure(self):
        self.day(1)
        self.day(0)
        self.assertEqual([s for s, _ in self.sent], ["量化交易：运行失败", "量化交易：已恢复"])
        self.day(0)  # 恢复后再成功不再发
        self.assertEqual(len(self.sent), 2)

    def test_rejected_orders_email(self):
        self.day(0, "INFO 需要调整：AAPL\nWARNING AAPL 拒单：数量 3000 超过单笔上限 2000\n")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("被拒绝", self.sent[0][0])
        self.assertIn("超过单笔上限", self.sent[0][1])


class TestNotify(unittest.TestCase):
    def test_empty_address_sends_nothing(self):
        with mock.patch.object(notify.subprocess, "run") as run:
            ok, _ = notify.send_email("x", "y", cfg={"notify": {"email": ""}})
        self.assertFalse(ok)
        run.assert_not_called()

    def test_outlook_failure_does_not_raise(self):
        with mock.patch.object(notify.subprocess, "run", side_effect=OSError("no powershell")):
            ok, detail = notify.send_email("x", "y", cfg={"notify": {"email": "a@b.c"}})
        self.assertFalse(ok)
        self.assertIn("no powershell", detail)


if __name__ == "__main__":
    unittest.main()
