"""nav_grid.py — 0.5 m 导航栅格：唯一真相源（模块 + 可独立运行的生成脚本）。

依据 AUDIT_REPORT.md §3.1(2) / §3.4(3)：
  · 月台纳入障碍；
  · 所有实体统一按 geometry.AGV_RADIUS 膨胀；
  · 分辨率从 1 m 提到 0.5 m，避免 0.73 m 半径把窄通道整格吃掉。

作为脚本运行：python scripts/nav_grid.py → data/nav_grid.json
作为模块：from nav_grid import load_grid
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from geometry import AGV_RADIUS, obstacle_rects, inflate

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
CELL = 0.5


class Grid:
    def __init__(self, cols, rows, cell, cells):
        self.cols, self.rows, self.cell, self.cells = cols, rows, cell, cells

    def walkable(self, c, r):
        return 0 <= c < self.cols and 0 <= r < self.rows and \
            self.cells[r * self.cols + c] == 0

    def to_cell(self, p):
        return (int(math.floor(p[0] / self.cell)), int(math.floor(p[1] / self.cell)))

    def center(self, c, r):
        return ((c + 0.5) * self.cell, (r + 0.5) * self.cell)

    def nearest_walkable(self, c, r):
        if self.walkable(c, r):
            return (c, r)
        for rad in range(1, 30):
            for dr in range(-rad, rad + 1):
                for dc in range(-rad, rad + 1):
                    if max(abs(dr), abs(dc)) == rad and self.walkable(c + dc, r + dr):
                        return (c + dc, r + dr)
        raise RuntimeError('NO_WALKABLE_CELL within 30 cells of (%d,%d)' % (c, r))


def build(layout, cfg):
    cell = CELL
    cols = int(math.ceil(layout['size']['width'] / cell))
    rows = int(math.ceil(layout['size']['depth'] / cell))
    cells = bytearray(cols * rows)
    for kind, rid, rect in obstacle_rects(layout, cfg):
        x0, z0, x1, z1 = inflate(rect, AGV_RADIUS)
        c0 = max(0, math.floor(x0 / cell))
        c1 = min(cols, math.ceil(x1 / cell))
        r0 = max(0, math.floor(z0 / cell))
        r1 = min(rows, math.ceil(z1 / cell))
        for c in range(c0, c1):
            for r in range(r0, r1):
                if (c + 1) * cell > x0 and c * cell < x1 and \
                   (r + 1) * cell > z0 and r * cell < z1:
                    cells[r * cols + c] = 1
    return Grid(cols, rows, cell, cells)


def load_grid():
    j = json.load(open(os.path.join(DATA, 'nav_grid.json'), encoding='utf-8'))
    cells = bytearray()
    for row in j['walkable_rows']:
        cells.extend(0 if ch == '1' else 1 for ch in row)
    return Grid(j['cols'], j['rows_count'], j['cell_size_m'], cells)


def main():
    layout = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
    cfg = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
    g = build(layout, cfg)
    rows = [''.join('1' if g.cells[r * g.cols + c] == 0 else '0'
                    for c in range(g.cols)) for r in range(g.rows)]
    blocked = sum(g.cells)
    kinds = {}
    for kind, _, _ in obstacle_rects(layout, cfg):
        kinds[kind] = kinds.get(kind, 0) + 1
    out = {
        'cell_size_m': CELL, 'cols': g.cols, 'rows_count': g.rows,
        'agv_radius_m': AGV_RADIUS,
        'legend': '1=可通行通道, 0=障碍(实体+AGV_RADIUS 膨胀)',
        'obstacle_rects_by_kind': kinds,
        'blocked_cells': blocked, 'corridor_cells': g.cols * g.rows - blocked,
        'walkable_rows': rows,
    }
    with open(os.path.join(DATA, 'nav_grid.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('NAV_GRID_OK', g.cols, 'x', g.rows, 'cell', CELL,
          'blocked', blocked, 'corridor', out['corridor_cells'],
          'radius', AGV_RADIUS, 'obstacles', json.dumps(kinds, ensure_ascii=False))


if __name__ == '__main__':
    main()
