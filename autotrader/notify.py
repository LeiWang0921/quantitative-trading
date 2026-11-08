"""出问题时发邮件提醒（tools/daily_run.py 用）：通过本机已登录的 Outlook 桌面版发送，不需要邮箱密码。

收件地址在 config.toml 的 [notify] email，留空就不发。手机上装 Outlook App 并登录同一个邮箱，就能收到推送；
国内也能收到。限制：电脑关机、睡眠或者没有登录 Windows 时，计划任务本身不会运行，也就发不出邮件。

发一封测试邮件：
  .venv\\Scripts\\python -m autotrader.notify
"""
from __future__ import annotations

import base64
import subprocess
import sys
from datetime import datetime

from .config import load_config

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


def send_email(subject: str, body: str, cfg: dict | None = None) -> tuple[bool, str]:
    """发一封邮件。返回 (是否成功, 说明)。不会抛异常：提醒失败不应影响交易流程。"""
    to = recipient(cfg)
    if not to:
        return False, "config.toml 的 [notify] email 为空，不发邮件"
    # 标题加上时间：同一天可能发好几封，也用来在发件箱里认出这一封
    subject = f"{subject}（{datetime.now():%m-%d %H:%M:%S}）"
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


if __name__ == "__main__":
    ok, detail = send_email("量化交易：测试邮件", "这是一封测试邮件。收到说明运行失败时的邮件提醒可以正常工作。\n\n"
                            "（由 python -m autotrader.notify 发送）")
    print(detail)
    sys.exit(0 if ok else 1)
