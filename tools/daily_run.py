"""每个交易日收盘后由 Windows 计划任务“QuantTrading-DailyRun”调用（14:45 一次，20:00 再补一次，经 tools\\daily_run.bat）。

做这几件事：
  1. 在 logs\\scheduled_run.log 里写一行时间
  2. 运行 run.py，把它的输出（包括报错）追加到同一个日志
  3. run.py 失败时弹出提醒（最常见的原因是 IB Gateway 没有登录），并发邮件（config.toml 的 [notify]）
  4. 有订单被风控拒绝时发邮件；之前失败、这次补跑成功时也发一封，告诉你已经恢复

同一天运行两次不会重复下单（run.py 会把已经提交的挂单算进去）。
为什么不直接写在 .bat 里：cmd 读含中文的 .bat 文件时会把行读乱（2026-10-21 起的自动运行因此全部失败），
所以 .bat 只保留英文，中文提示放在这里。

手动测试（不下单）：tools\\daily_run.bat --no-trade
"""
import datetime
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from autotrader import notify  # noqa: E402

LOG = ROOT / "logs" / "scheduled_run.log"
STATE = ROOT / "data" / "notify_state.json"  # 记住上次是否失败，补跑成功时发“已恢复”
MESSAGE = ("量化交易：今天的 run.py 运行失败（最常见的原因是 IB Gateway 没有登录）。"
           "请登录 Gateway 的模拟账户，晚上 20:00 会自动补跑一次，也可以手动运行 run.py。"
           "详情见 logs\\scheduled_run.log")
TAIL = 40  # 邮件里附上这次运行输出的最后多少行


def run_once(args: list[str]) -> tuple[int, str]:
    """运行一次 run.py，输出追加到日志。返回 (退出码, 这次运行的输出)。"""
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} run.py {' '.join(args)}\n")
        log.flush()
        start = log.tell()
        code = subprocess.run([sys.executable, str(ROOT / "run.py"), *args], cwd=ROOT, stdout=log,
                              stderr=subprocess.STDOUT, env=os.environ | {"PYTHONIOENCODING": "utf-8"}).returncode
        if code != 0:
            log.write(f"run.py 运行失败（退出码 {code}）\n")
    with LOG.open("rb") as f:
        f.seek(start)
        output = f.read().decode("utf-8", errors="replace")
    return code, output


def email(subject: str, lead: str, output: str) -> None:
    tail = "\n".join(output.strip().splitlines()[-TAIL:])
    ok, detail = notify.send_email(subject, f"{lead}\n\n---- 这次运行输出的最后 {TAIL} 行 ----\n{tail}")
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"邮件提醒：{detail}\n")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    code, output = run_once(sys.argv[1:])
    state = load_state()
    if code != 0:
        subprocess.run(["msg", getpass.getuser(), "/TIME:21600", MESSAGE])
        email("量化交易：运行失败", MESSAGE, output)
        save_state({"failed_at": f"{datetime.datetime.now():%Y-%m-%d %H:%M}"})
        return code
    rejected = [line for line in output.splitlines() if "拒单：" in line]
    if rejected:
        email("量化交易：有订单被拒绝", "这次运行有订单没有下出去（被程序的风控检查拒绝），请看下面的原因：\n"
              + "\n".join(rejected), output)
    if state.get("failed_at"):
        email("量化交易：已恢复", f"{state['failed_at']} 的运行失败了，这次运行已经成功。", output)
        save_state({})
    return code


if __name__ == "__main__":
    sys.exit(main())
