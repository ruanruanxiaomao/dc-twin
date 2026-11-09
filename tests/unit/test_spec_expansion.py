"""spec_layout expansion must be deterministic and seed-sensitive."""
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import spec_layout  # noqa: E402


def _spec():
    return json.loads((ROOT / "config" / "layout_spec.json").read_text(encoding="utf-8"))


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_expansion_is_deterministic():
    s = _spec()
    assert _digest(spec_layout.expand(s)) == _digest(spec_layout.expand(s))


def test_changing_seed_changes_expansion():
    s = _spec()
    s2 = copy.deepcopy(s)
    s2["site"]["seed"] = s["site"]["seed"] + 1
    assert _digest(spec_layout.expand(s)) != _digest(spec_layout.expand(s2))
