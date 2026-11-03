"""One-time tool: turn the original Word quotation (with <placeholders>) into a
template with {{field}} tokens.

    python tools/build_template.py "Mbtion_Quatation.docx"

Layout and formatting are NOT modified, only the placeholder text is swapped.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docxfill import (W, _cell_text, _q, add_page_break_before, find_terms_table,  # noqa: E402
                      process_docx, replace_in_paragraph, set_cell_text)

ANGLE = re.compile(r"<[^<>]*>")
STRAY_GT = re.compile(r"(?<=mm)>")

# paragraph text (as typed in the original) -> keys for each <...> in order.
# A list of lists is used when the same text appears more than once (nth occurrence).
RULES = {
    "<Commercial> Rooftop Solar EPC Project": [["project_type"]],
    "<JIO PETROL PUMP> ": [["customer_name"]],
    "<KHIMANA>": [["customer_address"]],
    "<15.375> KW": [["capacity_kw"]],
    "<15.375>": [["capacity_kw"]],
    "<25>": [["module_qty"]],
    "PV Module Topcon – <615>Wp": [["module_wp"]],
    "<WAAREE ENERGY LTD>": [["module_make"]],
    "<01>": [["inverter_qty"]],
    "<Solaryaan> With <15.00> Kw Capacity": [["inverter_make", "inverter_kw"]],
    "<Havells>": [["acdb_make"], ["dcdb_make"]],
    "<Polycab> (<6> mm> COPPER AC & <4>mm COPPER DC)": [["cable_make", "cable_ac_mm", "cable_dc_mm"]],
    # unit text that sits INSIDE the <...> in the Word file is written back after the token
    "<Polycab> (<6 mm>)": [["earth_make", ("earth_mm", " mm")]],
    "<Polytech> (<16 mm> Aluminium)": [["la_make", ("la_mm", " mm")]],
    "<Apollo/Hindustar> (GI Pipe <40X40 & 40X60>) With <2mm> Thickness With <6/4> Height": [
        ["structure_make", "gi_pipe", ("thickness", "mm"), "structure_height"]],
    "<Polycab>": [["conduit_make"]],
    "<31,236.24>/-": [["rate"]],
    "<4,80,257.19>/-": [["amount"], ["subtotal"]],
    " <42,742.89>/-": [["gst_amount"]],
    "TOTAL PROJECT COST (INCLUDING GST)<5,23,000>/-": [["total_cost"]],
    "<WAAREE 615Wp> Solar Panel": [["warranty_panel"]],
    "<Solaryaan 15.00 KW> Inverter": [["warranty_inverter"]],
    "<25> Years": [["warranty_panel_years"]],
    "<07> Years": [["warranty_inverter_years"]],
    "<05> Years": [["warranty_service_years"]],
    "<JANAK JOSHI>": [["contact_name"]],
    "+91 <8320565108>": [["contact_mobile"]],
}


def main(src, dst):
    occurrence = defaultdict(int)  # (part, paragraph text) -> how many times seen
    problems, used = [], []

    def transform(part, root):
        for p in root.iter("{%s}p" % W):
            state = {"n": 0, "full": None}

            def repl(m, full):
                state["full"] = full
                rules = RULES.get(full)
                if rules is None:
                    problems.append((part, full))
                    return m.group(0)
                keys = rules[min(occurrence[(part, full)], len(rules) - 1)]
                key = keys[state["n"]]
                suffix = ""
                if isinstance(key, tuple):
                    key, suffix = key
                state["n"] += 1
                used.append(key)
                return "{{%s}}%s" % (key, suffix)

            if replace_in_paragraph(p, ANGLE, repl, anchor_offset=1):
                occurrence[(part, state["full"])] += 1
                # the Word file has a typo "<6> mm> COPPER AC": drop the stray ">" after "mm"
                replace_in_paragraph(p, STRAY_GT, lambda m, full: "")

        # blank rows at the end of the Terms & Conditions table = slots for extra conditions
        tbl = find_terms_table(root) if part == "word/document.xml" else None
        if tbl is not None:
            blank = [r for r in tbl.findall(_q("tr"))[1:]
                     if not any(_cell_text(tc).strip() for tc in r.findall(_q("tc")))]
            for n, row in enumerate(blank, 1):
                set_cell_text(row.findall(_q("tc"))[1], "{{custom_term_%d}}" % n)
                used.append("custom_term_%d" % n)
            if len(blank) != 4:
                problems.append((part, "expected 4 blank T&C rows, found %d" % len(blank)))
            # Warranty section must always start on a new page, however many T&C rows are printed.
            # The break goes on the first paragraph after the table (not on the heading itself):
            # if the table fills the page, a break on the heading could push it onto an extra page.
            nxt = tbl.getnext()
            if nxt is not None and nxt.tag == _q("p"):
                add_page_break_before(nxt)
            else:
                problems.append((part, "no paragraph found after the Terms & Conditions table"))

    out = process_docx(src, transform)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(out)
    if problems:
        print("WARNING - placeholders with no rule:", problems)
    print("Template written:", dst)
    print("Fields:", sorted(set(used)))


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "Mbtion_Quatation.docx"
    main(src, Path(__file__).resolve().parent.parent / "docx_template" / "quotation_template.docx")
