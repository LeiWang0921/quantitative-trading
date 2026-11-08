# Setup guide

This guide is for anyone who has downloaded this repository and wants to run it on their own Windows PC: what to install, how to configure it, and how to run it every day.

> **Read this first**
> - This is a personal learning project, not investment advice. It places real orders when connected to a real account. Run it on a paper (simulated) account first, and use it at your own risk.
> - Code comments, log messages, the monitoring window and the other documents in `docs/` are written in Chinese. This guide is the only English document.
> - The strategy settings in `config.toml` (the stock list in particular) are the author's own choices, not recommendations. Change them to your own.

## Contents

1. [Requirements](#1-requirements)
2. [Download](#2-download)
3. [Install](#3-install)
4. [Try it without an account (dryrun mode)](#4-try-it-without-an-account-dryrun-mode)
5. [Set up Interactive Brokers and IB Gateway](#5-set-up-interactive-brokers-and-ib-gateway)
6. [Configure `config.toml`](#6-configure-configtoml)
7. [First run on the paper account](#7-first-run-on-the-paper-account)
8. [The monitoring window](#8-the-monitoring-window)
9. [Run it automatically every trading day](#9-run-it-automatically-every-trading-day)
10. [Email alerts](#10-email-alerts)
11. [Tests](#11-tests)
12. [Switching to a live account](#12-switching-to-a-live-account)
13. [Troubleshooting](#13-troubleshooting)
14. [Where things are stored](#14-where-things-are-stored)

## 1. Requirements

| What | Needed for | Notes |
|---|---|---|
| Windows 10 or 11 | everything | The scripts, the scheduled task, the desktop shortcut and the email alerts are Windows-specific. |
| Python 3.11 or newer | everything | From <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"** during installation. |
| An Interactive Brokers (IBKR) account | paper and live trading | A paper account is enough to start. Not needed for dryrun mode. |
| IB Gateway | paper and live trading | IBKR's lightweight API program. TWS also works, but uses different ports (see section 5). |
| US stock market data in your IBKR account | paper and live trading | The program downloads daily price history from IBKR on every run. |
| Classic Outlook desktop app | email alerts (optional) | Must be signed in. The "new Outlook" app does not work, because it has no COM interface. |
| Git | optional | Only needed if you want to download with `git clone` and update with `git pull`. |

Python packages (installed automatically in step 3) are listed in `requirements.txt`: `ib_async` for the IBKR connection, `PySide6` for the monitoring window, and pandas, matplotlib, statsmodels, scikit-learn, arch, openpyxl and ipykernel for research and backtests.

## 2. Download

You do not need a GitHub account. Use any one of these:

- **Web page**: open <https://github.com/LeiWang0921/quantitative-trading>, click the green **Code** button, then **Download ZIP**, and unzip it.
- **Git** (lets you update later with `git pull`):

  ```bash
  git clone https://github.com/LeiWang0921/quantitative-trading.git
  ```

- **Command line without Git** (`curl` is built into Windows 10 and later):

  ```bash
  curl -L -o quantitative-trading.zip https://github.com/LeiWang0921/quantitative-trading/archive/refs/heads/main.zip
  ```

Put the folder somewhere your user account can write to, for example `C:\quantitative trading` or a folder under your user profile. **Do not put it under `C:\Program Files`**: Windows blocks writing there, and the program writes data and log files inside its own folder. Spaces in the path are fine.

The repository does not include price data, logs or the Python environment. The steps below create them.

## 3. Install

1. Install Python 3.11+ (see section 1).
2. Double-click **`tools\setup.bat`**, or run it from a terminal opened in the project folder:

   ```bash
   tools\setup.bat
   ```

   In mainland China, use the Tsinghua package mirror instead, which is much faster there:

   ```bash
   tools\setup.bat cn
   ```

The script:
1. Checks that Python 3.11+ is available.
2. Creates a virtual environment in `.venv\`.
3. Installs `requirements.txt` into it.
4. Creates a shortcut named **"自动交易策略 - leiwang"** (the monitoring window) on the desktop, in the Start menu and in the project folder.

Everything is installed inside the project folder. Nothing is installed system-wide apart from Python itself.

**Manual installation** (what `setup.bat` does), if you prefer:

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools\make_shortcut.py
```

All commands in this guide are run from the project folder and use the Python inside `.venv`. If you move the project folder later, run `.venv\Scripts\python tools\make_shortcut.py` again so the shortcuts point to the new location.

## 4. Try it without an account (dryrun mode)

`dryrun` mode uses a fake local broker with randomly generated prices. It needs no account and no internet connection, and it is a good way to see the whole flow (signal → target weights → share counts → risk checks → orders).

1. Open `config.toml` and set:

   ```toml
   mode = "dryrun"
   ```

2. Show the decision without placing any orders:

   ```bash
   .venv\Scripts\python run.py --no-trade
   ```

3. Run it for real against the fake broker. Each run counts as one trading day, and positions carry over to the next run:

   ```bash
   .venv\Scripts\python run.py
   ```

4. Reset the fake account:

   ```bash
   .venv\Scripts\python run.py --reset
   ```

Set `mode` back to `"paper"` before continuing.

## 5. Set up Interactive Brokers and IB Gateway

### 5.1 Account

- Open an IBKR account. Stock trading permission is all this strategy needs: it only buys and sells US stocks and ETFs and never sells short.
- After the account is approved, create a **paper trading account**: in IBKR's Client Portal, go to Settings → Account Settings → Paper Trading Account. It has its own username and password.
- A paper account normally shares the market data subscriptions of your live account. If historical data requests fail with a market data permission error, check your subscriptions in Client Portal (Settings → Market Data Subscriptions).

### 5.2 IB Gateway

1. Download IB Gateway from IBKR's website (search for "IB Gateway download"). Either the stable or the latest version works.
2. Start it, choose **IB API** and **Paper Trading**, and log in with your **paper** username.
3. Go to **Configure → Settings → API → Settings**:
   - Tick **Enable ActiveX and Socket Clients**.
   - **Socket port**: `4002` for paper, `4001` for live (IB Gateway defaults). TWS uses `7497` (paper) and `7496` (live) instead; if you use TWS, change the ports in `config.toml` under `[ibkr]`.
   - **Trusted IPs**: add `127.0.0.1`.
   - Untick **Read-Only API**. Only `run.py` and `tools\convert_fx.py` need this, because they place orders. The monitoring window and `fetch_data.py` always connect read-only.
4. Go to **Configure → Settings → Lock and Exit** and choose **Auto restart**. Otherwise IB Gateway logs off once a day and the scheduled runs fail.

Things to know:
- **Weekly login**: IBKR still requires a full login, with two-factor authentication, about once a week. Until you log in again, runs fail. If email alerts are set up (section 10), you get an email when this happens.
- **One session per username**: if the same IBKR username is logged in somewhere else at the same time (Client Portal in a browser, the mobile app, or TWS), IBKR may give the market data to that session. Historical data requests then fail with **error 162**. Log out of the other session before the scheduled runs.
- **Client IDs**: the programs connect with these API client IDs: `1` (`run.py`), `11` (`tools\convert_fx.py`), `21` (`fetch_data.py`), `41` (the monitoring window). Do not use the same IDs in other programs connected to the same IB Gateway.

## 6. Configure `config.toml`

All settings are in `config.toml`. The comments in it are in Chinese; the important keys are:

| Key | What it does | What to set |
|---|---|---|
| `mode` | `"dryrun"` (fake broker), `"paper"` (IBKR paper account), `"live"` (real money) | `"paper"` to start. |
| `[ibkr]` `paper_port`, `live_port` | API ports | Leave as is for IB Gateway; change for TWS (section 5.2). |
| `[strategy]` `kind` | Which strategy runs | `"sleeves"` is the current strategy. |
| `[strategy.sleeves]` `stocks` | Candidate stocks (US tickers). Each day the strategy ranks them and holds up to `max_holdings` of them. | **Your own list.** The author's list is not a recommendation. |
| `[strategy.sleeves]` `defense` | Defensive assets (Treasuries `IEF`, gold `GLD`) | Leave as is unless you have a reason. |
| `[strategy]` `exit_symbols` | Old holdings to sell off completely when you switch strategies | Usually leave empty for a new account. |
| `[risk]` `max_shares_per_order`, `max_order_value_usd` | Hard limits per order. Orders above them are rejected and logged. | **Size these to your account.** The defaults suit a paper account of about 700,000 USD. |
| `[risk]` `allow_live` | Master switch for live trading | Keep `false` until you deliberately go live (section 12). |
| `[notify]` `email` | Where alert emails go | **Change this to your own address**, or set it to `""` to turn alerts off. The default is the author's address. |
| `[backtest]` `years`, `cost_bps` | History length and trading cost used by `backtest.py` | Optional. |

How the strategy works (in short):
- **Stocks, about 70% in total.** Each candidate gets a trend score, the share of its 100- to 300-day moving averages that the price is above. The score is scaled down when the stock's recent volatility is high. The candidates are ranked by this score and the top 7 are held.
- **Defense, about 30% plus anything the stocks don't use.** The defense money goes to IEF and GLD, but only while each one is above its own 200-day moving average. Otherwise that part stays in cash.
- **Rebalancing** happens only for positions more than 3 percentage points away from their target.
- **Full description**: `docs/防守策略研究报告.md` (in Chinese), and the 策略说明 (strategy description) tab of the monitoring window.

**USD cash.** IBKR paper accounts are funded in your base currency (for example CAD), and a cash account needs USD to buy US stocks. If the log shows "现金只够买 N 股" (cash only enough for N shares) and you have no sells pending, convert some with:

```bash
.venv\Scripts\python tools\convert_fx.py 14000
```

This buys 14,000 USD with your base currency. Amounts below 25,000 USD fill as an odd lot at a slightly worse rate. `tools\convert_fx.py -- -5000` converts 5,000 USD back.

## 7. First run on the paper account

With IB Gateway logged in to the paper account and `mode = "paper"`:

1. Look at the decision without trading:

   ```bash
   .venv\Scripts\python run.py --no-trade
   ```

   It downloads about 800 days of daily prices for each symbol from IBKR, prints each candidate's rank, score and target weight, then prints the share difference for every position. Nothing is sent to IBKR. Every run is also logged to `logs\trader.log`.

2. If the output looks right, run it for real:

   ```bash
   .venv\Scripts\python run.py
   ```

How orders behave:
- The program is meant to run **after the US market closes** (16:00 New York time).
- It sends market orders, which wait until the next trading day's open and fill there. This matches the backtest's assumption.
- Running it again before the open does not place duplicate orders, because pending orders are counted as already held.
- Sells are placed before buys. A buy is reduced if there is not enough USD cash; with a cash account, the money from a sell is only available after it fills, so the rest is bought on the next run.

Optional, for backtests and research only (`run.py` does not need these files):

```bash
.venv\Scripts\python fetch_data.py
.venv\Scripts\python backtest.py
```

`fetch_data.py` downloads 25 years of history for the current strategy's symbols into `data\history\`. `backtest.py` then runs the strategy on that history.

## 8. The monitoring window

Double-click the **"自动交易策略 - leiwang"** shortcut (or `start_gui.bat`). The window is **read-only**: it never places orders. It has four tabs:

- **状态 (Status)**: the IB Gateway connection, account value, and a checklist of the environment: Python, `.venv`, packages, config, IB Gateway installed/running, API port, scheduled task, data folder. Anything missing is shown with a fix. **Use it to check your setup.**
- **选股池与持仓 (Pool and holdings)**: today's ranking and holdings; an allocation pie chart (stocks / Treasuries / gold / cash); the account value curve compared with SPY. You can add a candidate stock here. The window checks the ticker with IBKR, asks you to confirm, then edits `config.toml`.
- **日志 (Log)**: the latest log lines.
- **策略说明 (Strategy)**: a description of the strategy, with numbers taken from `config.toml`.

Notes:
- **Ticker search**: to search for tickers by name when adding a candidate, download the US symbol list once. It comes from Nasdaq's public symbol directory, about 3 MB, and does not use IBKR:

  ```bash
  .venv\Scripts\python tools\update_symbols.py
  ```

- **Closing**: closing the window hides it to the system tray. Quit from the tray icon's menu.
- **Single instance**: double-clicking the shortcut again brings back the existing window.

## 9. Run it automatically every trading day

The daily run uses Windows Task Scheduler. It calls `tools\daily_run.bat`, which runs `run.py`, appends the output to `logs\scheduled_run.log`, shows a pop-up if the run failed, and sends alert emails (section 10).

**Choosing the times.** Pick two times in **your PC's local time zone**, both after the US close (16:00 New York time) and before the next open (09:30 New York time). Pick them on the local days that follow each US trading day. The second run is a retry in case the first one failed, for example because IB Gateway was logged out; when the first run succeeded, the second places no new orders. For example:

| PC time zone | Days | First run | Retry |
|---|---|---|---|
| Pacific (Vancouver, Los Angeles) | Monday–Friday | 14:45 | 20:00 |
| Eastern (Toronto, New York) | Monday–Friday | 16:45 | 21:00 |
| China (UTC+8) | **Tuesday–Saturday** (the US Friday close is Saturday morning in China) | 06:00 | 08:00 |

For China, change `$days` below to `"Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"`.

Running a few minutes after the close is fine: the program waits for the day's final bar, and before 16:15 New York time it uses the previous close.

**Creating the task.** Open PowerShell (not as administrator), change `$dir` and the times, and run:

```powershell
$dir = "C:\quantitative trading"
$days = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$action = New-ScheduledTaskAction -Execute "$dir\tools\daily_run.bat" -WorkingDirectory $dir
$triggers = @(
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "14:45"
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "20:00"
)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "QuantTrading-DailyRun" -Action $action -Trigger $triggers -Settings $settings
```

Check it with a manual run that does not trade, then read `logs\scheduled_run.log`:

```bash
tools\daily_run.bat --no-trade
```

**Requirements and management.**
- **What must be on**: the task runs only while you are logged in to Windows, and the PC must be on and awake, with IB Gateway logged in. If the PC is off on a day, that day is skipped; the next run moves the positions to that day's targets.
- **Pause**: `Disable-ScheduledTask -TaskName QuantTrading-DailyRun`. Resume with `Enable-ScheduledTask`.
- **Remove**: `Unregister-ScheduledTask -TaskName QuantTrading-DailyRun`.
- **Keep `.bat` files English-only**: `cmd` misreads non-ASCII text in them, so do not add Chinese or other non-ASCII text to them.

## 10. Email alerts

`tools\daily_run.py` sends an email:
- when a run fails, for example because IB Gateway is not logged in;
- when an order is rejected by the risk checks;
- when a run succeeds after a failure.

Each email includes the last 40 lines of that run's output.

The email is sent **through the classic Outlook desktop app on the same PC**, so no password is stored anywhere. To set it up:

1. Sign in to your email account in classic Outlook.
2. Set your address in `config.toml`. If this address is one of the accounts in Outlook, the email is sent from that account; otherwise Outlook's default account is used.

   ```toml
   [notify]
   email = "you@example.com"
   ```

3. Send a test email:

   ```bash
   .venv\Scripts\python -m autotrader.notify
   ```

To get notifications on your phone, install the Outlook app (or any mail app) there with the same account. Set `email = ""` to turn alerts off. Alerts cannot tell you about a PC that is off or asleep, because then the task does not run at all.

## 11. Tests

The `tests\` folder checks the order logic, using a fake broker:
- **Order sizing**: target share counts, cash limits, sells before buys, the rebalance band.
- **Safety**: no duplicate orders, the risk limits, no short selling, refusing to run when a live account is connected in paper mode.
- **Strategy and config**: strategy weights, and that `config.toml` does not have live trading switched on.
- **Alerts**: when emails are sent.

The tests do not connect to IBKR and take about 2 seconds. Run them after changing code or `config.toml`:

```bash
.venv\Scripts\python -m unittest discover -s tests
```

## 12. Switching to a live account

Run on paper long enough to trust the program first.

1. **Re-set the order limits.** Set `[risk]` `max_shares_per_order` and `max_order_value_usd` for the live account's size.
2. **Switch the mode.** In `config.toml`, set `mode = "live"` **and** `[risk]` `allow_live = true`. The program refuses to run in live mode unless both are set.
3. **Log in to live.** Log in to IB Gateway with your **live** username (API port `4001`), and apply the API settings from section 5.2 there as well.
4. **Check with `--no-trade` first.**

Built-in protection: if `mode` is not `"live"` but the connected account turns out to be a live account, the program stops before placing any order. The test suite also fails while `allow_live` is `true` or `mode` is `"live"`. Those two tests are a reminder; edit them deliberately if you really go live.

## 13. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `setup.bat` says Python 3.11 was not found | Install Python from python.org with "Add python.exe to PATH" ticked, then open a new terminal. |
| Connection refused / timeout | IB Gateway is not running or not logged in, the API is not enabled, or the port in `config.toml` does not match (section 5.2). |
| No historical data, or error 162 | The same IBKR username is logged in elsewhere (browser, mobile app, TWS). Log out there and run again. Could also be missing market data permissions (section 5.1). |
| Error 10358 | Fundamentals data is not available for the account. The strategy does not need it. |
| "现金只够买 N 股" (cash only enough for N shares) | Normal on a day with sells: the money from a sell is only available after it fills, so the rest is bought on the next run. If it keeps happening with no sells, the account needs more USD: convert with `tools\convert_fx.py` (section 6). |
| "拒单" (order rejected) in the log | An order exceeded `max_shares_per_order` / `max_order_value_usd`, or would have sold more shares than held. Check `[risk]`. |
| "连接到的是实盘账户，但 mode 不是 live" (connected to a live account but mode is not live) | IB Gateway is logged in to the live account while `mode = "paper"`. Log in with the paper username. |
| Scheduled run did not happen | The PC was off/asleep or you were not logged in to Windows. Check Task Scheduler's "Last Run Result" and `logs\scheduled_run.log`. |
| Test email fails | Classic Outlook is not installed or not signed in (the "new Outlook" app is not supported). |
| Shortcut does nothing after moving the folder | Run `.venv\Scripts\python tools\make_shortcut.py` again. |

## 14. Where things are stored

None of these are in the repository; they are created on your PC as the program runs.

| Path | Contents |
|---|---|
| `.venv\` | Python environment created by `setup.bat` |
| `data\history\` | Price history downloaded by `fetch_data.py` (CSV) |
| `data\last_run.json` | The latest run's full decision record (shown in the monitoring window) |
| `data\nav_history.csv` | Account value at every run (the monitoring window's chart) |
| `data\us_symbols.csv` | US symbol list from `tools\update_symbols.py` |
| `logs\trader.log` | Log of every `run.py` run |
| `logs\scheduled_run.log` | Scheduled runs, including errors and alert-email results |
| `results\` | Backtest and research output |

Back up `data\` and `logs\` if you want to keep your trading history. For a description of every file in the project, see `README.md` and `docs/项目结构说明.md` (both in Chinese).
