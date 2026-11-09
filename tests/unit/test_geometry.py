"""Geometry primitives shared by the planner and all validators."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from geometry import AGV_RADIUS, MIN_SEP, inflate, seg_hits_rect, seg_rect_dist  # noqa: E402


def test_min_sep_is_twice_agv_radius():
    assert abs(MIN_SEP - 2 * AGV_RADIUS) < 1e-6
    assert MIN_SEP > 1.0


def test_inflate_expands_all_sides():
    r = (2.0, 3.0, 5.0, 4.0)
    q = inflate(r, 0.73)
    assert tuple(q) == (2.0 - 0.73, 3.0 - 0.73, 5.0 + 0.73, 4.0 + 0.73)


def test_segment_crosses_rect_and_misses_when_offset():
    r = (2.0, -1.0, 3.0, 1.0)
    assert seg_hits_rect((0.0, 0.0), (5.0, 0.0), r)
    assert not seg_hits_rect((0.0, 5.0), (5.0, 5.0), r)


def test_segment_distance_is_zero_when_intersecting():
    r = (2.0, -1.0, 3.0, 1.0)
    assert seg_rect_dist((0.0, 0.0), (5.0, 0.0), r) == 0.0
    assert seg_rect_dist((0.0, 5.0), (5.0, 5.0), r) >= 4.0
