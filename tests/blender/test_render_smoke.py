"""Optional Blender smoke test. CI skips this with -m 'not blender'."""
import subprocess

import pytest

from dc_twin.paths import blender_exe


@pytest.mark.blender
def test_blender_5_2_is_usable():
    b = blender_exe()
    assert b, "set BLENDER_EXE or install Blender 5.2"
    r = subprocess.run(
        [b, "-b", "--factory-startup", "--python-expr",
         "import bpy; print('DC_VER', bpy.app.version_string)"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert "DC_VER" in r.stdout
