"""把 autotrader/gui/app_source.png（AI 生成的“股神”图）做成窗口程序的图标 autotrader/gui/app.ico。

.ico 里放 16~256 多种尺寸，任务栏、桌面、开始菜单各取合适的大小；每个尺寸都是整张图（不裁剪），四个角切成圆角。换图时把新图存成 app_source.png（正方形，越大越好），再运行：
  .venv\\Scripts\\python tools\\make_icon.py            生成 app.ico
  .venv\\Scripts\\python tools\\make_icon.py 预览.png   只出预览图，不改动 app.ico
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

GUI = Path(__file__).resolve().parent.parent / "autotrader" / "gui"
SOURCE, OUT = GUI / "app_source.png", GUI / "app.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def rounded(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    scale = 4  # 先在 4 倍大小上画圆角遮罩再缩小，边缘更平滑
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * scale - 1, size * scale - 1), radius=size * scale // 6,
                                           fill=255)
    img.putalpha(mask.resize((size, size), Image.LANCZOS))
    return img


def icon_images() -> list[Image.Image]:
    src = Image.open(SOURCE).convert("RGB")
    return [rounded(src, size) for size in SIZES]


def save_ico(images: list[Image.Image], path: Path) -> None:
    """多尺寸 .ico：每个尺寸存成一张 PNG（Windows Vista 起支持）。"""
    pngs = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        pngs.append((img.width, buf.getvalue()))
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    for size, png in pngs:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    path.write_bytes(out + b"".join(png for _, png in pngs))


def main() -> None:
    images = icon_images()
    if len(sys.argv) == 1:
        save_ico(images, OUT)
        print(OUT)
        return
    # 预览：各尺寸放大到同样大小看清楚细节，下面一排是实际大小
    sheet = Image.new("RGBA", (len(SIZES) * 140 + 20, 330), (43, 43, 43, 255))
    for i, img in enumerate(images):
        sheet.alpha_composite(img.resize((128, 128), Image.NEAREST), (20 + i * 140, 20))
        sheet.alpha_composite(img, (20 + i * 140, 180))
    sheet.save(sys.argv[1])


if __name__ == "__main__":
    main()
