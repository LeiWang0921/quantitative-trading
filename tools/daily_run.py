"""每个交易日收盘后由 Windows 计划任务“QuantTrading-DailyRun”调用（14:45 一次，20:00 再补一次，经 tools\\daily_run.bat）。

做三件事：
  1. 在 logs\\scheduled_run.log 里写一行时间
  2. 运行 run.py，把它的输出（包括报错）追加到同一个日志
  3. run.py 失败时弹出提醒（最常见的原因是 IB Gateway 没有登录）

同一天运行两次不会重复下单（run.py 会把已经提交的挂单算进去）。
为什么不直接写在 .bat 里：cmd 读含中文的 .bat 文件时会把行读乱（2026-10-21 起的自动运行因此全部失败），
所以 .bat 只保留英文，中文提示放在这里。

手动测试（不下单）：tools\\daily_run.bat --no-trade
"""
import datetime
import getpass
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "scheduled_run.log"
MESSAGE = ("量化交易：今天的 run.py 运行失败（最常见的原因是 IB Gateway 没有登录）。"
           "请登录 Gateway 的模拟账户，晚上 20:00 会自动补跑一次，也可以手动运行 run.py。"
           "详情见 logs\\scheduled_run.log")


def main() -> int:
    args = sys.argv[1:]
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} run.py {' '.join(args)}\n")
        log.flush()
        code = subprocess.run([sys.executable, str(ROOT / "run.py"), *args], cwd=ROOT, stdout=log,
                              stderr=subprocess.STDOUT, env=os.environ | {"PYTHONIOENCODING": "utf-8"}).returncode
        if code != 0:
            log.write(f"run.py 运行失败（退出码 {code}）\n")
    if code != 0:
        subprocess.run(["msg", getpass.getuser(), "/TIME:21600", MESSAGE])
    return code


if __name__ == "__main__":
    sys.exit(main())
