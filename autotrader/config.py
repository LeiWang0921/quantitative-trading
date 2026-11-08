"""读取 config.toml，以及项目里常用的文件路径。"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # 项目根目录
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"               # fetch_data.py 下载的历史日线
MACRO_DIR = DATA_DIR / "macro"                   # fetch_macro.py 下载的宏观 / 长期市场数据
RESULTS_DIR = ROOT / "results"                   # backtest.py 的输出
LOGS_DIR = ROOT / "logs"


def load_config() -> dict:
    return tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def ibkr_port(cfg: dict) -> int:
    """按 mode 选端口；mode=live 但没打开 allow_live 时报错。"""
    mode = cfg["mode"]
    if mode not in ("paper", "live"):
        raise SystemExit(f"需要 paper 或 live 模式才能连接盈透，当前 mode = {mode}")
    if mode == "live" and not cfg["risk"]["allow_live"]:
        raise SystemExit("mode = live 但 risk.allow_live = false，拒绝运行。")
    return cfg["ibkr"]["live_port"] if mode == "live" else cfg["ibkr"]["paper_port"]
