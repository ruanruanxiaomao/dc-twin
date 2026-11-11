import subprocess, sys, tempfile, shutil, pathlib, os

url, out = sys.argv[1], sys.argv[2]
w = sys.argv[3] if len(sys.argv) > 3 else "1560,900"
budget = sys.argv[4] if len(sys.argv) > 4 else "22000"
edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
out_p = pathlib.Path(out).resolve()
out_p.parent.mkdir(parents=True, exist_ok=True)
if out_p.exists():
    out_p.unlink()
prof = tempfile.mkdtemp(prefix="dc_shot_")
r = subprocess.run([edge, "--headless=new", "--disable-gpu", "--use-gl=swiftshader",
                    "--user-data-dir=" + prof, "--no-first-run",
                    "--window-size=" + w, "--virtual-time-budget=" + budget,
                    "--screenshot=" + str(out_p), url],
                   capture_output=True, text=True, timeout=180)
shutil.rmtree(prof, ignore_errors=True)
print("rc", r.returncode, "exists", out_p.exists(),
      out_p.stat().st_size if out_p.exists() else 0)
if not out_p.exists():
    print((r.stderr or "")[-400:])
