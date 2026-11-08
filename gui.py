"""窗口程序：监控 IB Gateway 连接、账户、选股池与持仓、净值、日志和策略说明。只读，不下单。

启动：双击 start_gui.bat（不带黑色命令行窗口），或 .venv\\Scripts\\python gui.py
每天的交易仍由计划任务 QuantTrading-DailyRun 运行 run.py，和这个窗口开没开无关。
"""
from autotrader.gui.app import main

if __name__ == "__main__":
    main()
