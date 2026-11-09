## What changed

<!-- One paragraph. Link the issue if there is one. -->

## Verification

- [ ] `dc doctor`
- [ ] `dc run --scope data` (or the smallest scope that covers the change)
- [ ] `pytest -m "not blender"`
- [ ] `dc validate --tier data` (and `--tier full` if Blender artifacts changed)
- [ ] `dc validate --check` (output contracts / manifest)
- [ ] Docs + CHANGELOG updated; current numbers come from `out/CURRENT_STATUS.md`
- [ ] No generated binaries, personal absolute paths, or new hard-coded geometry

## Notes for reviewers

<!-- Call out anything that is intentionally NOT solved, and label analytic
     what-if numbers separately from measured main-chain numbers. -->
