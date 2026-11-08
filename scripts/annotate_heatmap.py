"""annotate_heatmap.py — 给 05_slotting_heatmap.png 叠加 5 档图例。

第二轮审计 §2.2 异常 10 / §2.8：05 图没有图例，且被青色路线线污染。
路线线已由 render.py 在拍 05 时隐藏；本脚本补上色阶图例与每档货架数。

运行：python scripts/annotate_heatmap.py（必须在 render.py 之后）
"""
import json
import os

from PIL import Image, ImageDraw

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
IMG = os.path.join(OUT, 'renders', '05_slotting_heatmap.png')

BUCKETS = [
    ('rack_cold', '#3b82f6', 'cold / C'),
    ('rack_cool', '#22c55e', 'cool'),
    ('rack_mid', '#eab308', 'mid'),
    ('rack_warm', '#f97316', 'warm'),
    ('rack_hot', '#ef4444', 'hot / A'),
]


def main():
    br = json.load(open(os.path.join(OUT, 'build_report.json'), encoding='utf-8'))
    counts = br['counts'].get('heat_buckets', {})
    img = Image.open(IMG).convert('RGB')
    d = ImageDraw.Draw(img)
    W, H = img.size
    x0, y0, w, h = W - 420, H - 250, 396, 218
    d.rectangle([x0, y0, x0 + w, y0 + h], fill=(250, 250, 248),
                outline=(90, 100, 110), width=2)
    d.text((x0 + 14, y0 + 10), 'Slotting heat  |  5 buckets  |  rack count',
           fill=(30, 40, 50))
    for i, (key, color, label) in enumerate(BUCKETS):
        y = y0 + 40 + i * 32
        d.rectangle([x0 + 16, y, x0 + 46, y + 22], fill=color,
                    outline=(60, 70, 80), width=1)
        d.text((x0 + 58, y + 4), '%-10s  %s racks'
               % (label, counts.get(key, 0)), fill=(30, 40, 50))
    d.text((x0 + 14, y0 + h - 26),
           'heat = 0.75 x velocity rank + 0.25 x event hits',
           fill=(80, 90, 100))
    img.save(IMG)
    print('HEATMAP_LEGEND_OK', IMG, img.size)


if __name__ == '__main__':
    main()
