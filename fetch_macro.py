"""下载宏观和长期市场数据（不需要盈透），存到 data/macro/。

用法：
  python fetch_macro.py

下载内容：
  1. FRED（美联储圣路易斯分行的公开经济数据库，https://fred.stlouisfed.org），每个序列一个 CSV：
       DTB3       3 个月国库券利率（%，年化）——现金的利息，1954 年起
       DGS10      10 年期国债收益率（%）——用来合成 2004 年以前的国债回报，1962 年起
       DGS2       2 年期国债收益率（%），1976 年起
       T10Y3M     期限利差：10 年期减 3 个月（%）——为负（收益率倒挂）常被视为衰退信号，1982 年起
       BAA10Y     信用利差：Baa 级公司债减 10 年期国债（%）——经济紧张时变大，1986 年起
       VIXCLS     VIX 恐慌指数：标普 500 期权隐含的未来 30 天波动率（%），1990 年起
       USEPUINDXD 经济政策不确定性指数（每日）：统计约 1000 家美国报纸里同时出现“经济”“政策”“不确定”
                  相关词的文章比例，1985 年起（Baker、Bloom、Davis）
       UMCSENT    密歇根大学消费者信心指数（每月调查约 600 人），1952 年起
       EMVOVERALLEMV  股市波动新闻追踪（每月）：报纸里谈论股市波动的文章比例，1985 年起
       UNRATE     失业率（%，每月），1948 年起；每月第一个周五公布上个月的数据
       ICSA       每周首次申请失业救济人数，1967 年起；周四公布上一周（截至周六）的数据
       SAHMREALTIME  萨姆衰退指标（实时版）：失业率 3 个月均值比过去 12 个月最低点高出多少个百分点，
                  ≥ 0.5 历史上意味着衰退已经开始；“实时”指每个时点只用当时已公布的数据，1959 年起
  2. 旧金山联储每日新闻情绪指数（news_sentiment.csv）：用固定词典给约 20 家美国报纸的经济类文章
     打“正面/负面”分，按天汇总，1980 年起（Shapiro、Sudhof、Wilson）
  3. 肯·弗伦奇数据库（Kenneth French Data Library，学术界研究股市最常用的数据源）：
       french_daily.csv：美国全市场（纽交所+美交所+纳斯达克，按市值加权）的日收益率（含分红）
       和 1 个月国库券日收益率，1926 年 7 月起。
"""
from __future__ import annotations

import io
import urllib.request
import zipfile

import pandas as pd

from autotrader.config import MACRO_DIR, load_config
from autotrader.research.data import download_fred

SF_NEWS_URL = "https://www.frbsf.org/wp-content/uploads/news_sentiment_data.xlsx"
FRENCH_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
              "F-F_Research_Data_Factors_daily_CSV.zip")


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def fetch_fred(series_id: str) -> None:
    df = download_fred(series_id)
    print(f"FRED {series_id}: {len(df)} 行 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")


def fetch_french() -> None:
    with zipfile.ZipFile(io.BytesIO(download(FRENCH_URL))) as z:
        text = z.read(z.namelist()[0]).decode("latin-1")
    # 文件开头和结尾有说明文字，只保留“8 位日期,数字,...”格式的数据行
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5 and len(parts[0]) == 8 and parts[0].isdigit():
            rows.append(parts)
    df = pd.DataFrame(rows, columns=["date", "mkt_rf", "smb", "hml", "rf"])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    for col in ["mkt_rf", "smb", "hml", "rf"]:
        df[col] = df[col].astype(float) / 100  # 原始单位是 %
    df["mkt"] = df["mkt_rf"] + df["rf"]  # 市场总收益 = 超额收益 + 无风险收益
    df[["date", "mkt", "rf"]].to_csv(MACRO_DIR / "french_daily.csv", index=False)
    print(f"French 全市场日收益: {len(df)} 行 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")


def fetch_sf_news() -> None:
    df = pd.read_excel(io.BytesIO(download(SF_NEWS_URL)), sheet_name="Data")
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df.dropna()
    df.to_csv(MACRO_DIR / "news_sentiment.csv", index=False)
    print(f"旧金山联储新闻情绪: {len(df)} 行 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")


def main() -> None:
    cfg = load_config()
    MACRO_DIR.mkdir(parents=True, exist_ok=True)
    for series_id in cfg["research"]["fred"]:
        fetch_fred(series_id)
    fetch_sf_news()
    fetch_french()


if __name__ == "__main__":
    main()
