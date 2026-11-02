# AI error log

Mistakes made by the AI while updating this project, how they were caught, and the fix.
Add your own entries from running the GUI.

| # | What the AI got wrong | How it was caught | Fix |
|---|---|---|---|
| 1 | Q-limit tests rewritten for the new outer-loop design: the old back-switch test depended on switching inside intermediate iterations, so it failed after the redesign | Test failure | Scenario found by random search (5-bus case) that really needs PV→PQ and PQ→PV; the old test was replaced, not silenced |
| 2 | JSON export of imported cases failed: NumPy `bool_` (from `status > 0`) is not serialisable | New project-file test | `bool()` in the importer + JSON fallback for NumPy scalars |
| 3 | Power-factor entry passed a string to `abs()`; partial generator edits raised `KeyError` | New property-validation tests | Convert values; use existing values when a field is not supplied |
| 4 | Fake-Tk smoke test: load scaling limit (20) made the divergence demonstration impossible | Smoke script assertion | Limit raised to 100 |
| 5 | Left dead code and a duplicated method in `gui_app.py` | Code review before delivery | Removed |
| 6 | Earlier session: unit test assumed load-bus voltages < 1 pu (false for IEEE-14) | Test failure | Assert exact V² relation instead |
| 7 | Divergence of synthetic 1000/3000-bus test networks looked like a solver bug | Investigation | Networks were infeasible (slack supplying GW through a ring); not a bug |

## Verification that gave confidence
FD Jacobian (PQ, Z, ZIP, phase shifter), textbook H/N/M/L formulas, independent dense solver on IEEE 14/30/57/118
(V, angle, all branch flows, losses), power balance, published IEEE-14 numbers, Q-limit post-conditions on all four cases.
