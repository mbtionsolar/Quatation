"""Tool: turn the two Word quotations (Commercial / Residential, written with <placeholders>)
into templates with {{field}} tokens that the app fills.

    python tools/build_template.py                         # builds both from docx_template/originals/
    python tools/build_template.py commercial  "My_Commercial.docx"
    python tools/build_template.py residential "My_Residential.docx"

Layout and formatting are NOT modified, only the placeholder text is swapped. In the Terms & Conditions table
4 blank rows are added at the end (slots for the "Additional conditions" the form can add).
Run it again whenever you change an original Word file (originals are kept in docx_template/originals/).
"""
import copy
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from docxfill import (W, _cell_text, _q, find_spec_table, find_terms_table,  # noqa: E402
                      process_docx, replace_in_paragraph, set_cell_text)

ANGLE = re.compile(r"<[^<>]*>")
CUSTOM_SLOTS = 4

# paragraph text (as typed in the Word file) -> keys for each <...> in order.
# A key can be ("field", "unit") when the unit sits INSIDE the <...> in Word (it is written back after the token),
# or None to drop that <...> completely. A list of lists is used when the same text appears more than once.
COMMON = {
    "<4.320>": [["capacity_kw"]],
    "PV Module <Topcon> – <540> Wp": [["panel_type", "module_wp"]],
    "<08>": [["module_qty"]],
    "<WAAREE ENERGY LTD>": [["module_make"]],
    "<Solaryaan> With <4.00> Kw Capacity": [["inverter_make", "inverter_kw"]],
    "<Polycab> (<2.5>mm)": [["earth_make", "earth_mm"]],
    "<Polycab>": [["conduit_make"]],
    "<42,512.66>/-": [["rate"]],
    "<1,83,654.72>/-": [["amount"]],
    "<1,83,645.72>/-": [["subtotal"]],
    "<16,345.27>/-": [["gst_amount"]],
    "<25> Years": [["warranty_panel_years"]],
    "<07> Years": [["warranty_inverter_years"]],
    "<05> Years": [["warranty_service_years"]],
    "<Solaryaan 4.00 KW> Inverter": [["warranty_inverter"]],
}
RULES = {
    "commercial": {
        **COMMON,
        "<KUMAR GAJJAR>": [["customer_name"]],
        "<SHIRWADA>": [["customer_address"]],
        "<4.320 KW>": [[("capacity_kw", " KW")]],
        "<Polycab> (<2.5>mm AC COPPER & 4mm DC COPPER)": [["cable_make", "cable_ac_mm"]],
        "<Polytech> (<16>mm Aluminium)": [["la_make", "la_mm"]],
        "<Apollo/Hindustar> (GI Pipe <40X40> & <40X60>) With <2>mm Thickness With <6/4> Height": [
            ["structure_make", "gi_pipe", None, "thickness", "structure_height"]],
        "TOTAL PROJECT COST (INCLUDING GST)<2,00,000>/-": [["total_cost"]],
        "<WAAREE 540Wp> Solar Panel": [["warranty_panel"]],
    },
    "residential": {
        **COMMON,
        "<SANJAY THAKOR>": [["customer_name"]],
        "<PALDI>": [["customer_address"]],
        "<4.320> KW": [["capacity_kw"]],
        "<Polycab> (<2.5>mm AC COPPER & <4>mm DC COPPER)": [["cable_make", "cable_ac_mm", "cable_dc_mm"]],
        "<Polytech> (<16mm> Aluminium)": [["la_make", ("la_mm", "mm")]],
        "<Apollo/Hindustar> (GI Pipe <40X40> & <40X60>) With <2mm> Thickness With <6/4> Height": [
            ["structure_make", "gi_pipe", None, ("thickness", "mm"), "structure_height"]],
        "(-)<78,000>/-": [["subsidy"]],
        "<1,22,000>/-": [["amount_after_subsidy"]],
        "<WAAREE ENERGY LTD> 540Wp Solar Panel": [["warranty_panel"]],
    },
}

# text that is NOT inside <...> in Word but must still come from the form: (regex, replacement)
# applied to every paragraph of the part; they only match the intended text.
STATIC_FIXES = {
    "all": [
        (re.compile(r" & (?=\) With)"), ""),                      # "GI Pipe {{gi_pipe}} & )": gi_pipe holds both sizes
    ],
    "commercial": [
        (re.compile(r"(?<=& )4(?=mm DC COPPER)"), "{{cable_dc_mm}}"),
    ],
    "residential": [
        (re.compile(r"\s540Wp(?= Solar Panel)"), ""),             # warranty text now comes from {{warranty_panel}}
        (re.compile(r"COMMERTIAL"), "RESIDENTIAL"),               # typo in the Word file (Rates table)
    ],
}
FOOTER_FIXES = [  # contact person printed in the page footer
    (re.compile(r"^KUMAR GAJJAR$"), "{{contact_name}}"),
    (re.compile(r"9662590454"), "{{contact_mobile}}"),
]
TOTAL_STATIC = re.compile(r"\d[\d,]*(?=/-)")      # "TOTAL PROJECT COST (INCLUDING GST)2,00,000/-" (Residential has no <> here)


def build(kind, src, dst):
    rules = RULES[kind]
    occurrence = defaultdict(int)  # (part, paragraph text) -> how many times seen
    problems, used = [], []

    def tokenise_spec_table(root):
        tbl = find_spec_table(root)
        if tbl is None:
            problems.append(("document", "Project Specifications table not found"))
            return
        for row in tbl.findall(_q("tr"))[1:]:
            cells = row.findall(_q("tc"))
            name = _cell_text(cells[1]).strip().lower()
            if name == "inverter":
                set_cell_text(cells[2], "{{inverter_qty}}"); used.append("inverter_qty")
            elif name in ("acdb", "dcdb"):
                set_cell_text(cells[3], "{{%s_make}}" % name); used.append("%s_make" % name)

    def transform(part, root):
        if part == "word/document.xml":
            tokenise_spec_table(root)
        for p in root.iter("{%s}p" % W):
            state = {"n": 0, "full": None}

            def repl(m, full):
                state["full"] = full
                spec = rules.get(full)
                if spec is None:
                    problems.append((part, full))
                    return m.group(0)
                keys = spec[min(occurrence[(part, full)], len(spec) - 1)]
                key = keys[state["n"]]
                state["n"] += 1
                if key is None:
                    return ""
                suffix = ""
                if isinstance(key, tuple):
                    key, suffix = key
                used.append(key)
                return "{{%s}}%s" % (key, suffix)

            if replace_in_paragraph(p, ANGLE, repl, anchor_offset=1):
                occurrence[(part, state["full"])] += 1
            if part == "word/document.xml":
                for rx, new in STATIC_FIXES["all"] + STATIC_FIXES[kind]:
                    replace_in_paragraph(p, rx, lambda m, _f, new=new: new)
                text = "".join(t.text or "" for t in p.iter(_q("t")))
                if text.startswith("TOTAL PROJECT COST") and replace_in_paragraph(p, TOTAL_STATIC, lambda m, _f: "{{total_cost}}"):
                    used.append("total_cost")
            elif part.startswith("word/footer"):
                for rx, new in FOOTER_FIXES:
                    if replace_in_paragraph(p, rx, lambda m, _f, new=new: new):
                        used.append(new.strip("{}"))

        if part == "word/document.xml":
            tbl = find_terms_table(root)
            if tbl is None:
                problems.append((part, "Terms & Conditions table not found"))
                return
            # blank rows at the end = slots for the extra conditions typed in the form
            last = tbl.findall(_q("tr"))[-1]
            for n in range(1, CUSTOM_SLOTS + 1):
                row = copy.deepcopy(last)
                cells = row.findall(_q("tc"))
                set_cell_text(cells[0], "")
                set_cell_text(cells[1], "{{custom_term_%d}}" % n)
                used.append("custom_term_%d" % n)
                last.addnext(row)
                last = row

    out = process_docx(src, transform)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(out)
    left = [x for x in problems if x[1] and ANGLE.search(x[1]) or "not found" in x[1]]
    print(f"[{kind}] template written: {dst}")
    print(f"[{kind}] fields:", sorted(set(used)))
    if left:
        print(f"[{kind}] WARNING - placeholders with no rule:", left)
        return False
    return True


def main(argv):
    docx = ROOT / "docx_template"
    jobs = ([(argv[1], argv[2])] if len(argv) >= 3 else
            [("commercial", docx / "originals" / "commercial.docx"), ("residential", docx / "originals" / "residential.docx")])
    ok = True
    for kind, src in jobs:
        if kind not in RULES:
            sys.exit("first argument must be 'commercial' or 'residential'")
        ok = build(kind, src, docx / f"quotation_{kind}.docx") and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
