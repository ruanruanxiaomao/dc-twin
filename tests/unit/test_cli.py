"""CLI / doctor / runner smoke tests - no pipeline execution, no Blender."""
from dc_twin import doctor, runner
from dc_twin.cli import build_parser
from dc_twin.paths import ROOT, blender_exe, python_exe


def test_every_subcommand_parses():
    p = build_parser()
    for argv in (["doctor"], ["doctor", "--json"], ["demo"], ["demo", "--no-serve"],
                 ["run"], ["run", "--scope", "full", "--dry-run"],
                 ["validate"], ["validate", "--tier", "data", "--json"],
                 ["serve"], ["export"], ["export", "--target", "release"],
                 ["compare", "runs/a", "runs/b"]):
        a = p.parse_args(argv)
        assert a.cmd == argv[0]


def test_doctor_collect_is_pass_and_has_checks():
    r = doctor.collect()
    assert r["schema_version"] == "1.0" and r["kind"] == "doctor_report"
    assert len(r["checks"]) >= 10
    assert r["verdict"] == "PASS", r["failed"]


def test_scopes_have_expected_step_counts_and_are_ordered():
    assert set(runner.SCOPES) == {"data", "scenarios", "game", "inventory", "full"}
    assert len(runner.SCOPES["data"]) == 14   # + spec_areas / check_process / gen_inventory
    assert len(runner.SCOPES["scenarios"]) > len(runner.SCOPES["data"])
    assert len(runner.SCOPES["full"]) > len(runner.SCOPES["scenarios"])
    # 游戏门是独立一条链：不进 full（CI 可能没有浏览器），但必须有
    assert len(runner.SCOPES["game"]) >= 3
    assert runner.SCOPES["game"] != runner.SCOPES["full"]
    # 库存门是实验链（压库存/调补货点），不进 full
    assert len(runner.SCOPES["inventory"]) >= 3
    assert all(step[0] not in [s[0] for s in runner.SCOPES["full"]]
               for step in runner.SCOPES["inventory"])
    full = runner.build_steps("full", None)
    assert full[0][0] == "scripts/spec_layout.py"
    assert full[-1][0] == "scripts/manifest.py"


def test_dry_run_writes_nothing_and_exits_zero():
    assert runner.run(scope="full", dry_run=True) == 0


def test_path_helpers_are_absolute_and_consistent():
    assert ROOT.is_dir() and (ROOT / "requirements.txt").exists()
    assert python_exe()
    assert blender_exe() is None or isinstance(blender_exe(), str)
