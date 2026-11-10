#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate assets/dc_studio.ico + .png (geometric mark, no external assets)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
BG = "#0a1120"
ACCENT = "#48c9f0"
GREEN = "#2ee6a6"
DIM = "#22314f"


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256.0
    d.rounded_rectangle([8 * s, 8 * s, 248 * s, 248 * s], radius=48 * s, fill=BG,
                        outline=DIM, width=int(4 * s))
    # warehouse roof
    d.polygon([(52 * s, 108 * s), (128 * s, 52 * s), (204 * s, 108 * s),
               (186 * s, 108 * s), (128 * s, 71 * s), (70 * s, 108 * s)], fill=ACCENT)
    # three rack columns
    for i, x in enumerate((74, 118, 162)):
        height = 96 if i == 1 else 72
        y0 = 208 - height
        d.rounded_rectangle([x * s, y0 * s, (x + 20) * s, 208 * s], radius=5 * s, fill=ACCENT)
        for k in range(1, 4):
            y = y0 + k * height / 4
            d.rectangle([x * s, y * s, (x + 20) * s, (y + 5) * s], fill=BG)
    # moving dot
    d.ellipse([176 * s, 176 * s, 204 * s, 204 * s], fill=GREEN)
    return img


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    big = draw(1024)
    big.resize((256, 256), Image.LANCZOS).save(ASSETS / "dc_studio.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.save(ASSETS / "dc_studio.ico", sizes=sizes)
    print("wrote", ASSETS / "dc_studio.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
