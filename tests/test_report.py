import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import display as D
import report
from matpower_import import load_matpower_case
from solver import solve_power_flow


class TestDisplayRules(unittest.TestCase):
    def test_loading_thresholds_and_colours(self):
        self.assertEqual(D.loading_state(79.9), "ok")
        self.assertEqual(D.loading_state(80.0), "amber")
        self.assertEqual(D.loading_state(100.0), "amber")
        self.assertEqual(D.loading_state(100.1), "red")
        self.assertEqual(D.loading_state(None), "ok")
        self.assertEqual(D.branch_colour(90), D.COL_AMBER)
        self.assertEqual(D.branch_colour(120), D.COL_RED)
        self.assertEqual(D.branch_colour(50, in_service=False), D.COL_GREY)
        self.assertEqual(D.branch_colour(120, stale=True), D.COL_GREY)
        self.assertNotEqual(D.COL_UNDER, D.COL_OVER)

    def test_box_text_conventions(self):
        net = load_matpower_case(ROOT / "data" / "case14.m")
        net.branches[0].rate_mva = 150
        res = solve_power_flow(net)
        self.assertIn("V = 1.060 pu", D.bus_box_text(res["bus_results"][0]))
        self.assertIn("0.00\u00b0", D.bus_box_text(res["bus_results"][0]))
        self.assertIn("kV", D.bus_box_text(dict(res["bus_results"][0], vm_kv=13.8), show_kv=True))
        t = D.branch_box_text(res["branch_results"][0])
        self.assertIn("\u2192", t); self.assertIn("%", t)


class TestReports(unittest.TestCase):
    def setUp(self):
        self.net = load_matpower_case(ROOT / "data" / "case14.m")
        self.net.branches[0].rate_mva = 150      # ~105 % loaded -> red branch
        self.net.buses[13].v_min = 1.04          # bus 14 under-voltage
        self.res = solve_power_flow(self.net)
        self.pos = {b.number: (80 + 90 * (i % 5), 80 + 110 * (i // 5)) for i, b in enumerate(self.net.buses)}
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_violations(self):
        rows = report.violation_table(self.net, self.res)[1]
        text = " ".join(map(str, sum(rows, [])))
        self.assertIn("undervoltage", text); self.assertIn("overload", text)

    def test_csv_xlsx(self):
        paths = report.export_csv(Path(self.tmp.name) / "csv", self.net, self.res)
        self.assertEqual(len(paths), 5)
        with open(paths[0], encoding="utf-8-sig") as fh:
            self.assertEqual(len(list(csv.reader(fh))), 15)
        from openpyxl import load_workbook
        wb = load_workbook(report.export_xlsx(Path(self.tmp.name) / "r.xlsx", self.net, self.res))
        self.assertEqual(wb["Bus"].max_row, 15); self.assertIn("Violations", wb.sheetnames)

    def test_html_pdf_with_diagram(self):
        h = report.export_html(Path(self.tmp.name) / "r.html", self.net, self.res, self.pos)
        txt = Path(h).read_text(encoding="utf-8")
        for needle in ("<svg", "Bus results", "13.39", "undervoltage"):
            self.assertIn(needle, txt)
        p = report.export_pdf(Path(self.tmp.name) / "r.pdf", self.net, self.res, self.pos)
        self.assertEqual(Path(p).read_bytes()[:5], b"%PDF-")
        self.assertGreater(Path(p).stat().st_size, 5000)

    def test_divergent_result_can_be_reported(self):
        for b in self.net.buses:
            b.p_load *= 40; b.q_load *= 40
        res = solve_power_flow(self.net)
        self.assertFalse(res["converged"])
        prims = report.diagram_primitives(self.net, self.pos, res, worst=res["worst_buses"])
        self.assertTrue(any(p[5] == D.COL_WORST for p in prims if p[0] == "line"))


if __name__ == "__main__":
    unittest.main()
