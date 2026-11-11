"""窗口程序主体：状态 / 选股池与持仓 / 净值 / 日志 / 策略说明 五个标签页，外加托盘图标和异常提醒。

只读：用只读连接取盈透数据，不下单。唯一会改动的文件是 config.toml 的候选股列表（“加入候选股”）。
盈透的数据在后台线程里取（每分钟一次），界面不会卡住。关闭窗口时缩到托盘，继续监控和提醒。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PySide6.QtCharts import (QCandlestickSeries, QCandlestickSet, QCategoryAxis, QChart, QChartView, QDateTimeAxis,
                              QLineSeries, QPieSeries, QValueAxis)
from PySide6.QtCore import (QDateTime, QObject, QProcess, QProcessEnvironment, QStringListModel, Qt, QThread, QTimer,
                            Signal, Slot)
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter, QDialog, QFormLayout,
                               QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSplitter, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from .. import records, strategy
from ..config import LOGS_DIR, ROOT, load_config
from . import checks, config_edit, ibdata, strategy_text, symbols

SNAPSHOT_MS = 60_000      # 每分钟从盈透取一次账户和持仓
CHECKS_MS = 5 * 60_000    # 每 5 分钟检查一次运行环境
FILES_MS = 15_000         # 每 15 秒看一次日志、last_run.json、config.toml 有没有变化
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
ICON = ROOT / "autotrader" / "gui" / "app.ico"   # tools/make_icon.py 生成
APP_ID = "QuantTrading.Monitor"
APP_NAME = "自动交易策略 - leiwang"

MODE_BADGE = {"paper": ("模拟盘 PAPER", "#1565c0"), "live": ("实盘 LIVE", "#c62828"), "dryrun": ("本地假券商", "#757575")}
DEFENSE_LABELS = {"IEF": ("美债", "#26a69a"), "TLT": ("美债", "#26a69a"), "SHY": ("美债", "#26a69a"),
                  "GLD": ("黄金", "#d4a017"), "IAU": ("黄金", "#d4a017")}   # 饼图里防守资产的名称和颜色

# 颜色跟随 Windows 的浅色 / 深色模式（启动时由 apply_theme 设置）。
# 行背景用半透明的色调（#AARRGGBB）叠在系统背景上，文字用系统颜色，两种模式下都看得清
DARK = False
GREEN = RED = ORANGE = GREY = BLUE = ""
BG_CHOSEN = BG_NEW = BG_DEFENSE = BG_CASH = BG_OTHER = ""


def apply_theme(dark: bool) -> None:
    global DARK, GREEN, RED, ORANGE, GREY, BLUE, BG_CHOSEN, BG_NEW, BG_DEFENSE, BG_CASH, BG_OTHER
    DARK = dark
    if dark:
        GREEN, RED, ORANGE, GREY, BLUE = "#81c784", "#ef9a9a", "#ffb74d", "#9e9e9e", "#64b5f6"
        a = "40"
    else:
        GREEN, RED, ORANGE, GREY, BLUE = "#2e7d32", "#c62828", "#ef6c00", "#757575", "#1565c0"
        a = "24"
    BG_CHOSEN, BG_NEW, BG_DEFENSE, BG_CASH, BG_OTHER = (f"#{a}43a047", f"#{a}ffb300", f"#{a}1e88e5",
                                                        f"#{a}9e9e9e", f"#{a}f4511e")


apply_theme(False)


def _style_chart(chart: QChart) -> None:
    chart.setTheme(QChart.ChartThemeDark if DARK else QChart.ChartThemeLight)
    chart.setBackgroundBrush(QApplication.palette().window())  # 和窗口背景一致，不出现一块白板
    chart.setBackgroundRoundness(0)
    chart.setTitleBrush(QApplication.palette().windowText())
    chart.legend().setLabelColor(QApplication.palette().windowText().color())


def _style_axis(axis) -> None:
    """坐标轴文字用窗口的文字颜色（浅色背景深色字、深色背景浅色字），保证看得清。"""
    axis.setLabelsBrush(QApplication.palette().windowText())


def _money(x: float | None, digits: int = 0) -> str:
    return "" if x is None else f"{x:,.{digits}f}"


def _pct(x: float | None, digits: int = 1) -> str:
    return "" if x is None else f"{x * 100:.{digits}f}%"


# 选股表“状态”列的简短文字 → 鼠标提示里的完整说明
STATUS_TIPS = {
    "新加入": "新加入的候选股，下次运行 run.py（或点“重新计算排名”）时参与排名",
    "策略外": "持有，但不属于当前策略（不在候选股和防守资产里）",
    "策略外，待卖出": "不属于当前策略，在清仓名单 exit_symbols 里，下次运行 run.py 时全部卖出",
    "暂无排名": "还没有排名记录：下次运行 run.py 后出现",
}


NEW_YORK = ZoneInfo("America/New_York")  # 美东时间：美股交易所用的时间，夏令时 / 冬令时由时区数据库自动切换


def eastern_clock(now: datetime | None = None) -> tuple[str, str]:
    """右上角时钟：(显示文字, 鼠标提示)。不管本机在哪个时区，都显示美东时间。
    不写死“UTC−5”：3 月第二个星期日起是夏令时 EDT（UTC−4），11 月第一个星期日起是冬令时 EST（UTC−5），
    换算交给 zoneinfo（Windows 上的数据来自 tzdata 包）。now 用于测试，需带时区。"""
    t = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    zone = "夏令时 EDT，UTC−4" if t.dst() else "冬令时 EST，UTC−5"
    weekday = "一二三四五六日"[t.weekday()]
    tip = (f"美东时间（纽约，美股交易所使用的时间）\n{t:%Y-%m-%d} 星期{weekday}，{zone}\n"
           "美股常规交易 9:30–16:00（美东）；夏令时 / 冬令时自动切换")
    return f"美东 {t:%H:%M:%S}", tip


def _cap(x: float) -> str:
    """市值：3.2 万亿美元 / 850 亿美元；没有数据（ETF、OTC）返回空。"""
    if not x:
        return ""
    return f"{x / 1e12:.2f} 万亿美元" if x >= 1e12 else f"{x / 1e8:,.0f} 亿美元"


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _tray_icon(color: str) -> QIcon:
    """托盘图标：程序图标右下角加一个状态点（绿 = 已连接，红 = 连不上或账户不一致，灰 = 还没连）。"""
    pix = QIcon(str(ICON)).pixmap(64, 64)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(QPen(QColor("white"), 4))
    p.drawEllipse(36, 36, 26, 26)
    p.end()
    return QIcon(pix)


class Worker(QObject):
    """后台线程：所有会阻塞的操作（连盈透、跑 PowerShell）都在这里做，一次只做一件。"""
    snapshot_done = Signal(dict)
    checks_done = Signal(list)
    validate_done = Signal(dict)

    @Slot()
    def setup(self) -> None:
        asyncio.set_event_loop(asyncio.new_event_loop())  # ib_async 需要本线程自己的事件循环

    @Slot(dict, list, str)
    def snapshot(self, cfg: dict, symbols: list, nav_start: str) -> None:
        try:
            data = ibdata.snapshot(cfg, symbols, nav_start or None)
        except BaseException as e:  # 连不上 Gateway、超时，或 ibkr_port 的 SystemExit（dryrun 模式）
            data = {"error": str(e) or type(e).__name__}
        data["time"] = datetime.now().strftime("%H:%M:%S")
        self.snapshot_done.emit(data)

    @Slot(dict)
    def run_checks(self, cfg: dict) -> None:
        result = checks.run_all(cfg)
        self.checks_done.emit([result, checks.scheduled_task(), checks.last_scheduled_run()])

    @Slot(dict, str, int)
    def validate(self, cfg: dict, symbol: str, need: int) -> None:
        try:
            data = ibdata.validate_symbol(cfg, symbol, need)
        except BaseException as e:
            data = {"ok": False, "symbol": symbol, "reason": f"连接盈透失败：{e or type(e).__name__}"}
        self.validate_done.emit(data)


class FitTable(QTableWidget):
    """各列至少和内容一样宽：窗口够宽时把多出的宽度平均分给各列，不够宽时出现横向滚动条（不会把某一列挤没）。
    填完数据后调用 fit_columns()；窗口大小变化时自动重新分配。"""

    def fit_columns(self) -> None:
        n = self.columnCount()
        if not n:
            return
        header = self.horizontalHeader()
        widths = [max(self.sizeHintForColumn(c), header.sectionSizeHint(c)) + 8 for c in range(n)]
        extra = self.viewport().width() - sum(widths)
        if extra > 0:
            widths = [w + extra // n for w in widths]
            widths[-1] += extra % n
        for c, w in enumerate(widths):
            self.setColumnWidth(c, w)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_columns()


def _table(headers: list[str], cls: type = QTableWidget) -> QTableWidget:
    t = cls(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(False)
    return t


def _set_row(t: QTableWidget, row: int, values: list, bg: str | None = None, bold: bool = False,
             right_from: int = 0, colors: dict[int, str] | None = None) -> None:
    for col, v in enumerate(values):
        it = QTableWidgetItem("" if v is None else str(v))
        it.setTextAlignment((Qt.AlignRight if col >= right_from > 0 else Qt.AlignLeft) | Qt.AlignVCenter)
        if bg:
            it.setBackground(QColor(bg))
        if colors and col in colors:
            it.setForeground(QColor(colors[col]))
        if bold:
            f = it.font()
            f.setBold(True)
            it.setFont(f)
        t.setItem(row, col, it)


class MainWindow(QMainWindow):
    req_snapshot = Signal(dict, list, str)
    req_checks = Signal(dict)
    req_validate = Signal(dict, str, int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.cfg = load_config()
        self.cfg_mtime = _mtime(ROOT / "config.toml")
        self.snap: dict | None = None
        self.last = records.load_last_run()
        self.last_mtime = _mtime(records.LAST_RUN)
        self.log_mtime = 0.0
        self.alerts: set[str] = set()          # 正在生效的提醒（同一件事只提醒一次，恢复后才会再提醒）
        self.log_pos = self._log_size()        # 只对窗口打开以后新出现的警告发提醒
        self.quitting = False
        self.told_tray = False

        self._build_ui()
        self._build_tray()
        self._start_worker()
        self.refresh_files(force=True)

        for ms, fn in ((SNAPSHOT_MS, self.request_snapshot), (CHECKS_MS, self.request_checks),
                       (FILES_MS, self.refresh_files)):
            timer = QTimer(self)
            timer.timeout.connect(fn)
            timer.start(ms)
        QTimer.singleShot(200, self.request_checks)
        QTimer.singleShot(400, self.request_snapshot)

    def _tick(self) -> None:
        text, tip = eastern_clock()
        if self.clock.text() != text:
            self.clock.setText(text)
            self.clock.setToolTip(tip)

    # ---------- 界面 ----------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        top = QHBoxLayout()
        self.badge = QLabel()
        self.conn_label = QLabel("正在连接 IB Gateway…")
        self.updated_label = QLabel("")
        self.updated_label.setStyleSheet(f"color:{GREY}")
        refresh = QPushButton("立即刷新")
        refresh.clicked.connect(lambda: (self.request_snapshot(), self.request_checks(), self.refresh_files(True)))
        top.addWidget(self.badge)
        top.addSpacing(12)
        top.addWidget(self.conn_label)
        top.addStretch()
        top.addWidget(self.updated_label)
        top.addWidget(refresh)
        # 时钟（本机时间，24 小时制，精确到秒）。每 0.25 秒检查一次，秒数变了才改文字，跳秒准而且不费资源
        self.clock = QLabel()
        # 和旁边的文字同一字体字号；固定宽度，跳秒时不会挤动左边的控件
        self.clock.setMinimumWidth(self.clock.fontMetrics().horizontalAdvance("美东 00:00:00") + 4)
        self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addSpacing(12)
        top.addWidget(self.clock)
        self._tick()
        clock_timer = QTimer(self)
        clock_timer.timeout.connect(self._tick)
        clock_timer.start(250)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._status_page(), "状态")
        self.tabs.addTab(self._pool_page(), "选股池与持仓")
        self.tabs.addTab(self._log_page(), "日志")
        self.tabs.addTab(self._strategy_page(), "策略说明")
        root.addWidget(self.tabs)
        self.setCentralWidget(central)

    def _status_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()

        acct = QGroupBox("连接与账户")
        form = QFormLayout(acct)
        self.st = {}
        for key, label in (("gateway", "IB Gateway"), ("api", "API 连接"), ("account", "账户"),
                           ("nav", "净值"), ("usd", "美元现金"), ("cad", "加元现金"), ("orders", "未成交挂单")):
            self.st[key] = QLabel("—")
            self.st[key].setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label + "：", self.st[key])
        row.addWidget(acct, 1)

        auto = QGroupBox("自动运行")
        form2 = QFormLayout(auto)
        for key, label in (("task", "计划任务"), ("sched", "上次自动运行"), ("decision", "最近一次决策")):
            self.st[key] = QLabel("—")
            self.st[key].setWordWrap(True)
            self.st[key].setTextInteractionFlags(Qt.TextSelectableByMouse)
            form2.addRow(label + "：", self.st[key])
        row.addWidget(auto, 1)
        lay.addLayout(row)

        env = QGroupBox("运行环境检查")
        env_lay = QVBoxLayout(env)
        self.env_table = _table(["项目", "状态", "说明", "解决办法"])
        h = self.env_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.env_table.setWordWrap(True)
        env_lay.addWidget(self.env_table)
        lay.addWidget(env, 1)
        return page

    def _pool_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.pool_info = QLabel("—")
        self.pool_info.setWordWrap(True)
        lay.addWidget(self.pool_info)

        split = QSplitter(Qt.Horizontal)
        # 各列至少按内容定宽（窗口窄时出现横向滚动条，而不是把某一列挤没），窗口宽时多出的宽度平均分给各列
        self.pool_table = _table(["排名", "代码", "状态", "趋势", "20日波动", "满额比例", "目标占比",
                                  "实际占比", "股数", "市值 USD", "成本价", "浮动盈亏 USD", "盈亏%"], cls=FitTable)
        self.pool_table.setContextMenuPolicy(Qt.CustomContextMenu)  # 右键菜单：删除候选股
        self.pool_table.customContextMenuRequested.connect(self.on_pool_menu)
        split.addWidget(self.pool_table)

        # 右侧上下两块：资金分布饼图 + 净值曲线（中间的分隔条可以拖动）
        right = QSplitter(Qt.Vertical)
        self.pie = QChart()
        _style_chart(self.pie)
        self.pie.setTitle("资金分布（实际）")
        self.pie.legend().setAlignment(Qt.AlignRight)
        pie_view = QChartView(self.pie)
        pie_view.setRenderHint(QPainter.Antialiasing)
        right.addWidget(pie_view)
        right.addWidget(self._nav_widget())
        right.setStretchFactor(0, 2)
        right.setStretchFactor(1, 3)
        right.setMinimumWidth(460)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        legend = QLabel("目标占比、实际占比都按“可投资金额”计算：(美元现金 + 持股市值) × (1 − 预留现金)，和 run.py 一致。"
                        "绿色 = 入选（持有名单）；黄色 = 新加入、还没参与过排名；蓝色 = 防守资产；橙色 = 不属于当前策略的持仓。")
        legend.setWordWrap(True)
        legend.setStyleSheet(f"color:{GREY}")
        lay.addWidget(legend)

        add = QHBoxLayout()
        add.addWidget(QLabel("加入候选股："))
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("代码、英文名或中文名，如 MSFT / micro / 微软")
        self.add_input.setMinimumWidth(300)
        self.add_input.returnPressed.connect(self.on_add)
        # 离线检索提示：边输入边在本地查（symbols.py），不连盈透
        self.symbol_index = symbols.load_index()
        self.symbol_hits: dict[str, str] = {}   # 下拉列表里的文字 → 代码
        self.completer_model = QStringListModel(self)
        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.completer.setMaxVisibleItems(12)
        self.add_input.setCompleter(self.completer)
        self.add_input.textEdited.connect(self.on_symbol_typed)
        self.completer.activated[str].connect(
            lambda text: QTimer.singleShot(0, lambda: self.add_input.setText(self.symbol_hits.get(text, text))))
        self.add_button = QPushButton("检查并加入")
        self.add_button.clicked.connect(self.on_add)
        self.dry_button = QPushButton("重新计算排名（试运行，不下单）")
        self.dry_button.clicked.connect(self.on_dry_run)
        self.pool_status = QLabel("")
        self.pool_status.setWordWrap(True)
        add.addWidget(self.add_input)
        add.addWidget(self.add_button)
        add.addSpacing(20)
        add.addWidget(self.dry_button)
        add.addWidget(self.pool_status, 1)
        lay.addLayout(add)
        return page

    def _nav_widget(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        self.nav_chart = QChart()
        _style_chart(self.nav_chart)
        self.nav_chart.legend().setAlignment(Qt.AlignBottom)
        view = QChartView(self.nav_chart)
        view.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(view, 1)
        self.nav_stats = QLabel("—")
        self.nav_stats.setWordWrap(True)
        self.nav_stats.setToolTip("净值来自每次运行 run.py 时记录的账户净值（每天取最后一次），包含加元现金和汇率变化；"
                                  "SPY 为同期复权收盘价，用美元计。两条线都从第一天起算收益率。")
        lay.addWidget(self.nav_stats)
        return box

    def _log_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.log_choice = QComboBox()
        self.log_choice.addItems(["trader.log（run.py 的决策和下单）", "scheduled_run.log（计划任务的每次运行）"])
        self.log_choice.currentIndexChanged.connect(lambda: self.refresh_log(force=True))
        self.log_warn_only = QCheckBox("只看警告和错误")
        self.log_warn_only.toggled.connect(lambda: self.refresh_log(force=True))
        bar.addWidget(self.log_choice)
        bar.addWidget(self.log_warn_only)
        bar.addStretch()
        lay.addLayout(bar)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        lay.addWidget(self.log_view)
        return page

    def _strategy_page(self) -> QWidget:
        self.strategy_view = QTextBrowser()
        self.strategy_view.setOpenExternalLinks(False)
        return self.strategy_view

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_tray_icon(GREY), self)
        menu = QMenu()
        show = QAction("显示窗口", self)
        show.triggered.connect(self.show_window)
        quit_ = QAction("退出", self)
        quit_.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window()
                                    if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    def _start_worker(self) -> None:
        self.thread_ = QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.thread_)
        self.thread_.started.connect(self.worker.setup)
        self.req_snapshot.connect(self.worker.snapshot)
        self.req_checks.connect(self.worker.run_checks)
        self.req_validate.connect(self.worker.validate)
        self.worker.snapshot_done.connect(self.on_snapshot)
        self.worker.checks_done.connect(self.on_checks)
        self.worker.validate_done.connect(self.on_validate)
        self.thread_.start()

    # ---------- 托盘与关闭 ----------
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self.quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            self.thread_.quit()
            self.thread_.wait(15_000)
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self.told_tray:
            self.tray.showMessage(APP_NAME, "窗口已缩到托盘，继续监控和提醒。右键托盘图标选“退出”可完全关闭。",
                                  QSystemTrayIcon.Information, 5000)
            self.told_tray = True

    def quit_app(self) -> None:
        self.quitting = True
        self.close()
        QApplication.quit()

    def notify(self, title: str, message: str, level=QSystemTrayIcon.Warning) -> None:
        self.tray.showMessage(title, message, level, 15_000)

    def alert(self, key: str, active: bool, message: str) -> None:
        """同一件事只在“刚出现”时提醒一次；恢复正常后清除，下次再出现会再提醒。"""
        if active and key not in self.alerts:
            self.alerts.add(key)
            self.notify("量化交易提醒", message)
        elif not active:
            self.alerts.discard(key)

    # ---------- 后台请求 ----------
    def request_snapshot(self) -> None:
        params = strategy.load(self.cfg)
        nav = records.load_nav(self.cfg["mode"])
        self.req_snapshot.emit(self.cfg, strategy.symbols(params), nav[0]["time"][:10] if nav else "")

    def request_checks(self) -> None:
        self.req_checks.emit(self.cfg)

    # ---------- 收到后台结果 ----------
    @Slot(dict)
    def on_snapshot(self, data: dict) -> None:
        self.updated_label.setText(f"盈透数据更新于 {data['time']}")
        mode = self.cfg["mode"]
        if "error" in data:
            self.snap = None
            self.conn_label.setText(f"<span style='color:{RED}'>● 连不上 IB Gateway：{data['error']}</span>")
            self.st["api"].setText(f"<span style='color:{RED}'>连不上：{data['error']}</span>")
            for k in ("account", "nav", "usd", "cad", "orders"):
                self.st[k].setText("—")
            self.tray.setIcon(_tray_icon(RED))
            self.tray.setToolTip(f"{APP_NAME}：连不上 IB Gateway")
            if mode != "dryrun":
                self.alert("api", True, f"连不上 IB Gateway（{data['error']}）。"
                                        "请登录 Gateway 的模拟账户，否则今天的自动交易会失败。")
        else:
            self.snap = data
            self.alert("api", False, "")
            acct = "、".join(data["accounts"])
            kind = "实盘" if data["is_live"] else "模拟盘"
            mismatch = data["is_live"] != (mode == "live")
            color = RED if mismatch else GREEN
            self.conn_label.setText(f"<span style='color:{color}'>● 已连接 {acct}（{kind}）</span>")
            self.st["api"].setText(f"<span style='color:{GREEN}'>已连接（只读）</span>")
            self.st["account"].setText(f"{acct}（{kind}）" + (
                f" <span style='color:{RED}'>⚠ 和 config.toml 的 mode = {mode} 不一致</span>" if mismatch else ""))
            self.alert("mismatch", mismatch, f"Gateway 登录的是{kind}账户，但 config.toml 的 mode = {mode}。"
                                             "run.py 会拒绝运行，请检查 Gateway 的登录。")
            self.st["nav"].setText(f"{_money(data['nav'], 2)} {data['currency']}")
            self.st["usd"].setText(_money(data["cash"].get("USD"), 2))
            self.st["cad"].setText(_money(data["cash"].get("CAD"), 2) + "（不参与策略）")
            orders = data["orders"]
            self.st["orders"].setText("、".join(f"{o['symbol']} {o['action']} {o['qty']:.0f}（{o['status']}）"
                                               for o in orders) if orders else "无")
            self.tray.setIcon(_tray_icon(RED if mismatch else GREEN))
            self.tray.setToolTip(f"{APP_NAME}：{acct} 净值 {_money(data['nav'])} {data['currency']}")
        self.refresh_pool()
        self.refresh_nav()

    @Slot(list)
    def on_checks(self, payload: list) -> None:
        result, task, sched = payload
        gw = next((c for c in result if c.name == "IB Gateway 正在运行"), None)
        if gw:
            self.st["gateway"].setText(f"<span style='color:{GREEN if gw.ok else RED}'>{gw.detail}</span>")
        t = self.env_table
        t.setRowCount(len(result))
        for i, c in enumerate(result):
            mark, color = {True: ("✓ 满足", GREEN), False: ("× 不满足", RED), None: ("! 提醒", ORANGE)}[c.ok]
            _set_row(t, i, [c.name, mark, c.detail, "" if c.ok else c.fix], colors={1: color})
        t.resizeRowsToContents()

        if task:
            ok = task["state"] in ("Ready", "Running")
            self.st["task"].setText(f"<span style='color:{GREEN if ok else RED}'>{task['state']}</span>，"
                                    f"下次运行 {task['next_run']}")
        else:
            self.st["task"].setText(f"<span style='color:{RED}'>不存在：不会自动运行</span>")
        if sched:
            if sched["failed"]:
                tail = "<br>".join(sched["tail"][-3:])
                self.st["sched"].setText(f"<span style='color:{RED}'>{sched['time']} 失败</span><br>"
                                         f"<span style='color:{GREY}'>{tail}</span>")
            else:
                self.st["sched"].setText(f"<span style='color:{GREEN}'>{sched['time']} 成功</span>")
            self.alert(f"sched:{sched['time']}", sched["failed"],
                       f"{sched['time']} 的自动运行失败，详情见“日志”页的 scheduled_run.log。")
        else:
            self.st["sched"].setText("还没有记录")

    @Slot(dict)
    def on_validate(self, data: dict) -> None:
        self.add_button.setEnabled(True)
        sym = data.get("symbol", "")
        if not data["ok"]:
            self.pool_status.setText(f"<span style='color:{RED}'>{sym} 不能加入：{data['reason']}</span>")
            return
        msg = (f"{sym}：{data['name']}\n交易所 {data['exchange']}，类型 {data['type'] or '—'}\n"
               f"历史日线 {data['bars']} 根（从 {data['first']} 起，至少需要 {data['need']} 根）\n\n"
               f"加入后 config.toml 的候选股变为 {len(strategy.load(self.cfg)['stocks']) + 1} 只。"
               "不影响现在的持仓；下一次运行 run.py 时，它会和其他候选股一起参与排名。\n\n确定加入吗？")
        if QMessageBox.question(self, "加入候选股", msg) != QMessageBox.Yes:
            self.pool_status.setText(f"已取消加入 {sym}")
            return
        try:
            config_edit.add_candidate(sym)
        except Exception as e:
            self.pool_status.setText(f"<span style='color:{RED}'>写入 config.toml 失败：{e}</span>")
            return
        self.add_input.clear()
        self.symbol_index = symbols.load_index()  # 刚查到的公司名也能搜到了
        self.pool_status.setText(f"<span style='color:{GREEN}'>已加入 {sym}（config.toml 已备份为 config.toml.bak）。"
                                 "正在后台下载它的历史数据供回测使用…</span>")
        self.refresh_files(force=True)
        self._run_process([str(ROOT / "fetch_data.py"), sym],
                          lambda code, out: self.pool_status.setText(
                              f"已加入 {sym}；历史数据" + ("已下载到 data/history/" if code == 0 else
                                                       f"下载失败（不影响交易）：{out[-200:]}")))

    # ---------- 右键菜单 ----------
    def on_pool_menu(self, pos) -> None:
        t = self.pool_table
        item = t.itemAt(pos)
        if item is None or t.item(item.row(), 1) is None:
            return
        sym = t.item(item.row(), 1).text()
        params = strategy.load(self.cfg)
        if params["kind"] != "sleeves" or sym in ("", "USD", "合计"):
            return
        menu = QMenu(t)
        row = item.row()
        menu.addAction(f"显示股票详情（{sym}）").triggered.connect(lambda: self.show_symbol_info(sym, row))
        menu.addAction(f"近 1 年 K 线（{sym}）").triggered.connect(lambda: self.show_kline(sym))
        menu.addSeparator()
        if sym in params["stocks"]:
            menu.addAction(f"从候选池删除 {sym}…").triggered.connect(lambda: self.on_remove(sym))
        elif sym in params["defense"]:
            menu.addAction(f"{sym} 是防守资产，不能在这里删除（在 config.toml 的 defense 里修改）").setEnabled(False)
        else:
            menu.addAction(f"{sym} 不属于当前策略" + ("，下次运行会全部卖出" if sym in strategy.exit_symbols(params)
                                                 else "")).setEnabled(False)
        menu.exec(t.viewport().mapToGlobal(pos))

    def show_symbol_info(self, sym: str, row: int) -> None:
        """股票详情：和“加入候选股”搜索提示同一份离线资料（symbols.py），外加它在当前策略里的状态。"""
        e = self.symbol_index.get(sym)
        names = self.snap["names"] if self.snap else ibdata._load_names()
        cell = lambda c: (self.pool_table.item(row, c).text() if self.pool_table.item(row, c) else "")  # noqa: E731
        rank, status = cell(0), cell(2)
        fields = [("代码", sym),
                  ("中文名", e.cn if e else ""),
                  ("英文名", (e.name if e else "") or names.get(sym, "")),
                  ("行业", e.sector if e else ""),
                  ("类型", ("ETF" if e.etf else "股票") if e and (e.name or e.exchange) else ""),
                  ("交易所", e.exchange if e else ""),
                  ("市值", _cap(e.market_cap) if e else ""),
                  ("国家/地区", e.country if e else ""),
                  ("简介", e.note if e else ""),
                  ("在本策略中", (f"第 {rank} 名，" if rank else "") + status)]
        html = "".join(f"<tr><td style='color:{GREY}; padding-right:12px'>{k}</td><td>{v}</td></tr>"
                       for k, v in fields if v)
        if not e or not (e.name or e.cn):
            html += (f"<tr><td colspan='2' style='color:{GREY}'><br>离线资料里没有这只股票的详细信息。"
                     "可以运行 tools\\update_symbols.py 更新美股代码列表。</td></tr>")
        box = QMessageBox(QMessageBox.Information, f"股票详情 - {sym}", f"<table>{html}</table>", QMessageBox.Ok, self)
        box.setTextFormat(Qt.RichText)
        box.exec()

    def show_kline(self, sym: str) -> None:
        """近 1 年日 K 线 + 策略用的均线（个股 5 条，防守资产 1 条）。
        数据是 run.py 每次运行时存的 data/bars/（每天更新），没有时用 data/history/。"""
        rows, source = records.load_recent_bars(sym)
        if len(rows) < 2:
            QMessageBox.information(self, f"近 1 年 K 线 - {sym}",
                                    f"还没有 {sym} 的日线数据。\n\n参与策略的股票在下一次运行 run.py（或点“重新计算排名”）后就有；"
                                    f"其他股票可以运行 fetch_data.py {sym} 下载。")
            return
        closes = [r["close"] for r in rows]
        start = max(0, len(rows) - 252)  # 约 1 年的交易日
        # 均线：个股画策略的 5 条（trend_windows，图例写上权重）；防守资产只看 200 日均线（defense_trend_window）
        params = strategy.load(self.cfg)
        is_defense = sym in params.get("defense", [])
        if is_defense:
            lines = [(params.get("defense_trend_window", 200), None)]
        else:
            ws = list(params.get("trend_windows", [100, 150, 200, 250, 300]))
            wt = params.get("trend_weights") or [1 / len(ws)] * len(ws)
            lines = list(zip(ws, wt))
        mas = []  # [(窗口, 权重, {行号: 均线值})]
        for n, w in lines:
            mas.append((n, w, {i: sum(closes[i - n + 1:i + 1]) / n for i in range(n - 1, len(rows))}))
        view = rows[start:]

        chart = QChart()
        _style_chart(chart)
        chart.legend().setAlignment(Qt.AlignBottom)
        candles = QCandlestickSeries()
        candles.setName("日 K（复权）")
        candles.setIncreasingColor(QColor(GREEN))
        candles.setDecreasingColor(QColor(RED))
        candles.setBodyOutlineVisible(False)
        candles.setCapsVisible(False)
        candles.setBodyWidth(0.85)
        candles.setMinimumColumnWidth(1.5)
        # 均线配色用 Okabe-Ito 色盲友好色，避开 K 线的红绿；线型也各不相同，只看形状也能区分。权重越大线越粗
        styles = [("#56B4E9", Qt.DotLine), ("#CC79A7", Qt.DashDotLine), ("#E69F00", Qt.DashLine),
                  (QApplication.palette().windowText().color().name(), Qt.SolidLine),
                  ("#F0E442" if DARK else "#0072B2", Qt.DashDotDotLine)]
        ma_series = []
        for k, (n, w, _) in enumerate(mas):
            line = QLineSeries()
            if w is None:
                line.setName(f"{n} 日均线（防守资产看它判断趋势）")
                pen = QPen(QColor("#E69F00"), 1.8)
            else:
                color, style = styles[k % len(styles)]
                line.setName(f"{n} 日均线")
                pen = QPen(QColor(color), 1.2 + 2.2 * w)
                pen.setStyle(style)
            line.setPen(pen)
            ma_series.append(line)
        for i, r in enumerate(view, start):
            ts = QDateTime.fromString(r["date"], "yyyy-MM-dd").toMSecsSinceEpoch()
            bar = QCandlestickSet(r["open"], r["high"], r["low"], r["close"], ts)
            color = QColor(GREEN if r["close"] >= r["open"] else RED)
            bar.setPen(QPen(color, 1))  # 影线和实体同色
            bar.setBrush(color)
            candles.append(bar)
            for line, (_, _, ma) in zip(ma_series, mas):
                if i in ma:
                    line.append(ts, ma[i])
        chart.addSeries(candles)
        for line in ma_series:
            if line.count():
                chart.addSeries(line)
        # 横轴：K 线只能挂在日期轴上，但日期轴的刻度是等分的、对不准月初。所以 K 线挂在一条隐藏的日期轴上，
        # 另放一条范围完全相同的轴只用来显示月份：每个月第一个交易日标一次（1 月带上年份），并画竖线；起止年月写在标题里
        first_ts = QDateTime.fromString(view[0]["date"], "yyyy-MM-dd").toMSecsSinceEpoch()
        last_ts = QDateTime.fromString(view[-1]["date"], "yyyy-MM-dd").toMSecsSinceEpoch()
        day = 86_400_000
        ax = QDateTimeAxis()
        ax.setRange(QDateTime.fromMSecsSinceEpoch(first_ts - day), QDateTime.fromMSecsSinceEpoch(last_ts + day))
        ax.setVisible(False)
        months = QCategoryAxis()
        months.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
        months.setStartValue(first_ts - day)
        prev_month = view[0]["date"][:7]
        for r in view[1:]:
            if r["date"][:7] != prev_month:
                y, m = int(r["date"][2:4]), int(r["date"][5:7])
                months.append(f"{y}年{m}月" if m == 1 else f"{m}月",
                              QDateTime.fromString(r["date"], "yyyy-MM-dd").toMSecsSinceEpoch())
                prev_month = r["date"][:7]
        months.setRange(first_ts - day, last_ts + day)
        guide = QLineSeries()  # 月份轴需要挂一条线才会显示；这条线不画出来、也不进图例
        guide.append(first_ts - day, view[0]["close"])
        guide.append(last_ts + day, view[-1]["close"])
        guide.setPen(QPen(Qt.NoPen))
        ay = QValueAxis()
        values = ([r["low"] for r in view] + [r["high"] for r in view]
                  + [v for _, _, ma in mas for i, v in ma.items() if i >= start])
        lo, hi = min(values), max(values)
        ay.setRange(lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05)
        ay.applyNiceNumbers()  # 刻度取整齐的数（如 60、70、80）
        ay.setLabelFormat("%.0f" if hi >= 50 else "%.2f")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(months, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        for axis in (months, ay):  # 加进图表以后再设颜色，否则会被图表主题覆盖
            _style_axis(axis)
        for s in chart.series():
            s.attachAxis(ax)
            s.attachAxis(ay)
        chart.addSeries(guide)
        guide.attachAxis(months)
        guide.attachAxis(ay)
        chart.legend().markers(guide)[0].setVisible(False)
        e = self.symbol_index.get(sym)
        names = self.snap["names"] if self.snap else ibdata._load_names()
        chart.setTitle(f"{sym}  {(e.cn if e and e.cn else '') or names.get(sym, '')}  近 1 年日 K 线"
                       f"（{view[0]['date'][:7]} 至 {view[-1]['date'][:7]}）")

        last, first = view[-1], view[0]
        year_hi, year_lo = max(r["high"] for r in view), min(r["low"] for r in view)
        summary = QLabel(f"最新收盘 <b>{last['close']:.2f}</b>（{last['date']}）　"
                         f"近 1 年 <b style='color:{GREEN if last['close'] >= first['close'] else RED}'>"
                         f"{last['close'] / first['close'] - 1:+.1%}</b>　1 年最高 {year_hi:.2f}，最低 {year_lo:.2f}")
        note = QLabel(f"数据截至 {last['date']}，{source}。价格为复权价（已按分红、拆股调整，早期价格比当时实际成交价略低）。"
                      "绿色 = 收涨，红色 = 收跌。"
                      + ("防守资产只看 200 日均线：价格在它之上才持有。" if is_defense else
                         "均线是策略算趋势用的 5 条：价格在各条均线之上的加权比例就是趋势信号，权重越大的线画得越粗。"))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{GREY}")
        dlg = QDialog(self)
        dlg.setWindowTitle(f"近 1 年 K 线 - {sym}")
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        lay = QVBoxLayout(dlg)
        view_widget = QChartView(chart)
        view_widget.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(view_widget, 1)
        lay.addWidget(summary)
        lay.addWidget(note)
        dlg.resize(860, 540)
        dlg.show()  # 不阻塞主窗口，可以同时打开几只股票对比

    def _held(self, sym: str) -> tuple[float, float | None, float]:
        """(持股数, 市值, 挂单净股数)：优先盈透实时数据，连不上时用上次运行记录。"""
        if self.snap:
            h = next((p for p in self.snap["portfolio"] if p["symbol"] == sym and p["sec_type"] == "STK"), None)
            pend = sum(o["qty"] if o["action"] == "BUY" else -o["qty"] for o in self.snap["orders"] if o["symbol"] == sym)
            return (h["qty"], h["value"], pend) if h else (0, None, pend)
        if self.last:
            qty = self.last.get("positions", {}).get(sym, 0)
            price = self.last.get("prices", {}).get(sym)
            return qty, qty * price if price else None, self.last.get("pending", {}).get(sym, 0)
        return 0, None, 0

    def on_remove(self, sym: str) -> None:
        params = strategy.load(self.cfg)
        stocks = params["stocks"]
        if len(stocks) <= 1:
            self.pool_status.setText(f"<span style='color:{RED}'>候选池里至少要留一只股票</span>")
            return
        names = self.snap["names"] if self.snap else ibdata._load_names()
        n, cap = len(stocks) - 1, params.get("max_holdings") or len(stocks)
        qty, value, pend = self._held(sym)
        title = f"{sym}（{names[sym]}）" if names.get(sym) else sym
        lines = [f"从候选池删除 {title}？", "",
                 f"删除后候选股变为 {n} 只。"]
        if n <= cap:
            lines.append(f"候选股不多于持有上限 {cap} 只，之后会全部持有，每只的满额变为 "
                         f"{params['stock_share'] / n:.1%}（现在是 {params['stock_share'] / min(cap, len(stocks)):.1%}）。")
        if qty or pend:
            held = f"持有 {qty:,.0f} 股" + (f"（市值约 {value:,.0f} USD）" if value else "")
            held += f"，另有挂单 {pend:+,.0f} 股" if pend else ""
            lines.append(f"\n你现在{held}。删除后它会加入清仓名单（exit_symbols），下一次运行 run.py 时全部卖出。")
        else:
            lines.append("\n现在没有持仓，删除不会产生交易。")
        lines.append("\nconfig.toml 会先备份为 config.toml.bak。")
        box = QMessageBox(QMessageBox.Warning if qty or pend else QMessageBox.Question, "删除候选股", "\n".join(lines),
                          QMessageBox.Yes | QMessageBox.No, self)
        box.setDefaultButton(QMessageBox.No)
        box.exec()
        if box.standardButton(box.clickedButton()) != QMessageBox.Yes:
            self.pool_status.setText(f"已取消删除 {sym}")
            return
        try:
            config_edit.remove_candidate(sym)
        except Exception as e:
            self.pool_status.setText(f"<span style='color:{RED}'>写入 config.toml 失败：{e}</span>")
            return
        note = "，下次运行时卖出现有持仓" if qty or pend else ""
        self.pool_status.setText(f"<span style='color:{GREEN}'>已从候选池删除 {sym}{note}"
                                 "（config.toml 已备份为 config.toml.bak）</span>")
        self.refresh_files(force=True)

    # ---------- 按钮 ----------
    def on_symbol_typed(self, text: str) -> None:
        params = strategy.load(self.cfg)
        held = set(params.get("stocks", [])) | set(params.get("defense", []))
        self.symbol_hits = {}
        for e in symbols.search(self.symbol_index, text):
            label = e.label() + ("   ✓ 已在候选池" if e.symbol in held else "")
            self.symbol_hits[label] = e.symbol
        self.completer_model.setStringList(list(self.symbol_hits))

    def on_add(self) -> None:
        text = self.add_input.text()
        sym = self.symbol_hits.get(text, text).strip().upper().replace(".", " ")
        params = strategy.load(self.cfg)
        if params["kind"] != "sleeves":
            self.pool_status.setText("当前策略不是 sleeves，没有候选股池")
            return
        if not sym or not all(ch.isalnum() or ch == " " for ch in sym):
            self.pool_status.setText(f"<span style='color:{RED}'>请输入美股代码，如 MSFT</span>")
            return
        if sym in params["stocks"] or sym in params["defense"]:
            self.pool_status.setText(f"{sym} 已经在策略里了")
            return
        need = strategy.history_days(params)
        self.add_button.setEnabled(False)
        self.pool_status.setText(f"正在向盈透查询 {sym}…")
        self.req_validate.emit(self.cfg, sym, need)

    def on_dry_run(self) -> None:
        self.dry_button.setEnabled(False)
        self.pool_status.setText("正在试运行 run.py --no-trade（只计算，不下单），大约需要半分钟…")

        def done(code: int, out: str) -> None:
            self.dry_button.setEnabled(True)
            if code == 0:
                self.pool_status.setText(f"<span style='color:{GREEN}'>试运行完成，排名已更新（未下单）</span>")
            else:
                self.pool_status.setText(f"<span style='color:{RED}'>试运行失败：{out.strip()[-300:]}</span>")
            self.refresh_files(force=True)
        self._run_process([str(ROOT / "run.py"), "--no-trade"], done)

    def _run_process(self, args: list[str], on_done) -> None:
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(str(ROOT))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.finished.connect(lambda code, _status: on_done(
            code, bytes(proc.readAll()).decode("utf-8", errors="replace")))
        proc.start(str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable), args)

    # ---------- 本地文件 ----------
    def _log_size(self) -> int:
        try:
            return (LOGS_DIR / "trader.log").stat().st_size
        except FileNotFoundError:
            return 0

    def refresh_files(self, force: bool = False) -> None:
        cfg_m = _mtime(ROOT / "config.toml")
        if force or cfg_m != self.cfg_mtime:
            try:
                self.cfg = load_config()
                self.cfg_mtime = cfg_m
            except Exception as e:
                self.pool_status.setText(f"<span style='color:{RED}'>config.toml 读取失败：{e}</span>")
            text, color = MODE_BADGE.get(self.cfg["mode"], (self.cfg["mode"], GREY))
            self.badge.setText(text)
            self.badge.setStyleSheet(f"background:{color}; color:white; padding:4px 10px; border-radius:4px;"
                                     "font-weight:bold")
            self.strategy_view.setMarkdown(strategy_text.markdown(self.cfg))
        last_m = _mtime(records.LAST_RUN)
        if force or last_m != self.last_mtime:
            self.last = records.load_last_run()
            self.last_mtime = last_m
            self.refresh_decision()
        self.refresh_pool()
        self.refresh_nav()
        self.refresh_log(force)
        self.check_new_warnings()

    def check_new_warnings(self) -> None:
        size = self._log_size()
        if size < self.log_pos:  # 日志被清空或重建
            self.log_pos = 0
        if size == self.log_pos:
            return
        with (LOGS_DIR / "trader.log").open("rb") as f:
            f.seek(self.log_pos)
            new = f.read().decode("utf-8", errors="replace").splitlines()
        self.log_pos = size
        warns = [line for line in new if " WARNING " in line or " ERROR " in line]
        if warns:
            first = warns[0].split(" ", 3)[-1]
            more = f"（另有 {len(warns) - 1} 条）" if len(warns) > 1 else ""
            self.notify("run.py 出现警告", first[:200] + more)

    def refresh_decision(self) -> None:
        last = self.last
        if not last:
            self.st["decision"].setText("还没有记录（下次运行 run.py 后出现）")
            return
        orders = last.get("orders", [])
        kind = "试运行（不下单）" if last.get("no_trade") else "正式运行"
        if orders:
            detail = "；".join(f"{o['symbol']} {o['qty']:+d} 股 → {o['result']}" for o in orders)
        else:
            detail = "没有标的偏离超过再平衡阈值，不交易"
        self.st["decision"].setText(f"{last['time']}，{kind}，信号日 {last['signal'].get('date', '?')}<br>{detail}")

    def refresh_log(self, force: bool = False) -> None:
        path = LOGS_DIR / ("trader.log" if self.log_choice.currentIndex() == 0 else "scheduled_run.log")
        m = _mtime(path)
        if not force and m == self.log_mtime:
            return
        self.log_mtime = m
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            self.log_view.setPlainText(f"{path} 还不存在")
            return
        if self.log_warn_only.isChecked():
            lines = [x for x in lines if " WARNING " in x or " ERROR " in x or "失败" in x]
        html = []
        for line in lines[-400:]:
            esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;")
            if " ERROR " in line or "失败" in line or "Traceback" in line:
                esc = f"<span style='color:{RED}'>{esc}</span>"
            elif " WARNING " in line:
                esc = f"<span style='color:{ORANGE}'>{esc}</span>"
            elif "下单" in line and "本应" not in line:
                esc = f"<b>{esc}</b>"
            elif line.startswith("=====") or "信号（" in line:
                esc = f"<span style='color:{BLUE}'>{esc}</span>"
            html.append(esc)
        self.log_view.setHtml("<br>".join(html))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    # ---------- 选股池 ----------
    def refresh_pool(self) -> None:
        params = strategy.load(self.cfg)
        last, snap = self.last, self.snap
        if params["kind"] != "sleeves":
            self.pool_info.setText("当前策略不是 sleeves（个股 + 防守），没有候选股池")
        assets = last["signal"].get("assets", {}) if last and last.get("kind") == "sleeves" else {}
        weights = last.get("weights", {}) if last else {}
        names = snap["names"] if snap else ibdata._load_names()

        # 持仓：优先用盈透的实时数据，连不上时用上次运行记录
        holdings: dict[str, dict] = {}
        pending: dict[str, float] = {}
        if snap:
            for p in snap["portfolio"]:
                if p["sec_type"] == "STK" and p["currency"] == "USD":
                    holdings[p["symbol"]] = p
            for o in snap["orders"]:
                pending[o["symbol"]] = pending.get(o["symbol"], 0) + (o["qty"] if o["action"] == "BUY" else -o["qty"])
            usd_cash = snap["cash"].get("USD", 0.0)
            source = f"持仓为盈透实时数据（{snap['time']}）"
        elif last:
            for s, q in last.get("positions", {}).items():
                if q:
                    price = last["prices"].get(s)
                    holdings[s] = {"symbol": s, "qty": q, "price": price, "value": q * price if price else None,
                                   "avg_cost": None, "unrealized": None}
            usd_cash = last["account"]["usd_cash"]
            source = f"连不上盈透，持仓为上次运行记录（{last['time']}）"
        else:
            usd_cash, source = 0.0, "还没有持仓数据"
        equity = usd_cash + sum(h["value"] or 0 for h in holdings.values())
        investable = equity * (1 - params["cash_reserve"]) or 1.0

        stocks = list(params.get("stocks", []))
        defense = list(params.get("defense", []))
        ranked = sorted([s for s in stocks if s in assets], key=lambda s: assets[s]["rank"])
        new = [s for s in stocks if s not in assets]
        others = [s for s in holdings if s not in stocks and s not in defense]

        rows: list[tuple[list, str]] = []

        def holding_cols(s: str) -> list:
            h = holdings.get(s)
            if not h:
                qty = "0" + (f"（挂单 {pending[s]:+.0f}）" if pending.get(s) else "")
                return [_pct(0.0), qty, "", "", "", ""]
            pnl_pct = (h["price"] / h["avg_cost"] - 1) if h.get("avg_cost") and h.get("price") else None
            qty = f"{h['qty']:,.0f}" + (f"（挂单 {pending[s]:+.0f}）" if pending.get(s) else "")
            return [_pct((h["value"] or 0) / investable), qty, _money(h["value"]),
                    _money(h.get("avg_cost"), 2), _money(h.get("unrealized")), _pct(pnl_pct)]

        for s in ranked:
            a = assets[s]
            status = "入选" if a["chosen"] else "候选"
            rows.append(([a["rank"], s, status, _pct(a["trend"], 0), _pct(a["vol"]),
                          _pct(a["size"], 0), _pct(weights.get(s, 0.0))] + holding_cols(s),
                         BG_CHOSEN if a["chosen"] else ""))
        for s in new:
            if assets:
                rows.append((["", s, "新加入", "", "", "", ""] + holding_cols(s),
                             BG_NEW))
            else:  # 还没有任何排名记录（run.py 还没在新版本下运行过）
                rows.append((["", s, "暂无排名", "", "", "", ""] + holding_cols(s), ""))
        for s in defense:
            a = assets.get(s, {})
            trend = "" if not a else ("向上" if a.get("trend") else "向下（留现金）")
            rows.append((["", s, "防守", trend, _pct(a.get("vol")), "",
                          _pct(weights.get(s)) if s in weights else ""] + holding_cols(s), BG_DEFENSE))
        exiting = set(strategy.exit_symbols(params)) if params["kind"] == "sleeves" else set()
        for s in others:
            status = "策略外，待卖出" if s in exiting else "策略外"
            rows.append((["", s, status, "", "", "", ""] + holding_cols(s), BG_OTHER))
        target_cash = 1 - sum(weights.values()) if weights else None
        rows.append((["", "USD", "美元现金", "", "", "", _pct(target_cash), _pct(usd_cash / investable),
                      "", _money(usd_cash), "", "", ""], BG_CASH))

        t = self.pool_table
        t.setRowCount(len(rows) + 1)
        for i, (vals, bg) in enumerate(rows):
            pnl = vals[11]
            color = {11: GREEN if not pnl.startswith("-") else RED, 12: GREEN if not vals[12].startswith("-") else RED}
            _set_row(t, i, vals, bg=bg or None, right_from=3, colors=color if pnl else None)
            # 公司名不单独占一列：鼠标停在代码上显示（完整资料见右键“显示股票详情”）
            name = "美元现金" if vals[1] == "USD" else names.get(vals[1], "")
            if name and t.item(i, 1):
                t.item(i, 1).setToolTip(name)
            tip = STATUS_TIPS.get(vals[2])
            if tip and t.item(i, 2):
                t.item(i, 2).setToolTip(tip)
        total_pnl = sum(h.get("unrealized") or 0 for h in holdings.values())
        _set_row(t, len(rows), ["", "合计", "策略资产", "", "", "", "", "", "",
                                _money(equity), "", _money(total_pnl), ""], bold=True, right_from=3)
        if t.item(len(rows), 2):
            t.item(len(rows), 2).setToolTip("策略资产 = 美元现金 + 持股市值")
        t.fit_columns()

        # 说明行
        if params["kind"] == "sleeves":
            k, n, b = strategy.max_holdings(params), len(stocks), strategy.rank_buffer(params)
            info = [f"候选股 {n} 只，持有排名前 {k} 只（已持有的跌出前 {k + b} 名才换）。{source}。"]
            if last:
                info.append(f"排名和目标来自 {last['time']} 的{'试运行' if last.get('no_trade') else '运行'}"
                            f"（信号日 {last['signal'].get('date', '?')}）。")
                dip = last["signal"].get("dip")
                if dip:
                    if dip.get("triggered_today"):
                        state = "已触发"
                    elif dip["on"]:
                        state = f"维持中（{dip['last_trigger']} 触发，还剩 {dip['days_left']} 个交易日）"
                    else:
                        state = f"未触发（阈值 −{dip['threshold'] * 100:.0f}%）"
                    info.append(f"逢跌加仓：{dip['market']} 距 63 日高点 {dip['drawdown'] * 100:+.1f}%，{state}。")
            else:
                info.append(f"<span style='color:{ORANGE}'>还没有排名记录：下次 run.py 运行后出现，"
                            "也可以在 Gateway 登录后点“重新计算排名”马上生成（不下单）。</span>")
            if new and assets:
                info.append(f"<span style='color:{ORANGE}'>{'、'.join(new)} 是新加入的，"
                            "点“重新计算排名”可以马上看到它们的排名（不下单）。</span>")
            self.pool_info.setText(" ".join(info))

        # 饼图
        self.pie.removeAllSeries()
        series = QPieSeries()
        # 四类都列出来（为 0 的也在图例里显示 0%），一眼看出防守资产现在有没有持仓
        stock_value = sum((holdings[s]["value"] or 0) for s in holdings if s not in defense)
        parts = [("个股", stock_value, BLUE)]
        for d in defense:
            name, color = DEFENSE_LABELS.get(d, (d, ORANGE))
            parts.append((f"{name} {d}", (holdings.get(d) or {}).get("value") or 0, color))
        parts.append(("现金", usd_cash, GREY))
        total = sum(v for _, v, _ in parts) or 1.0
        for label, v, color in parts:
            sl = series.append(f"{label} {v / total * 100:.0f}%", max(v, 0))
            sl.setLabelVisible(False)
            sl.setColor(QColor(color))
            sl.setBorderColor(QApplication.palette().window().color())
        self.pie.addSeries(series)

    # ---------- 净值 ----------
    def refresh_nav(self) -> None:
        rows = records.load_nav(self.cfg["mode"])
        chart = self.nav_chart
        chart.removeAllSeries()
        for axis in chart.axes():
            chart.removeAxis(axis)
        if len(rows) < 2:
            self.nav_stats.setText("净值记录还不够两天")
            return
        navs = [float(r["nav"]) for r in rows]
        base = navs[0]
        nav_series = QLineSeries()
        nav_series.setName(f"账户净值（{rows[0]['currency']}）")
        peak, max_dd = navs[0], 0.0
        for r, v in zip(rows, navs):
            t = QDateTime.fromString(r["time"][:10], "yyyy-MM-dd")
            nav_series.append(t.toMSecsSinceEpoch(), (v / base - 1) * 100)
            peak = max(peak, v)
            max_dd = min(max_dd, v / peak - 1)
        series_list = [nav_series]
        spy_text = ""
        spy = (self.snap or {}).get("spy") or {}
        start = rows[0]["time"][:10]
        spy_days = sorted(d for d in spy if d >= start)
        if spy_days:
            spy_series = QLineSeries()
            spy_series.setName("SPY（USD）")
            spy_base = spy[spy_days[0]]
            for d in spy_days:
                spy_series.append(QDateTime.fromString(d, "yyyy-MM-dd").toMSecsSinceEpoch(), (spy[d] / spy_base - 1) * 100)
            series_list.append(spy_series)
            spy_text = f"；SPY 同期 {(spy[spy_days[-1]] / spy_base - 1) * 100:+.2f}%"
        x_axis = QDateTimeAxis()
        x_axis.setFormat("MM-dd")
        x_axis.setTickCount(5)
        y_axis = QValueAxis()
        y_axis.setLabelFormat("%.1f%%")
        y_axis.setTickCount(5)
        chart.addAxis(x_axis, Qt.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignLeft)
        for s in series_list:
            chart.addSeries(s)
            s.attachAxis(x_axis)
            s.attachAxis(y_axis)
        ys = [p.y() for s in series_list for p in s.points()]
        pad = max(0.2, (max(ys) - min(ys)) * 0.1)
        y_axis.setRange(min(ys) - pad, max(ys) + pad)
        y_axis.applyNiceNumbers()  # 刻度取整（如 -4、-2、0、2），不出现 -1.8 这种
        chart.setTitle("累计收益（净值 vs SPY）")
        self.nav_stats.setText(
            f"{start} 起：净值 {_money(base)} → {_money(navs[-1])} {rows[0]['currency']}，"
            f"累计 {(navs[-1] / base - 1) * 100:+.2f}%，最大回撤 {max_dd * 100:.2f}%{spy_text}"
            + ("" if spy_days else "（连上盈透后显示 SPY 对比）"))


def main() -> None:
    os.chdir(ROOT)
    if sys.platform == "win32":
        # 让任务栏把它当成独立的程序，显示自己的图标（否则显示成 Python 的图标）
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv)

    # 只允许开一个：已经在运行（比如缩在托盘里）时，再双击图标只是把那个窗口调出来
    probe = QLocalSocket()
    probe.connectToServer(APP_ID)
    if probe.waitForConnected(500):
        probe.write(b"show")
        probe.waitForBytesWritten(500)
        return
    QLocalServer.removeServer(APP_ID)  # 上次异常退出留下的残留
    server = QLocalServer()
    server.listen(APP_ID)

    apply_theme(app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    app.setQuitOnLastWindowClosed(False)  # 关窗口时缩到托盘，不退出
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(QIcon(str(ICON)))
    win = MainWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win.show_window()))
    win.show()
    sys.exit(app.exec())
