"""窗口右上角的美东时钟：不管本机时区，都显示美东时间；夏令时 / 冬令时切换前后都正确。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import helpers  # noqa: F401  （把项目根目录加入 sys.path）
from autotrader.gui.app import eastern_clock

UTC = timezone.utc


def text(y, mo, d, h, mi, s, tz=UTC) -> str:
    return eastern_clock(datetime(y, mo, d, h, mi, s, tzinfo=tz))[0]


class TestEasternClock(unittest.TestCase):
    def test_winter_is_utc_minus_5(self):
        self.assertEqual(text(2026, 11, 10, 18, 30, 0), "美东 13:30:00")
        self.assertIn("EST", eastern_clock(datetime(2026, 11, 10, 18, 30, tzinfo=UTC))[1])

    def test_summer_is_utc_minus_4(self):
        self.assertEqual(text(2026, 7, 1, 13, 30, 0), "美东 09:30:00")  # 夏天的开盘时刻
        self.assertIn("EDT", eastern_clock(datetime(2026, 7, 1, 13, 30, tzinfo=UTC))[1])

    def test_spring_forward(self):
        # 2026-03-08（3 月第二个星期日）美东凌晨 2:00 直接跳到 3:00
        self.assertEqual(text(2026, 3, 8, 6, 59, 59), "美东 01:59:59")
        self.assertEqual(text(2026, 3, 8, 7, 0, 0), "美东 03:00:00")

    def test_fall_back(self):
        # 2026-11-01（11 月第一个星期日）美东凌晨 2:00 回到 1:00，1 点钟这一小时出现两次
        self.assertEqual(text(2026, 11, 1, 5, 59, 59), "美东 01:59:59")
        self.assertEqual(text(2026, 11, 1, 6, 0, 0), "美东 01:00:00")
        self.assertIn("EDT", eastern_clock(datetime(2026, 11, 1, 5, 59, 59, tzinfo=UTC))[1])
        self.assertIn("EST", eastern_clock(datetime(2026, 11, 1, 6, 0, 0, tzinfo=UTC))[1])

    def test_independent_of_local_time_zone(self):
        # 同一时刻，从北京时间、温哥华时间、UTC 换算，结果都一样
        moment = datetime(2026, 11, 10, 18, 30, 0, tzinfo=UTC)
        beijing = moment.astimezone(timezone(timedelta(hours=8)))
        vancouver = moment.astimezone(timezone(timedelta(hours=-7)))
        self.assertEqual({eastern_clock(t)[0] for t in (moment, beijing, vancouver)}, {"美东 13:30:00"})

    def test_market_close_matches_run_py(self):
        # run.py 判断收盘用的也是 America/New_York：16:00 美东，冬天是 21:00 UTC，夏天是 20:00 UTC
        self.assertEqual(text(2026, 12, 1, 21, 0, 0), "美东 16:00:00")
        self.assertEqual(text(2026, 6, 1, 20, 0, 0), "美东 16:00:00")


if __name__ == "__main__":
    unittest.main()
