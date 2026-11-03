"""One-time tool: add the two subsidy rows to docx_template/quotation_template.docx.

    python tools/add_subsidy_rows.py "RESIDENTIAL_Quotation.docx"

The rows ("Subsidy" and "Amount After Subsidy") are copied, with their exact formatting, from a Residential
quotation made in Word and placed under the GST row of the rates table. They carry the tokens
{{subsidy_amount}} and {{after_subsidy}}; docxfill.apply_subsidy() keeps them for a Residential project and
removes them for every other project type.

It also replaces the fixed word "COMMERTIAL" in the rates line by {{project_type_caps}}, so the line reads
"... Solar Power for RESIDENTIAL" / "... for COMMERCIAL" according to the project type.
Safe to run again: nothing is added twice.
"""
import copy
import io
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docxfill import _cell_text, _q, process_docx, replace_in_paragraph, set_cell_text  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "docx_template" / "quotation_template.docx"
OLD_WORD = re.compile(r"COMMERTIAL")


def row_text(tr):
    return " ".join(_cell_text(tc).strip() for tc in tr.findall(_q("tc")))


def find_table(root, *needles):
    for tbl in root.iter(_q("tbl")):
        text = "".join(t.text or "" for t in tbl.iter(_q("t")))
        if all(n in text for n in needles):
            return tbl
    return None


def source_rows(residential_docx):
    root = etree.fromstring(zipfile.ZipFile(residential_docx).read("word/document.xml"))
    tbl = find_table(root, "Sub-Total", "Amount After Subsidy")
    if tbl is None:
        raise SystemExit("The Word file has no rates table with 'Subsidy' and 'Amount After Subsidy' rows.")
    rows = {}
    for tr in tbl.findall(_q("tr")):
        first = row_text(tr)
        if first.startswith("Subsidy"):
            rows["subsidy"] = tr
        elif first.startswith("Amount After Subsidy"):
            rows["after"] = tr
    if set(rows) != {"subsidy", "after"}:
        raise SystemExit("Could not find both the 'Subsidy' and the 'Amount After Subsidy' row.")
    return rows


def main(residential_docx, template=TEMPLATE):
    rows = source_rows(residential_docx)
    result = {"rows_added": 0, "word_replaced": 0}

    def transform(part, root):
        if part != "word/document.xml":
            return
        tbl = find_table(root, "Sub-Total (Before GST)", "{{gst_amount}}")
        if tbl is None:
            raise SystemExit("The template has no rates table (Sub-Total / GST rows).")
        if "{{subsidy_amount}}" not in "".join(t.text or "" for t in tbl.iter(_q("t"))):
            gst_row = [tr for tr in tbl.findall(_q("tr")) if row_text(tr).startswith("GST @")][-1]
            anchor = gst_row
            for key, token in (("subsidy", "(-){{subsidy_amount}}/-"), ("after", "{{after_subsidy}}/-")):
                tr = copy.deepcopy(rows[key])
                for mark in list(tr.iter(_q("bookmarkStart"), _q("bookmarkEnd"))):   # ids must stay unique
                    mark.getparent().remove(mark)
                set_cell_text(tr.findall(_q("tc"))[-1], token)
                anchor.addnext(tr)
                anchor = tr
                result["rows_added"] += 1
        for p in root.iter(_q("p")):
            if replace_in_paragraph(p, OLD_WORD, lambda m, _full: "{{project_type_caps}}"):
                result["word_replaced"] += 1

    data = process_docx(io.BytesIO(Path(template).read_bytes()), transform)
    Path(template).write_bytes(data)
    print(f"Template updated: {template}")
    print(f"  subsidy rows added: {result['rows_added']}   'COMMERTIAL' replaced: {result['word_replaced']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
