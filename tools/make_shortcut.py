"""在桌面、开始菜单和项目文件夹里建立“自动交易策略 - leiwang”快捷方式，双击即可打开窗口程序（不带黑色命令行窗口）。

tools\\setup.bat 最后会自动运行它；项目文件夹搬动位置后再运行一次即可：
  .venv\\Scripts\\python tools\\make_shortcut.py
"""
import base64
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "自动交易策略 - leiwang"
OLD_NAMES = ["量化交易监控"]  # 以前用过的名字：建新快捷方式时顺便删掉


def main() -> None:
    target = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    icon = ROOT / "autotrader" / "gui" / "app.ico"
    if not target.exists():
        raise SystemExit("找不到 .venv，请先运行 tools\\setup.bat")
    ps = f"""
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'), '{ROOT}') {{
    $tmp = Join-Path $dir 'QuantTradingMonitor.lnk'
    $lnk = $shell.CreateShortcut($tmp)
    $lnk.TargetPath = '{target}'
    $lnk.Arguments = '"{ROOT / "gui.py"}"'
    $lnk.WorkingDirectory = '{ROOT}'
    $lnk.IconLocation = '{icon},0'
    $lnk.Description = 'Quant trading monitor (read-only, never places orders)'
    $lnk.Save()
    $final = Join-Path $dir '{NAME}.lnk'
    Move-Item -LiteralPath $tmp -Destination $final -Force
    foreach ($old in {", ".join(f"'{n}'" for n in OLD_NAMES)}) {{
        Remove-Item -LiteralPath (Join-Path $dir "$old.lnk") -ErrorAction SilentlyContinue
    }}
    Write-Output $final
}}"""
    # 中文文件名：WScript.Shell 保存时会按系统的非中文代码页把中文变成 ?（Windows 不允许 ? 做文件名，保存失败），
    # 所以先用英文名保存，再用 Move-Item（支持中文）改名；脚本用 -EncodedCommand（UTF-16）传，中文不会走样
    encoded = base64.b64encode(("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + ps).encode("utf-16-le"))
    out = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded.decode()], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    print(out.stdout.strip() or out.stderr.strip())


if __name__ == "__main__":
    main()
