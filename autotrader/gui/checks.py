"""运行环境检查：在一台新电脑上要跑这个项目，除了账户之外还需要哪些软件和设置。

每项检查返回 Check(名称, 状态, 说明, 解决办法)。状态：True = 满足，False = 不满足，None = 提醒（不影响运行）。
"""
from __future__ import annotations

import importlib.metadata
import re
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..config import DATA_DIR, LOGS_DIR, ROOT

TASK_NAME = "QuantTrading-DailyRun"
GATEWAY_GLOB = "Jts/ibgateway/*/ibgateway.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 用 pythonw 启动时，调用命令行工具不弹黑窗口


@dataclass
class Check:
    name: str
    ok: bool | None
    detail: str
    fix: str = ""


def _run(cmd: list[str], timeout: float = 15) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return out.stdout.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def gateway_installs() -> list[Path]:
    return sorted(Path("C:/").glob(GATEWAY_GLOB))


def gateway_running() -> bool:
    return "ibgateway.exe" in _run(["tasklist", "/FI", "IMAGENAME eq ibgateway.exe", "/NH"]).lower()


def scheduled_task() -> dict | None:
    """计划任务的状态；不存在返回 None。"""
    ps = (f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop; $i = $t | Get-ScheduledTaskInfo; "
          "'{0}|{1:yyyy-MM-dd HH:mm}|{2}|{3:yyyy-MM-dd HH:mm}' -f $t.State, $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime")
    out = _run(["powershell", "-NoProfile", "-Command", ps])
    parts = out.split("|")
    if len(parts) != 4:
        return None
    state, last, result, nxt = parts
    return {"state": state, "last_run": last, "last_result": int(result) if result.lstrip("-").isdigit() else None,
            "next_run": nxt}


def last_scheduled_run() -> dict | None:
    """logs/scheduled_run.log 里最近一次自动运行：时间、是否失败、失败时的最后几行输出。"""
    try:
        lines = (LOGS_DIR / "scheduled_run.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    starts = [i for i, line in enumerate(lines) if line.startswith("===== ")]
    if not starts:
        return None
    block = lines[starts[-1]:]
    m = re.match(r"===== (\S+ \S+)", block[0])
    failed = any("运行失败" in line for line in block)
    return {"time": m.group(1) if m else "?", "failed": failed, "args": block[0].split("run.py", 1)[-1].strip(),
            "tail": [line for line in block[1:] if line.strip()][-5:]}


def _requirements() -> list[str]:
    names = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.split(r"[<>=!~\[ ]", line, 1)[0])
    return names


def run_all(cfg: dict | None) -> list[Check]:
    checks: list[Check] = []
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"

    # Python 和虚拟环境
    v = sys.version_info
    checks.append(Check("Python", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}（{sys.executable}）",
                        "需要 Python 3.11 或更新版本：从 python.org 下载安装，安装时勾选 Add python.exe to PATH"))
    cfg_file = ROOT / ".venv" / "pyvenv.cfg"
    home = ""
    if cfg_file.exists():
        m = re.search(r"^home\s*=\s*(.+)$", cfg_file.read_text(encoding="utf-8"), re.M)
        home = m.group(1).strip() if m else ""
    venv_ok = venv_python.exists() and bool(home) and Path(home).exists()
    checks.append(Check("虚拟环境 .venv", venv_ok,
                        f"基于 {home}" if venv_ok else "不存在，或创建它的 Python 已被卸载",
                        "运行 tools\\setup.bat（会创建 .venv 并安装依赖包）"))
    in_venv = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    if not in_venv:
        checks.append(Check("窗口程序的 Python", None, f"当前不是用项目的 .venv 启动的（{sys.prefix}）",
                            "用 start_gui.bat 启动，下面的依赖包检查才准确"))

    # 依赖包
    missing, found = [], []
    for name in _requirements():
        try:
            found.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    checks.append(Check("依赖包（requirements.txt）", not missing,
                        ("缺少：" + "、".join(missing)) if missing else "、".join(found),
                        ".venv\\Scripts\\python -m pip install -r requirements.txt"))

    # 配置文件
    if cfg is None:
        try:
            cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
        except Exception as e:
            checks.append(Check("config.toml", False, f"读取失败：{e}", "检查 config.toml 的格式（引号、方括号是否成对）"))
    if cfg is not None:
        mode = cfg.get("mode")
        detail = {"paper": "模拟盘", "live": "实盘", "dryrun": "本地假券商"}.get(mode, f"未知模式 {mode}")
        ok = mode in ("paper", "dryrun") or (mode == "live" and cfg["risk"]["allow_live"])
        checks.append(Check("config.toml", ok, f"mode = {mode}（{detail}）",
                            "mode = live 时还需要把 risk.allow_live 改成 true"))

    # IB Gateway
    installs = gateway_installs()
    checks.append(Check("IB Gateway 已安装", bool(installs),
                        "、".join(str(p.parent) for p in installs) if installs else "没有找到 C:\\Jts\\ibgateway",
                        "从 interactivebrokers.com 下载 IB Gateway（Stable 版）并安装"))
    running = gateway_running()
    checks.append(Check("IB Gateway 正在运行", running, "进程 ibgateway.exe 在运行" if running else "没有运行",
                        "打开 IB Gateway，选 IB API，用模拟账户登录"))
    if cfg is not None and cfg.get("mode") in ("paper", "live"):
        host = cfg["ibkr"]["host"]
        port = cfg["ibkr"]["live_port" if cfg["mode"] == "live" else "paper_port"]
        ok = port_open(host, port)
        checks.append(Check("API 端口", ok, f"{host}:{port} {'可以连接' if ok else '连不上'}",
                            "Gateway 里 配置 → 设置 → API → 设置：端口填 "
                            f"{port}，受信任的 IP 加 127.0.0.1，取消勾选只读 API"))

    # 计划任务
    task = scheduled_task()
    if task is None:
        checks.append(Check("计划任务 " + TASK_NAME, False, "不存在：不会每天自动运行 run.py",
                            "在任务计划程序里新建任务：每周一到五 14:45 和 20:00 运行 tools\\daily_run.bat"))
    else:
        ok = task["state"] in ("Ready", "Running")
        checks.append(Check("计划任务 " + TASK_NAME, ok,
                            f"状态 {task['state']}，下次运行 {task['next_run']}，上次 {task['last_run']}"
                            f"（结果 {task['last_result']}）",
                            "在任务计划程序里启用这个任务"))

    # 数据目录
    try:
        DATA_DIR.mkdir(exist_ok=True)
        probe = DATA_DIR / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        checks.append(Check("data 目录可写", True, str(DATA_DIR)))
    except OSError as e:
        checks.append(Check("data 目录可写", False, str(e), "不要把项目放在 C:\\Program Files 这类受保护的目录"))
    return checks
