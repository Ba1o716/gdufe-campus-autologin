"""生成托盘/程序图标（campus_login/assets/tray.ico）。

只有需要重新生成图标时才运行本脚本（需要 Pillow）：
    python tools/make_icon.py
程序运行本身**不需要** Pillow。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]
BACKGROUND = (31, 111, 235)
FOREGROUND = (255, 255, 255)


def draw_icon(size: int) -> Image.Image:
    scale = 8
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = canvas * 0.045
    draw.rounded_rectangle(
        [margin, margin, canvas - margin, canvas - margin],
        radius=canvas * 0.22,
        fill=BACKGROUND,
    )

    center_x = canvas / 2
    dot_y = canvas * 0.735
    width = max(int(canvas * 0.075), 2)

    # Wi-Fi 信号：3 段圆弧 + 底部圆点
    for index, radius in enumerate((0.155, 0.275, 0.395)):
        box = [
            center_x - canvas * radius,
            dot_y - canvas * radius,
            center_x + canvas * radius,
            dot_y + canvas * radius,
        ]
        draw.arc(box, start=215, end=325, fill=FOREGROUND, width=width)

    dot_radius = canvas * 0.052
    draw.ellipse(
        [center_x - dot_radius, dot_y - dot_radius, center_x + dot_radius, dot_y + dot_radius],
        fill=FOREGROUND,
    )

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    target = Path(__file__).resolve().parent.parent / "campus_login" / "assets" / "tray.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw_icon(size) for size in SIZES]
    frames[0].save(target, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[1:])
    preview = Path(__file__).resolve().parent / "tray_preview.png"
    draw_icon(256).save(preview)
    print(f"已生成 {target}")
    print(f"预览图 {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
