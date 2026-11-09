"""Console API smoke test: starts a real subprocess on an ephemeral port."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.slow
def test_bootstrap_endpoint_responds():
    port = _free_port()
    env = dict(os.environ, DC_CONSOLE_PORT=str(port), DC_CONSOLE_HOST="127.0.0.1")
    proc = subprocess.Popen([sys.executable, "console/server.py"], cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = "http://127.0.0.1:%d/api/bootstrap" % port
        body = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    body = json.loads(r.read().decode("utf-8"))
                break
            except Exception:
                time.sleep(0.15)
        assert body is not None, "console did not start on port %d" % port
        assert isinstance(body, dict) and body, "bootstrap returned an empty payload"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_non_loopback_host_requires_token():
    """CLI-level guard: dc serve must refuse 0.0.0.0 without a token."""
    r = subprocess.run([sys.executable, "-m", "dc_twin", "serve", "--host", "0.0.0.0"],
                       cwd=ROOT, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k != "DC_CONSOLE_TOKEN"})
    assert r.returncode != 0
    assert "DC-E4001" in (r.stdout + r.stderr)
