"""Legacy self-written test drivers are intentionally NOT pytest modules.

They are subprocess-based negative-test runners invoked by
tests/run_all_negative.py; importing them during collection would execute
their top-level setup. pytest tests live under the unit/ contract/
integration/ acceptance/ e2e/ blender/ folders instead.
"""
collect_ignore = [
    "run_all_negative.py",
    "test_spec_layout.py",
    "test_scenario.py",
    "test_cleanroom.py",
    "test_validator_can_fail.py",
]
