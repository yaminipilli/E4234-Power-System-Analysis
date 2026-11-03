"""Tests for the logic behind the GUI (undo/redo, element editing, validation, files)."""
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gui_fields
from gui_model import ProjectModel, MAX_UNDO


def small():
    m = ProjectModel()                                   # bus 1 = slack
    a = 1
    b = m.add_bus(300, 100)
    c = m.add_bus(300, 300)
    m.add_branch("line", a, b)
    m.add_branch("line", b, c)
    m.add_element("load", c)
    return m, (a, b, c)


class TestBuildAndRun(unittest.TestCase):
    def test_build_small_network_and_run(self):
        m, (a, b, c) = small()
        self.assertTrue(m.can_run())
        res = m.run()
        self.assertTrue(res["converged"])
        self.assertLess(next(r for r in res["bus_results"] if r["bus"] == c)["vm_pu"], 1.0)

    def test_generator_and_external_grid_tools(self):
        m, (a, b, c) = small()
        key = m.add_element("generator", b)
        self.assertEqual(key[0], "gen")
        self.assertEqual(m.net.buses[m.net.index[b]].kind, "PV")            # type set from what is connected
        m.add_element("external grid", c)
        self.assertEqual([x.number for x in m.net.buses if x.kind == "SLACK"], [c])
        self.assertEqual(m.net.buses[0].kind, "PQ")
        self.assertTrue(m.run()["converged"])

    def test_checks_block_run_for_islands_and_missing_slack(self):
        m, (a, b, c) = small()
        m.delete([("branch", 1)])
        self.assertFalse(m.can_run())
        self.assertTrue(any("island without" in e.message for e in m.errors()))
        m2 = ProjectModel(); m2.net.buses[0].kind = "PQ"
        self.assertFalse(m2.can_run())

    def test_delete_bus_removes_attached(self):
        m, (a, b, c) = small()
        m.add_element("generator", b)
        m.delete([("bus", b)])
        self.assertEqual((len(m.net.branches), len(m.net.generators)), (0, 0))
        self.assertTrue(m.undo())
        self.assertEqual((len(m.net.branches), len(m.net.generators)), (2, 1))

    def test_delete_branch_and_generator_by_key(self):
        m, (a, b, c) = small()
        m.add_element("generator", b)
        m.delete([("branch", 0), ("gen", 0)])
        self.assertEqual((len(m.net.branches), len(m.net.generators)), (1, 0))


class TestUndoAndStale(unittest.TestCase):
    def test_undo_redo_restores_layout_and_network(self):
        m, _ = small()
        xy = dict(m.positions)
        n0 = len(m.net.buses)
        m.add_bus(500, 500)
        m.move_buses({2: (999, 999)})
        self.assertTrue(m.undo()); self.assertEqual(m.positions[2], xy[2])     # layout NOT reset by undo
        self.assertTrue(m.undo()); self.assertEqual(len(m.net.buses), n0)
        self.assertTrue(m.redo() and m.redo())
        self.assertEqual(len(m.net.buses), n0 + 1)

    def test_undo_depth_at_least_10(self):
        m = ProjectModel()
        for i in range(15):
            m.add_bus(10 * i, 0)
        n = 0
        while m.undo():
            n += 1
        self.assertEqual(n, 15)
        for i in range(MAX_UNDO + 5):
            m.add_bus(0, 0)
        n = 0
        while m.undo():
            n += 1
        self.assertEqual(n, MAX_UNDO)

    def test_editing_marks_results_stale_but_keeps_them(self):
        m, _ = small()
        res = m.run()
        self.assertFalse(m.stale)
        m.apply_fields("bus", 3, {"p_load_mw": 30})
        self.assertTrue(m.stale)
        self.assertIs(m.results, res)               # greyed out, not deleted
        m.run()
        self.assertFalse(m.stale)


class TestProperties(unittest.TestCase):
    def test_invalid_values_rejected_with_messages(self):
        m, (a, b, c) = small()
        e = m.apply_fields("branch", 0, {"rate_mva": -5})
        self.assertIn("rate_mva", e)
        self.assertEqual(m.net.branches[0].rate_mva, 100.0)
        self.assertIn("v_min", m.apply_fields("bus", a, {"v_min": 1.2, "v_max": 1.1}))
        self.assertIn("base_kv", m.apply_fields("bus", a, {"base_kv": "abc"}))
        self.assertIn("x", m.apply_fields("branch", 0, {"r": 0, "x": 0}))
        self.assertEqual(m.apply_fields("branch", 0, {"rate_mva": 80}), {})

    def test_mw_entry_is_converted_to_pu(self):
        m, (a, b, c) = small()
        m.set_base(50.0, 60.0)
        m.apply_fields("bus", c, {"p_load_mw": 25, "q_load_mvar": 10, "b_shunt_mvar": 5})
        bus = m.net.buses[m.net.index[c]]
        self.assertAlmostEqual(bus.p_load, 0.5); self.assertAlmostEqual(bus.q_load, 0.2)
        self.assertAlmostEqual(bus.b_shunt, 0.1)
        self.assertEqual(m.net.frequency_hz, 60.0)

    def test_power_factor_entry(self):
        m, (a, b, c) = small()
        m.apply_fields("bus", c, {"p_load_mw": 80, "pf": "0.8", "pf_lagging": True})
        self.assertAlmostEqual(m.net.buses[m.net.index[c]].q_load * 100, 60.0)
        m.apply_fields("bus", c, {"p_load_mw": 80, "pf": "0.8", "pf_lagging": False})
        self.assertAlmostEqual(m.net.buses[m.net.index[c]].q_load * 100, -60.0)
        self.assertIn("pf", m.apply_fields("bus", c, {"pf": "1.5"}))

    def test_zip_load_model_validation_and_run(self):
        m, (a, b, c) = small()
        self.assertIn("zip_p", m.apply_fields("bus", c, {"load_model": "ZIP", "zip_z": 0.5, "zip_i": 0.5, "zip_p": 0.5}))
        self.assertEqual(m.apply_fields("bus", c, {"load_model": "ZIP", "zip_z": 0.3, "zip_i": 0.2, "zip_p": 0.5}), {})
        self.assertTrue(m.run()["converged"])

    def test_line_physical_entry(self):
        m, (a, b, c) = small()
        m.apply_fields("bus", a, {"base_kv": 132})
        e = m.apply_fields("branch", 0, {"input_mode": "physical", "length_km": 20, "r_ohm_km": 0.1,
                                         "x_ohm_km": 0.4, "b_us_km": 3.0})
        self.assertEqual(e, {})
        self.assertAlmostEqual(m.net.branches[0].x, 0.4 * 20 / (132 ** 2 / 100), places=9)

    def test_line_physical_needs_kv(self):
        m, (a, b, c) = small()
        m.apply_fields("bus", a, {"base_kv": 0})
        self.assertIn("input_mode", m.apply_fields("branch", 0, {"input_mode": "physical"}))

    def test_transformer_nameplate_and_tap_range(self):
        m, (a, b, c) = small()
        t = m.add_branch("transformer", a, c)
        self.assertEqual(m.apply_fields("branch", t, {"rated_mva": 50, "z_pct": 10, "x_r": 10, "tap": 1.05}), {})
        br = m.net.branches[t]
        self.assertAlmostEqual(math.hypot(br.r, br.x), 0.2, places=9)
        self.assertEqual(br.rate_mva, 50)
        self.assertIn("tap", m.apply_fields("branch", t, {"tap": 1.5}))

    def test_imported_transformer_keeps_its_data_when_edited(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        i = next(k for k, b in enumerate(m.net.branches) if b.tap != 1.0)
        r, x = m.net.branches[i].r, m.net.branches[i].x
        self.assertEqual(m.apply_fields("branch", i, {"shift_deg": 0.0, "in_service": True}), {})
        self.assertEqual((m.net.branches[i].r, m.net.branches[i].x), (r, x))

    def test_generator_fields(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        self.assertIn("q_min_mvar", m.apply_fields("gen", 1, {"q_min_mvar": 60, "q_max_mvar": 50}))
        self.assertEqual(m.apply_fields("gen", 1, {"p_mw": 35, "q_max_mvar": 45}), {})
        self.assertAlmostEqual(m.net.generators[1].p_gen, 0.35)
        self.assertTrue(m.run()["converged"])

    def test_slack_checkbox_moves_slack(self):
        m, (a, b, c) = small()
        m.apply_fields("bus", c, {"slack": True})
        self.assertEqual([x.number for x in m.net.buses if x.kind == "SLACK"], [c])

    def test_values_roundtrip_and_inf(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        v = gui_fields.get_values(m.net, "gen", 0)
        self.assertEqual(gui_fields.validate(m.net, "gen", 0, v), {})
        m.net.generators[0].q_max = math.inf
        v = gui_fields.get_values(m.net, "gen", 0)
        self.assertEqual(v["q_max_mvar"], "inf")
        self.assertEqual(gui_fields.validate(m.net, "gen", 0, v), {})


class TestCopyFindFiles(unittest.TestCase):
    def test_copy_paste_buses_with_internal_branches_and_gens(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        nb, nbr, ng = len(m.net.buses), len(m.net.branches), len(m.net.generators)
        self.assertEqual(m.copy([("bus", 2), ("bus", 4), ("bus", 5)]), 3)
        sel = m.paste()
        self.assertEqual(len(sel), 3)
        self.assertEqual(len(m.net.buses), nb + 3)
        self.assertEqual(len(m.net.branches), nbr + 3)          # 2-4, 2-5, 4-5
        self.assertEqual(len(m.net.generators), ng + 1)
        self.assertEqual(sum(b.kind == "SLACK" for b in m.net.buses), 1)

    def test_find(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        self.assertIn(("bus", 7), m.find("7"))
        m.net.buses[8].name = "Harbour"
        self.assertEqual(m.find("harb"), [("bus", 9)])

    def test_project_json_roundtrip(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        m.study.enforce_q_limits, m.study.tolerance, m.study.start = True, 1e-6, "previous"
        m.positions[3] = (555.0, 777.0)
        with tempfile.TemporaryDirectory() as d:
            m.save(Path(d) / "p.json")
            m2 = ProjectModel.open(Path(d) / "p.json")
        self.assertEqual(m2.positions[3], (555.0, 777.0))
        self.assertEqual((m2.study.enforce_q_limits, m2.study.tolerance, m2.study.start), (True, 1e-6, "previous"))
        np.testing.assert_allclose([b["vm_pu"] for b in m2.run()["bus_results"]],
                                   [b["vm_pu"] for b in m.run()["bus_results"]], atol=1e-12)

    def test_project_with_infinite_limits_roundtrips(self):
        m, _ = small()
        m.add_element("generator", 2)
        m.net.generators[0].q_max = math.inf
        with tempfile.TemporaryDirectory() as d:
            m.save(Path(d) / "p.json")
            m2 = ProjectModel.open(Path(d) / "p.json")
        self.assertEqual(m2.net.generators[0].q_max, math.inf)

    def test_study_options_reach_solver(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        m.net.generators[1].q_max = 0.30
        m.study.enforce_q_limits = True
        lines = []
        res = m.run(lines.append)
        self.assertTrue(any("SWITCH" in l for l in lines)); self.assertTrue(res["converged"])
        m.study.enforce_q_limits, m.study.load_scale = False, 1.1
        self.assertAlmostEqual(sum(b["p_load_mw"] for b in m.run()["bus_results"]), 259.0 * 1.1, places=4)
        self.assertAlmostEqual(m.net.buses[1].p_load * 100, 21.7)          # saved data untouched by scaling

    def test_previous_solution_start(self):
        m = ProjectModel.import_case(ROOT / "data" / "case30.m")
        m.run()
        m.study.start = "previous"
        self.assertEqual(m.run()["iterations"], 0)

    def test_auto_layout(self):
        m = ProjectModel.import_case(ROOT / "data" / "case118.m")
        self.assertEqual(len(m.positions), 118)
        self.assertEqual(len(set(m.positions.values())), 118)


class TestLayoutAndLegacyProjects(unittest.TestCase):
    def test_imported_case_starts_on_a_circle(self):
        for case in (14, 118):
            m = ProjectModel.import_case(ROOT / "data" / f"case{case}.m")
            pts = np.array([m.positions[b.number] for b in m.net.buses])
            centre = (pts.min(axis=0) + pts.max(axis=0)) / 2
            radii = np.linalg.norm(pts - centre, axis=1)
            self.assertLess(radii.std() / radii.mean(), 0.03)          # all buses on one circle (grid snapping only)
            self.assertEqual(len({tuple(p) for p in pts}), len(pts))

    def test_auto_layout_modes_and_undo(self):
        m = ProjectModel.import_case(ROOT / "data" / "case30.m")
        circle = dict(m.positions)
        m.auto_layout("force")
        self.assertNotEqual(m.positions, circle)
        m.auto_layout("circle")
        self.assertEqual(m.positions, circle)
        m.undo(); m.undo()
        self.assertEqual(m.positions, circle)

    def test_open_project_written_by_first_gui_version(self):
        """Old files: study block with a string tolerance and 'flat_start'; generation kept on the Bus fields."""
        import json
        old = {"format": "E4234-LoadFlow-Project", "version": 1,
               "network": {"base_mva": 100.0, "frequency_hz": 50.0, "metadata": {"base_kv_by_bus": {"1": 11.0}},
                           "buses": [
                               {"number": 1, "kind": "SLACK", "vm": 1.02, "va_deg": 0.0},
                               {"number": 2, "kind": "PV", "vm": 1.01, "p_gen": 0.4, "q_min": -1.0, "q_max": 1.0},
                               {"number": 3, "kind": "PQ", "p_load": 0.5, "q_load": 0.2},
                               {"number": 4, "kind": "PQ", "p_gen": 0.1, "q_gen": 0.02}],
                           "branches": [{"from_bus": 1, "to_bus": 2, "r": 0.01, "x": 0.1},
                                        {"from_bus": 2, "to_bus": 3, "r": 0.02, "x": 0.2},
                                        {"from_bus": 3, "to_bus": 4, "r": 0.02, "x": 0.2}],
                           "generators": []},
               "layout": {"1": [100, 100], "2": [300, 100]},          # buses 3, 4 have no stored position
               "study": {"tolerance": "1e-8", "max_iterations": 30, "flat_start": False,
                         "enforce_q_limits": True, "load_scale": 1.0}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.json"
            p.write_text(json.dumps(old))
            m = ProjectModel.open(p)
        self.assertEqual(m.study.tolerance, 1e-8)
        self.assertEqual((m.study.start, m.study.enforce_q_limits), ("data", True))
        self.assertEqual(len(m.net.generators), 1)                       # PV bus 2 became a generator record
        self.assertEqual(m.net.buses[1].kind, "PV")
        self.assertAlmostEqual(m.net.buses[3].p_load, -0.1)              # PQ injection became a negative load
        self.assertEqual(set(m.positions), {1, 2, 3, 4})
        self.assertEqual(m.net.metadata["base_kv_by_bus"], {1: 11.0})
        res = m.run()
        self.assertTrue(res["converged"])
        self.assertAlmostEqual(next(b for b in res["bus_results"] if b["bus"] == 2)["p_gen_mw"], 40.0, places=6)

    def test_open_rejects_foreign_json_with_clear_message(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text("{\"hello\": 1}")
            with self.assertRaises(ValueError) as cm:
                ProjectModel.open(p)
        self.assertIn("not a load-flow project", str(cm.exception))

    def test_save_open_roundtrip_keeps_everything(self):
        m = ProjectModel.import_case(ROOT / "data" / "case14.m")
        m.study.load_scale = 1.2
        m.net.branches[3].in_service = False
        with tempfile.TemporaryDirectory() as d:
            m.save(Path(d) / "p.json")
            self.assertFalse(m.dirty)
            m2 = ProjectModel.open(Path(d) / "p.json")
        self.assertEqual(m2.positions, m.positions)
        self.assertEqual(m2.study.load_scale, 1.2)
        self.assertFalse(m2.net.branches[3].in_service)
        self.assertEqual(len(m2.net.generators), len(m.net.generators))


if __name__ == "__main__":
    unittest.main()
