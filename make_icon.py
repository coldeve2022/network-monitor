# -*- coding: utf-8 -*-
"""生成 NetworkMonitor 应用图标（网络监测主题）。
- 深蓝渐变圆角方块 + 青色信号波（探测/监测）+ 中心节点 + 底部告警红点
- 输出: dist/NetworkMonitor.ico（16/24/32/48/64/128/256 多尺寸）+ icon_preview.png
"""
import os
from pathlib import Path
from PIL import Image, ImageDraw


def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def draw_icon(size=256):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256.0  # 缩放因子

    # ---- 圆角矩形背景：深蓝垂直渐变 ----
    top, bottom = (18, 46, 92), (8, 20, 46)   # #122E5C -> #08142E
    radius = int(44 * s)
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    for y in range(size):
        t = y / max(size - 1, 1)
        bd.line([(0, y), (size, y)], fill=lerp(top, bottom, t) + (255,))
    img.paste(bg, (0, 0), mask)

    # 圆角描边：亮青色细边
    d.rounded_rectangle([int(2 * s), int(2 * s), size - 1 - int(2 * s), size - 1 - int(2 * s)],
                        radius=radius, outline=(80, 200, 255, 140), width=max(2, int(3 * s)))

    # ---- 信号波：三圈青色圆弧（从底部向上扩散的探测波纹）----
    wave_colors = [(56, 232, 255, 230), (56, 232, 255, 150), (56, 232, 255, 80)]
    cx, cy = size / 2, size * 0.60
    for i, col in enumerate(wave_colors):
        r = int((34 + i * 26) * s)  # 半径递增
        bbox = [cx - r, cy - r, cx + r, cy + r]
        # 圆弧从 195° 到 345°（底部开口的波形）
        d.arc(bbox, start=195, end=345, fill=col, width=max(3, int(5 * s)))

    # ---- 中心节点：发光圆点（监测目标）----
    n_r = int(15 * s)
    d.ellipse([cx - n_r, cy - n_r, cx + n_r, cy + n_r], fill=(120, 230, 255, 255))
    d.ellipse([cx - n_r * 0.5, cy - n_r * 0.5, cx + n_r * 0.5, cy + n_r * 0.5], fill=(235, 252, 255, 255))

    # ---- 底部：绿色基线（链路正常）+ 右侧小红点（告警）----
    bx1, bx2 = int(58 * s), int(198 * s)
    by = int(208 * s)
    d.line([(bx1, by), (bx2, by)], fill=(52, 211, 153, 230), width=max(3, int(5 * s)))
    d.ellipse([bx1 - int(4 * s), by - int(4 * s), bx1 + int(4 * s), by + int(4 * s)],
              fill=(52, 211, 153, 255))
    d.ellipse([bx2 - int(4 * s), by - int(4 * s), bx2 + int(4 * s), by + int(4 * s)],
              fill=(52, 211, 153, 255))

    # 右上角小圆点：脉冲红点（活性监测中）
    px, py = int(196 * s), int(48 * s)
    pr = int(12 * s)
    d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(248, 113, 113, 255))
    d.ellipse([px - pr * 0.4, py - pr * 0.4, px + pr * 0.4, py + pr * 0.4], fill=(255, 220, 220, 255))

    return img


def main():
    base = str(Path(__file__).resolve().parent)
    dist = os.path.join(base, "dist")
    os.makedirs(dist, exist_ok=True)

    img = draw_icon(256)
    img.save(os.path.join(base, "icon_preview.png"))

    # 多尺寸 ICO
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(os.path.join(base, "NetworkMonitor.ico"), format="ICO",
             sizes=sizes, append_images=[img.resize(sz, Image.LANCZOS) for sz in sizes[1:]])

    print("ICO:", os.path.join(base, "NetworkMonitor.ico"),
          os.path.getsize(os.path.join(base, "NetworkMonitor.ico")), "bytes")
    print("PNG:", os.path.join(base, "icon_preview.png"))


if __name__ == "__main__":
    main()
