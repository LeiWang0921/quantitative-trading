"""出问题时发邮件提醒（tools/daily_run.py 用），收件地址在 config.toml 的 [notify] email，留空就不发。

两种发送方式，自动选择：
  1. Microsoft Graph（微软官方接口）：本机有登录令牌 data/ms_graph_token.json 时用它。不需要安装 Outlook，
     也不保存密码：用私人 Outlook 账号在浏览器里授权一次（设备验证码），令牌只存在本机的 data/（不上传 GitHub）。
     china-pc 用这种（没有装 Outlook）。
  2. 本机已登录的经典版 Outlook 桌面版（COM 接口）：没有登录令牌时用它。
手机上装 Outlook App 并登录同一个邮箱，就能收到推送。电脑关机、睡眠或者没有登录 Windows 时，计划任务本身不会运行，
也就发不出邮件。

  .venv\\Scripts\\python -m autotrader.notify           发一封测试邮件
  .venv\\Scripts\\python -m autotrader.notify --login   用 Microsoft Graph 登录（第一次，或令牌失效后）

令牌保活：Microsoft 的登录令牌大约 90 天不用就会失效，而提醒邮件只在出问题时才发。所以 daily_run.py 每次运行都调用
keep_alive() 刷新一次（不发邮件），保证真正需要发邮件时令牌仍然有效。
"""
from __future__ import annotations

import base64
import subprocess
import sys
from datetime import datetime

from .config import DATA_DIR, load_config

# Microsoft Graph：用微软自己给脚本用的公共客户端“Microsoft Graph Command Line Tools”，不需要自己注册应用；
# consumers = 个人 Microsoft 账号（outlook.com / hotmail.com）；权限只申请“代表你发送邮件”
GRAPH_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
GRAPH_AUTHORITY = "https://login.microsoftonline.com/consumers"
GRAPH_SCOPES = ["Mail.Send"]
TOKEN_CACHE = DATA_DIR / "ms_graph_token.json"

# 用 PowerShell 调 Outlook 的 COM 接口（不用额外安装 pywin32）。
# 收件人、标题、正文用 base64 传进去，避免引号和中文出问题。
# Send() 只是把邮件放进发件箱：再触发一次收发，等它离开发件箱才算发出（最多等 60 秒）
_PS = r"""
$ErrorActionPreference = 'Stop'
function arg($b64) {{ [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) }}
$to = arg '{to}'; $subject = arg '{subject}'; $body = arg '{body}'
$outlook = New-Object -ComObject Outlook.Application
$ns = $outlook.GetNamespace('MAPI')
$mail = $outlook.CreateItem(0)
$mail.To = $to
$mail.Subject = $subject
$mail.Body = $body
foreach ($a in $ns.Accounts) {{
    if ($a.SmtpAddress -ieq $to) {{   # 用同一个邮箱发给自己；找不到就用 Outlook 的默认账户
        [void][System.__ComObject].InvokeMember('SendUsingAccount', [Reflection.BindingFlags]::SetProperty, $null, $mail, @($a))
    }}
}}
$mail.Send()
$ns.SendAndReceive($false)
$outbox = $ns.GetDefaultFolder(4)
for ($i = 0; $i -lt 60; $i++) {{
    Start-Sleep -Seconds 1
    if (@($outbox.Items | Where-Object {{ $_.Subject -eq $subject }}).Count -eq 0) {{ 'sent'; exit 0 }}
}}
'queued'
"""


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


def recipient(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("notify", {}).get("email", "").strip()


def method() -> str:
    """当前会用哪种方式发信："graph"（有登录令牌）或 "outlook"。"""
    return "graph" if TOKEN_CACHE.exists() else "outlook"


# ---------- Microsoft Graph ----------
def _graph_app():
    import msal  # 只有用 Graph 时才需要（requirements.txt 里有）
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE.exists():
        cache.deserialize(TOKEN_CACHE.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(GRAPH_CLIENT_ID, authority=GRAPH_AUTHORITY, token_cache=cache)
    return app, cache


def _save_cache(cache) -> None:
    if cache.has_state_changed:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TOKEN_CACHE.with_suffix(".tmp")
        tmp.write_text(cache.serialize(), encoding="utf-8")
        tmp.replace(TOKEN_CACHE)


def _graph_token() -> tuple[str | None, str]:
    """(access token, 说明)。令牌失效时返回 (None, 原因)，需要重新 --login。"""
    try:
        app, cache = _graph_app()
        accounts = app.get_accounts()
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0]) if accounts else None
        _save_cache(cache)  # 刷新令牌时会换一个新的 refresh token，要存下来
    except Exception as e:  # 网络、文件损坏、没装 msal……
        return None, f"Microsoft 登录令牌读取或刷新失败：{e}"
    if not result or "access_token" not in result:
        why = (result or {}).get("error_description", "") if result else ""
        return None, ("Microsoft 登录已失效，请运行 .venv\\Scripts\\python -m autotrader.notify --login 重新登录"
                      + (f"（{why[:200]}）" if why else ""))
    return result["access_token"], f"Microsoft Graph（{accounts[0].get('username', '')}）"


def keep_alive() -> tuple[bool, str]:
    """刷新 Microsoft 登录令牌（不发邮件）。没有使用 Graph 时什么都不做。"""
    if method() != "graph":
        return True, "不使用 Microsoft Graph，无需刷新"
    token, detail = _graph_token()
    return token is not None, detail if token is None else f"登录令牌有效：{detail}"


def _send_graph(to: str, subject: str, body: str) -> tuple[bool, str]:
    token, detail = _graph_token()
    if token is None:
        return False, detail
    import requests
    msg = {"message": {"subject": subject, "body": {"contentType": "Text", "content": body},
                       "toRecipients": [{"emailAddress": {"address": to}}]}, "saveToSentItems": True}
    try:
        r = requests.post("https://graph.microsoft.com/v1.0/me/sendMail", json=msg, timeout=30,
                          headers={"Authorization": f"Bearer {token}"})
    except Exception as e:
        return False, f"Microsoft Graph 发送失败：{e}"
    if r.status_code == 202:  # 202 Accepted = 微软已接收，随后投递
        return True, f"已通过 Microsoft Graph 发送到 {to}"
    return False, f"Microsoft Graph 发送失败：HTTP {r.status_code} {r.text[:300]}"


def login() -> int:
    """设备验证码登录：在任意设备的浏览器打开网址、输入验证码，用私人 Outlook 账号登录并同意“代表你发送邮件”。"""
    app, cache = _graph_app()
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "user_code" not in flow:
        print(f"无法开始登录：{flow.get('error_description', flow)}")
        return 1
    print(f"请在浏览器打开 {flow['verification_uri']} ，输入验证码 {flow['user_code']} ，"
          "用收提醒的 Outlook 账号登录并同意权限（约 15 分钟内有效）……", flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print(f"登录失败：{result.get('error_description', result)}")
        return 1
    _save_cache(cache)
    print(f"登录成功：{result.get('id_token_claims', {}).get('preferred_username', '')}，令牌已保存到 {TOKEN_CACHE}")
    return 0


# ---------- Outlook 桌面版 ----------
def _send_outlook(to: str, subject: str, body: str) -> tuple[bool, str]:
    script = _PS.format(to=_b64(to), subject=_b64(subject), body=_b64(body))
    encoded = base64.b64encode(("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script).encode("utf-16-le"))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded.decode()],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"调用 Outlook 失败：{e}"
    result = out.stdout.strip().splitlines()[-1:] or [""]
    if out.returncode == 0 and result[0] == "sent":
        return True, f"已发送到 {to}"
    if out.returncode == 0 and result[0] == "queued":
        return True, f"邮件在 Outlook 发件箱里，Outlook 下次联网同步时发出（{to}）"
    return False, f"Outlook 发送失败：{(out.stderr or out.stdout).strip()[:500]}"


def send_email(subject: str, body: str, cfg: dict | None = None) -> tuple[bool, str]:
    """发一封邮件。返回 (是否成功, 说明)。不会抛异常：提醒失败不应影响交易流程。"""
    to = recipient(cfg)
    if not to:
        return False, "config.toml 的 [notify] email 为空，不发邮件"
    # 标题加上时间：同一天可能发好几封，也用来在 Outlook 发件箱里认出这一封
    subject = f"{subject}（{datetime.now():%m-%d %H:%M:%S}）"
    try:
        return _send_graph(to, subject, body) if method() == "graph" else _send_outlook(to, subject, body)
    except Exception as e:
        return False, f"发送邮件时出错：{e}"


if __name__ == "__main__":
    if "--login" in sys.argv[1:]:
        sys.exit(login())
    ok, detail = send_email("量化交易：测试邮件", "这是一封测试邮件。收到说明运行失败时的邮件提醒可以正常工作。\n\n"
                            f"（由 python -m autotrader.notify 发送，方式：{method()}）")
    print(detail)
    sys.exit(0 if ok else 1)
