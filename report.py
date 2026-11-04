"""Result tables, CSV / XLSX export and HTML / PDF reports (with the single-line diagram).

Works on the dict returned by ``solver.solve_power_flow``; no GUI toolkit needed.
"""
import csv
import datetime
import html
import math
import os
import tempfile
from pathlib import Path

import display as D


# ------------------------------------------------------------------------------ tables
def _f(v, nd=3):
    return "" if v is None else round(float(v), nd)


def bus_table(net, res):
    h = ["Bus", "Name", "Type", "V (pu)", "V (kV)", "Angle (deg)", "P inj (MW)", "Q inj (MVAr)",
         "Pg (MW)", "Qg (MVAr)", "Pd (MW)", "Qd (MVAr)", "Limit"]
    rows, keys = [], []
    for b in res["bus_results"]:
        rows.append([b["bus"], b["name"], b["type"], _f(b["vm_pu"], 5), _f(b["vm_kv"], 3), _f(b["va_deg"], 4),
                     _f(b["p_injection_mw"]), _f(b["q_injection_mvar"]), _f(b["p_gen_mw"]), _f(b["q_gen_mvar"]),
                     _f(b["p_load_mw"]), _f(b["q_load_mvar"]), b["limit"] or "OK"
                     ])
        keys.append(("bus", b["bus"]))
    return h, rows, keys


def branch_table(net, res):
    h = ["#", "From", "To", "P from (MW)", "Q from (MVAr)", "S from (MVA)", "P to (MW)", "Q to (MVAr)",
         "S to (MVA)", "Loading (%)", "P loss (MW)", "Q loss (MVAr)"]
    rows, keys = [], []
    for r in res["branch_results"]:
        if not r["active"]:
            rows.append([r["index"], r["from_bus"], r["to_bus"], "out of service"] + [""] * 8)
        else:
            rows.append([r["index"], r["from_bus"], r["to_bus"], _f(r["p_from_mw"]), _f(r["q_from_mvar"]),
                         _f(r["s_from_mva"]), _f(r["p_to_mw"]), _f(r["q_to_mvar"]), _f(r["s_to_mva"]),
                         _f(r["loading_percent"], 2), _f(r["p_loss_mw"], 4), _f(r["q_loss_mvar"], 4)])
        keys.append(("branch", r["index"]))
    return h, rows, keys


def gen_table(net, res):
    h = ["Bus", "Generator", "P (MW)", "Q (MVAr)", "In service", "Limit"]
    rows = [[g["bus"], g["name"], _f(g["p_gen_mw"]), _f(g["q_gen_mvar"]), g["in_service"],
             g.get("limit") or "OK"] for g in res["generator_results"]]
    return h, rows, [("bus", g["bus"]) for g in res["generator_results"]]


def violation_table(net, res):
    h = ["Element", "Violation", "Value", "Limit"]
    rows, keys = [], []
    for b, row in zip(net.buses, res["bus_results"]):
        if not b.in_service:
            continue
        st = D.bus_state(row["vm_pu"], b.v_min, b.v_max)
        if st != "ok":
            rows.append([f"Bus {b.number}", "undervoltage" if st == "under" else "overvoltage",
                         f"{row['vm_pu']:.4f} pu", f"{b.v_min:g}..{b.v_max:g} pu"])
            keys.append(("bus", b.number))
    for r, br in zip(res["branch_results"], net.branches):
        st = D.loading_state(r["loading_percent"])
        if r["active"] and st != "ok":
            rows.append([f"Branch {br.from_bus}-{br.to_bus}", "overload (>100 %)" if st == "red" else "loading 80-100 %",
                         f"{r['loading_percent']:.1f} %", f"{br.rate_mva:g} MVA"])
            keys.append(("branch", r["index"]))
    for row in res["bus_results"]:
        if row["q_limit_hit"]:
            rows.append([f"Generator bus {row['bus']}", f"at {row['limit']}", f"{row['q_gen_mvar']:.2f} MVAr", ""])
            keys.append(("bus", row["bus"]))
    if not rows:
        rows.append(["System", "No violations", "OK", "—"])
    return h, rows, keys


def iteration_table(net, res):
    h = ["Iteration", "Round", "Max mismatch (pu)", "log10"]
    rows = [[e["iteration"], e.get("round", 1), f"{e['max_mismatch_pu']:.5e}",
             round(math.log10(max(e["max_mismatch_pu"], 1e-20)), 3)] for e in res["iteration_log"]]
    return h, rows, [("iter", e["iteration"]) for e in res["iteration_log"]]


def summary_rows(net, res):
    gen_p = sum(g["p_gen_mw"] for g in res["generator_results"])
    load_p = sum(b["p_load_mw"] for b in res["bus_results"])
    vms = [b["vm_pu"] for b in res["bus_results"] if b["in_service"]]
    return [("Status", "converged" if res["converged"] else f"NOT converged ({res['message']})"),
            ("NR iterations", str(res["iterations"])), ("Max mismatch", f"{res['max_mismatch_pu']:.2e} pu"),
            ("Solve time", f"{res['solve_time_s'] * 1e3:.2f} ms"),
            ("System base", f"{net.base_mva:g} MVA, {net.frequency_hz:g} Hz"),
            ("Total generation", f"{gen_p:.3f} MW"), ("Total load", f"{load_p:.3f} MW"),
            ("Total losses", f"{res['total_p_loss_mw']:.4f} MW, {res['total_q_loss_mvar']:.4f} MVAr"),
            ("Voltage range", f"{min(vms):.4f} - {max(vms):.4f} pu")]


# ------------------------------------------------------------------------------ CSV / Excel
def export_csv(folder, net, res):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, tab in (("bus_results", bus_table(net, res)), ("branch_results", branch_table(net, res)),
                      ("generator_results", gen_table(net, res)), ("violations", violation_table(net, res)),
                      ("iteration_log", iteration_table(net, res))):
        p = folder / f"{name}.csv"
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(tab[0]); w.writerows(tab[1])
        paths.append(str(p))
    return paths


def export_xlsx(path, net, res):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for r in summary_rows(net, res):
        ws.append(list(r))
    ws.column_dimensions["A"].width = 24; ws.column_dimensions["B"].width = 60
    for title, tab in (("Bus", bus_table(net, res)), ("Branch", branch_table(net, res)),
                       ("Generators", gen_table(net, res)), ("Violations", violation_table(net, res)),
                       ("Iterations", iteration_table(net, res))):
        w = wb.create_sheet(title)
        w.append(tab[0])
        for c in w[1]:
            c.font = Font(bold=True)
        for r in tab[1]:
            w.append(r)
        for col in w.columns:
            w.column_dimensions[col[0].column_letter].width = max(10, min(26, max(len(str(c.value or "")) for c in col) + 2))
    wb.save(path)
    return str(path)


# ------------------------------------------------------------------------------ diagram
def diagram_primitives(net, positions, res=None, stale=False, show_boxes=True, show_colours=True,
                       show_kv=False, worst=()):
    """Drawing primitives: ('line', x1,y1,x2,y2, colour, width, dashed) | ('text', x,y, text, colour, size)."""
    prims = []
    by_bus = {b["bus"]: b for b in res["bus_results"]} if res else {}
    for k, br in enumerate(net.branches):
        if br.from_bus not in positions or br.to_bus not in positions:
            continue
        (x1, y1), (x2, y2) = positions[br.from_bus], positions[br.to_bus]
        row = res["branch_results"][k] if res and k < len(res["branch_results"]) else None
        pct = row["loading_percent"] if row and row["active"] else None
        col = D.branch_colour(pct, br.in_service, stale, row is not None) if show_colours else \
            (D.COL_GREY if not br.in_service else D.COL_NORMAL)
        prims.append(("line", x1, y1, x2, y2, col, 2.0, not br.in_service))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if row and row["active"] and show_boxes:
            for j, t in enumerate(D.branch_box_text(row).split("\n")):
                prims.append(("text", mx + 6, my - 4 + 11 * j, t, D.COL_GREY if stale else col, 8))
    for b in net.buses:
        if b.number not in positions:
            continue
        x, y = positions[b.number]
        row = by_bus.get(b.number)
        st = D.bus_state(row["vm_pu"], b.v_min, b.v_max) if row and b.in_service else "ok"
        col = D.bus_colour(st, b.in_service, stale) if (show_colours and res) else \
            (D.COL_GREY if not b.in_service else D.COL_NORMAL)
        if b.number in worst:
            prims.append(("line", x - 32, y, x + 32, y, D.COL_WORST, 12, False))
        prims.append(("line", x - 25, y, x + 25, y, col, 6.0, not b.in_service))
        prims.append(("text", x - 25, y - 9, b.name or f"Bus {b.number}", D.COL_NORMAL, 9))
        if row and show_boxes and b.in_service:
            for j, t in enumerate(D.bus_box_text(row, show_kv).split("\n")):
                prims.append(("text", x + 28, y + 12 + 11 * j, t, D.COL_GREY if stale else col, 8))
    return prims


def _bounds(prims):
    xs, ys = [], []
    for p in prims:
        if p[0] == "line":
            xs += [p[1], p[3]]; ys += [p[2], p[4]]
        else:
            xs += [p[1], p[1] + 5.5 * len(p[3])]; ys += [p[2] - 10, p[2] + 4]
    return (min(xs) - 30, min(ys) - 30, max(xs) + 30, max(ys) + 30) if xs else (0, 0, 200, 100)


def to_svg(prims, legend=True):
    x0, y0, x1, y1 = _bounds(prims)
    W, H = x1 - x0, y1 - y0 + (16 * (len(D.LEGEND) + 1) if legend else 0)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.0f} {y0:.0f} {W:.0f} {H:.0f}" '
           f'width="1000" style="max-width:100%;background:#fff">']
    for p in prims:
        if p[0] == "line":
            dash = ' stroke-dasharray="6 4"' if p[7] else ""
            out.append(f'<line x1="{p[1]:.1f}" y1="{p[2]:.1f}" x2="{p[3]:.1f}" y2="{p[4]:.1f}" stroke="{p[5]}" '
                       f'stroke-width="{p[6]}"{dash}/>')
        else:
            out.append(f'<text x="{p[1]:.1f}" y="{p[2]:.1f}" font-size="{p[5]}" fill="{p[4]}" '
                       f'font-family="sans-serif">{html.escape(p[3])}</text>')
    if legend:
        for j, (c, t) in enumerate(D.LEGEND):
            yy = y1 + 8 + 14 * j
            out.append(f'<rect x="{x0 + 10:.0f}" y="{yy:.0f}" width="14" height="8" fill="{c}"/>'
                       f'<text x="{x0 + 30:.0f}" y="{yy + 8:.0f}" font-size="10" font-family="sans-serif">{html.escape(t)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def to_png(prims, path, legend=True, dpi=130, width_in=10.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x0, y0, x1, y1 = _bounds(prims)
    scale = width_in / (x1 - x0)
    fig = plt.figure(figsize=(width_in, max(2.0, (y1 - y0) * scale)), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.axis("off")
    for p in prims:
        if p[0] == "line":
            ax.plot([p[1], p[3]], [p[2], p[4]], color=p[5], lw=max(0.6, p[6] * scale * 72 * 0.8),
                    ls="--" if p[7] else "-", solid_capstyle="butt")
        else:
            ax.text(p[1], p[2], p[3], color=p[4], fontsize=max(4.0, p[5] * scale * 72 * 0.55))
    if legend:
        for j, (c, t) in enumerate(D.LEGEND):
            ax.text(x0 + 8 + j * (x1 - x0) / 5, y1 - 8, "\u25a0 " + t, color=c, fontsize=5.5, va="bottom")
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------------------------------------ reports
def _html_table(tab):
    h, rows, _ = tab
    return ("<table><thead><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in h) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
            + "</tbody></table>")


def export_html(path, net, res, positions, stale=False, show_kv=False):
    svg = to_svg(diagram_primitives(net, positions, res, stale, show_kv=show_kv, worst=res.get("worst_buses", ())))
    summ = "".join(f"<tr><th>{html.escape(a)}</th><td>{html.escape(b)}</td></tr>" for a, b in summary_rows(net, res))
    viol = violation_table(net, res)
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Load flow report</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1f2937}}table{{border-collapse:collapse;margin:8px 0 22px;font-size:12px}}
th,td{{border:1px solid #d1d5db;padding:3px 8px;text-align:right}}th{{background:#f3f4f6}}.s th,.s td{{text-align:left}}h1{{font-size:22px}}h2{{font-size:16px;margin-top:26px}}</style></head><body>
<h1>Load Flow Report</h1><p>Generated {datetime.datetime.now():%Y-%m-%d %H:%M}.{' <b>Results are stale.</b>' if stale else ''}</p>
<h2>Summary</h2><table class="s">{summ}</table><h2>Single-line diagram</h2>{svg}
<h2>Violations</h2>{_html_table(viol) if viol[1] else '<p>None.</p>'}
<h2>Bus results</h2>{_html_table(bus_table(net, res))}<h2>Branch results</h2>{_html_table(branch_table(net, res))}
<h2>Generator results</h2>{_html_table(gen_table(net, res))}<h2>Iteration log</h2>{_html_table(iteration_table(net, res))}
</body></html>"""
    Path(path).write_text(doc, encoding="utf-8")
    return str(path)


def export_pdf(path, net, res, positions, stale=False, show_kv=False):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate, Spacer, Paragraph, Table, TableStyle

    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title="Load flow report")
    W = landscape(A4)[0] - 24 * mm

    def mk(tab, fs=6.5):
        data = [[str(c) for c in tab[0]]] + [[str(c) for c in r] for r in tab[1]]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), fs), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9ca3af")), ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        return t

    flow = [Paragraph("Load Flow Report", st["Title"]),
            Paragraph(f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M}" + (" - RESULTS ARE STALE" if stale else ""), st["Normal"]),
            Spacer(1, 6), Paragraph("Summary", st["Heading2"]), mk((["Item", "Value"], [list(r) for r in summary_rows(net, res)], []), 8)]
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "sld.png")
        to_png(diagram_primitives(net, positions, res, stale, show_kv=show_kv, worst=res.get("worst_buses", ())), png)
        iw, ih = ImageReader(png).getSize()
        viol = violation_table(net, res)
        flow += [PageBreak(), Paragraph("Single-line diagram", st["Heading2"]), Image(png, width=W, height=min(W * ih / iw, 150 * mm)),
                 PageBreak(), Paragraph("Violations", st["Heading2"]), mk(viol, 8) if viol[1] else Paragraph("None.", st["Normal"]),
                 Spacer(1, 8), Paragraph("Bus results", st["Heading2"]), mk(bus_table(net, res)), PageBreak(),
                 Paragraph("Branch results", st["Heading2"]), mk(branch_table(net, res)), Spacer(1, 8),
                 Paragraph("Generator results", st["Heading2"]), mk(gen_table(net, res), 7)]
        doc.build(flow)
    return str(path)
