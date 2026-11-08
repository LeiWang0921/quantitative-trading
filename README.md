# quantitative trading

一个用来学习的自动交易框架：同一套代码可以在本地假券商、盈透模拟账户、盈透实盘之间切换，只改 `config.toml` 里的 `mode`。

## 结构

根目录下的 `.py` 是**你直接运行的脚本**；`autotrader/` 里是**被这些脚本调用的模块**，一般不直接运行。
项目分两块：**交易**（连盈透下单）和**研究**（只用历史数据，不会下单）。
每个文件的详细说明、每天的交易和回测是怎么一步步运行的，见 [docs/项目结构说明.md](docs/项目结构说明.md)。

```
config.toml              所有配置：运行模式、策略参数、回测参数、风控上限、研究用的标的

── 你运行的脚本：交易 ─────────────────────────────────────
run.py                   交易：取数据 → 算目标仓位 → 风控 → 下单（每个交易日收盘后运行一次）
backtest.py              回测 config.toml 里配置的策略，和“一直持有”“60/40”对比
candidate_charts.py      ★ 生成 docs/候选股票池.md：每个候选股一张 K 线图，阴影标出按当前规则持有的时间和仓位
tools/convert_fx.py      工具：在盈透账户里换汇（CAD ⇄ USD）
tools/daily_run.bat      Windows 计划任务每天调用它来运行 run.py（出问题时弹窗 + 发邮件提醒）
桌面图标“自动交易策略 - leiwang”  ★ 打开监控窗口（gui.py）：连接状态、运行环境、选股池与持仓、净值、日志、策略说明。只读，不下单。
                         只能开一个：缩在托盘时再双击图标会调出原来的窗口。start_gui.bat 效果相同
tools/setup.bat          新电脑第一次使用：创建 .venv、安装 requirements.txt、建立桌面和开始菜单图标
                         （在中国大陆用 setup.bat cn 走清华镜像）
tools/make_shortcut.py   重新建立桌面和开始菜单图标（项目文件夹搬家后运行一次）
tools/update_symbols.py  下载全部美股和 ETF 的代码、名称、行业、市值（纳斯达克公开数据），供“加入候选股”离线检索；几个月更新一次

── 你运行的脚本：数据和研究 ───────────────────────────────
fetch_data.py            从盈透下载复权日线到 data/history/（--research 下载研究用的 ETF）
fetch_macro.py           从 FRED、弗伦奇数据库下载宏观和 1926 年以来的美股数据到 data/macro/
research.py              ★ 防守型策略研究：全部实验从头跑一遍，结果写到 results/research/
notebooks/               用 Jupyter 画图查看结果
  defensive_research.ipynb ★ 防守型策略研究的全部图表
  backtest_results.ipynb   早期的均线策略回测曲线（已被取代，仅作记录）
  arma_forecast.ipynb      ARMA 涨跌预测（结论：预测不了）
docs/
  防守策略研究报告.md      ★ 研究报告：原理、规则、回测、证据、风险
  候选股票池.md            ★ 当前候选股的 K 线图和持仓阴影（candidate_charts.py 生成，图在 candidate_charts/）

── autotrader/ 模块：交易 ──────────────────────────────────
autotrader/strategy.py   ★ 实盘/模拟盘的策略（纯计算：收盘价 → 各标的目标权重），和回测共用同一套函数
autotrader/history.py    历史数据 CSV 的读写
autotrader/config.py     读取 config.toml、常用路径
autotrader/notify.py     邮件提醒：用本机 Outlook 发到 config.toml 的 [notify] email，不需要密码
autotrader/records.py    run.py 每次运行的记录（data/last_run.json、data/nav_history.csv），给窗口程序读
autotrader/gui/          窗口程序：app.py 界面、checks.py 运行环境检查、ibdata.py 只读取盈透数据、
                         config_edit.py 加入候选股、strategy_text.py 策略说明（数字从 config.toml 读）、
                         symbols.py 候选股离线检索、cn_names.py 约 700 家公司和常用 ETF 的中文名
autotrader/broker/
  base.py                券商接口（所有券商都要实现的方法）
  dryrun.py              本地假券商（随机价格，不联网）
  ibkr.py                盈透，经 IB Gateway 连接
tests/                   自动测试：下单流程（假券商）、策略计算、配置安全、提醒逻辑

── autotrader/research/ 模块：研究（只用于历史回测）───────
  data.py                读取和清洗数据，合成长期国债回报
  engine.py              多资产回测引擎（按目标权重调仓、成本、现金利息、成交时点）
  metrics.py             评价指标：最大回撤、危机期间表现、回本时间、自助法回撤概率
  strategies.py          策略函数；sleeves_core 是当前方案，defensive_core 是之前的指数方案
  models.py              训练的模型：波动率预测（GARCH/HAR）、危机分类器、隐马尔可夫模型
  forecast.py            ARMA 收益率预测

── 自动生成（不用手动改）──────────────────────────────────
data/history/SPY.csv     下载的历史日线（价格、成交量、历史/隐含波动率），可用 Excel 打开
data/macro/              FRED 和弗伦奇数据
data/dryrun_state.json   dryrun 的本地状态
data/last_run.json       最近一次 run.py 的决策：排名、打分、目标权重、持仓、下单情况
data/nav_history.csv     每次运行时的账户净值（窗口程序画净值曲线）
data/symbol_names.json   代码 → 公司名的缓存
data/us_symbols.csv      全部美股和 ETF 的代码、名称、行业、市值（tools/update_symbols.py 生成）
results/                 回测和研究的结果（CSV）
logs/trader.log          run.py 每次运行的日志
logs/scheduled_run.log   计划任务的运行记录（连接失败等错误也在这里）
```

## 运行

```bash
# 交易
.venv\Scripts\python run.py              # 按 config.toml 运行一次
.venv\Scripts\python run.py --no-trade   # 只看决策，不下单
.venv\Scripts\python run.py --reset      # 清空 dryrun 状态

# 改了 run.py、策略或 config.toml 之后跑一遍测试（不连盈透，约 2 秒）
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m autotrader.notify   # 发一封测试提醒邮件

# 简单回测（需要 IB Gateway 已登录才能下载；下载后回测不用联网）
.venv\Scripts\python fetch_data.py       # 下载当前策略全部标的的历史数据（25 年）
.venv\Scripts\python backtest.py         # 回测配置的策略并打印结果

# 防守型策略研究
.venv\Scripts\python fetch_data.py --research   # 下载研究用的 ETF（需要 IB Gateway，约 15 分钟）
.venv\Scripts\python fetch_macro.py             # 下载 FRED 和弗伦奇数据（不需要盈透）
.venv\Scripts\python research.py                # 跑全部实验（约 2 分钟），结果在 results/research/
# 然后打开 notebooks/defensive_research.ipynb 看图（内核选 .venv）
```

dryrun 模式下每运行一次相当于过了一个交易日。

## 当前策略和每日运行

`config.toml` 的 `strategy.kind` 选择运行哪个策略（代码 `autotrader/strategy.py`，原理见 `docs/防守策略研究报告.md`）。
**当前是 `"sleeves"`（2026-10-22 晚起）**：候选股排名 + 个股为主 + 美债/黄金防守。

```
候选股（N 只，在 config.toml 的 stocks 里，可随时增减）
    满额比例 = 趋势信号 × min(1, 20% ÷ 这只股票最近 20 日年化波动)
    趋势信号 = 价格在 100/150/200/250/300 日均线之上的加权比例（权重 4/19/19/49/9%），每条带 1% 缓冲
    每天按满额比例排名，持有前 7 只（已持有的跌出前 7 + N÷4 名才换），每只仓位 = 10% × 满额比例
    逢跌加仓：SPY 在 300 日均线之上、但比最近 63 日高点跌超 8% 时，仍在自己 300 日均线之上的股票按满额持有
防守（共 30%）：IEF、GLD 各 15%，再加上个股没用完的钱（平均分给两者），再乘 min(1, 15% ÷ 自己近 20 日波动)
    各自低于自己的 200 日均线时，那一份留作现金
```
- **增减候选股**：直接改 `config.toml` 的 `stocks` 列表，然后运行 `fetch_data.py` 和 `candidate_charts.py`（更新 `docs/候选股票池.md` 的持仓图）。持有数量固定为 7，每只的满额不会被摊薄。

- **满仓 100%** = 美元现金 + 策略里各标的的持仓市值，再留 `cash_reserve`（2%）现金不投；按整股向下取整。
- **再平衡**：只调整实际占比和目标相差超过 3 个百分点的标的（`rebalance_band`、`drift_only`），其余不动。
  目标低于 3% 的标的不会从 0 开始建仓。回测（`backtest.py`）用的是同一个规则。
- **切换策略时的旧持仓**：`exit_symbols` 里不属于当前策略的标的会被清仓（例如切换时卖掉 SPY）。
- **现金账户**：收盘后提交的卖单要到第二天开盘才成交，钱到账前不能用来买入；买不够的部分下一次运行再买。
- 之前的 `"spy_ief"` 方案（SPY + 美债，带申请失业金过滤）的参数保留在 `[strategy.spy_ief]`，改 `kind` 就能切回。

- **成交**：收盘后运行，市价单在下一个交易日开盘成交；再次运行会把挂单计入，不会重复下单。
- **每日自动运行**：Windows 计划任务 `QuantTrading-DailyRun` 每周一到周五 14:45 和 20:00（温哥华时间，美股收盘后）
  调用 `tools\daily_run.bat`（只含英文，cmd 读中文会出错），它再调用 `tools\daily_run.py`：写日志、运行 run.py、失败时弹出提醒。
  - 邮件提醒（`[notify] email`）：运行失败、有订单被风控拒绝、失败后补跑成功时各发一封，附运行输出的最后 40 行。
    用本机 Outlook 桌面版发送；手机装 Outlook App 就能收到推送。电脑关机或睡眠时任务不会运行，也就不会有邮件。
  20:00 是补跑：同一天运行两次不会重复下单。手动测试（不下单）：`tools\daily_run.bat --no-trade`。
  - IB Gateway 要设置成“自动重启”（配置 → 设置 → 锁定和退出），否则每天会自动退出；盈透要求每周手动完整登录一次。
  - 暂停：任务计划程序里右键该任务 → 禁用；或 PowerShell：`Disable-ScheduledTask -TaskName QuantTrading-DailyRun`
  - 删除：`Unregister-ScheduledTask -TaskName QuantTrading-DailyRun`
  - 那天电脑关机就跳过，第二天会把仓位调到当天的目标。

## 切换到盈透模拟账户

1. 打开 IB Gateway，用**模拟账户**登录。
2. Gateway 里 Configure → Settings → API → Settings：
   勾选 Enable ActiveX and Socket Clients，端口 4002，Trusted IPs 加 127.0.0.1，
   要下单时取消 Read-Only API。
3. `config.toml` 里把 `mode` 改成 `"paper"`，先跑 `--no-trade` 确认能取到数据。

注意：现金账户买美股需要账户里有 USD 现金。模拟账户的虚拟资金是 CAD（和实盘的基础货币一致），
如果日志提示“美元现金不足”，用 `tools/convert_fx.py` 换汇，例如 `.venv\Scripts\python tools\convert_fx.py 14000`
（用 CAD 买入 14000 USD；低于 25000 USD 会按零股成交，价格略差）。

实盘账户的用户名同一时间只能在一处使用行情。如果报错 162
“Trading TWS session is connected from a different IP address”，退出网页版或手机 App 的实盘登录。

收盘后运行时，市价单会挂到下一个交易日开盘才成交；再次运行时会把挂单计入，不会重复下单。

## 安全措施

- `mode = "live"` 必须同时把 `risk.allow_live` 改成 `true` 才会运行。
- 如果 mode 不是 live 却连到了实盘账户（账户号不以 D 开头），立即中止。
- 单笔股数、单笔金额都有上限；买入前检查美元现金。
- 不做空：卖出股数不能超过持仓减去已挂的卖单（还没成交的买单不算）。
- `tests/` 里的测试覆盖以上各条，另外检查当前 config.toml 没有打开实盘。
- 账号密码只在 IB Gateway 里输入，不写进任何文件。
