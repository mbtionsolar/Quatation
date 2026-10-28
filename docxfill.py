"""Run-aware placeholder replacement for .docx files.

Word splits text into many <w:r> runs, so a placeholder like <15.375> is often
stored as '<' + '15.375' + '>' in three runs. We work on the joined paragraph
text, then write the replacement into ONE run and blank the rest. No run
properties are touched, so fonts, colours, sizes and table layout stay exactly
as designed.
"""
import copy
import io
import re
import zipfile

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PARTS = re.compile(r"^word/(document|header\d*|footer\d*)\.xml$")
TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
HIGHLIGHT_FILL = "FFF2CC"      # light orange used to highlight a whole Terms & Conditions row


def own_text_nodes(p):
    """<w:t> nodes belonging to paragraph p (not to a nested text-box paragraph)."""
    out = []
    for t in p.iter("{%s}t" % W):
        if next(t.iterancestors("{%s}p" % W)) is p:
            out.append(t)
    return out


def replace_in_paragraph(p, regex, repl_fn, anchor_offset=0):
    """Replace every regex match in paragraph p. repl_fn(match, paragraph_text) -> str."""
    ts = own_text_nodes(p)
    texts = [t.text or "" for t in ts]
    full = "".join(texts)
    matches = list(regex.finditer(full))
    if not matches:
        return False
    owner = [i for i, x in enumerate(texts) for _ in x]
    out = list(full)
    for m in matches:
        s, e = m.span()
        for pos in range(s, e):
            out[pos] = ""
        anchor = min(s + anchor_offset, e - 1)
        out[anchor] = repl_fn(m, full)
    new = [[] for _ in texts]
    for pos, ch in enumerate(out):
        new[owner[pos]].append(ch)
    for t, old, parts in zip(ts, texts, new):
        value = "".join(parts)
        if value != old:
            t.text = value
            t.set(XML_SPACE, "preserve")
    return True


def process_docx(src, transform):
    """Copy a docx, passing each document/header/footer XML root to transform(name, root)."""
    zin = zipfile.ZipFile(src)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if PARTS.match(item.filename):
                root = etree.fromstring(data)
                transform(item.filename, root)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(item, data)
    return buf.getvalue()


def _q(tag):
    return "{%s}%s" % (W, tag)


def _cell_text(tc):
    return "".join(t.text or "" for t in tc.iter(_q("t")))


CUSTOM_SLOT = re.compile(r"\{\{\s*custom_term_(\d+)\s*\}\}")


def set_cell_text(tc, text):
    """Write text into a table cell, re-using the cell's existing run (so its font/size stay),
    also when the cell is blank and has no <w:t> yet."""
    p = tc.find(_q("p"))
    ts = own_text_nodes(p)
    if ts:
        ts[0].text = text
        ts[0].set(XML_SPACE, "preserve")
        for extra in ts[1:]:
            extra.text = ""
        return
    r = p.find(_q("r"))
    if r is None:
        r = etree.SubElement(p, _q("r"))
        ppr_rpr = p.find("%s/%s" % (_q("pPr"), _q("rPr")))
        if ppr_rpr is not None:
            r.insert(0, copy.deepcopy(ppr_rpr))
    t = etree.SubElement(r, _q("t"))
    t.text = text
    t.set(XML_SPACE, "preserve")


def add_page_break_before(p):
    """Make paragraph p start on a new page (no extra page if it already starts one)."""
    ppr = p.find(_q("pPr"))
    if ppr is None:
        ppr = etree.Element(_q("pPr"))
        p.insert(0, ppr)
    if ppr.find(_q("pageBreakBefore")) is not None:
        return
    pos = 0
    for i, child in enumerate(ppr):      # schema order: pStyle, keepNext, keepLines, pageBreakBefore, ...
        if child.tag in (_q("pStyle"), _q("keepNext"), _q("keepLines")):
            pos = i + 1
    ppr.insert(pos, etree.Element(_q("pageBreakBefore")))


def shade_row(row, fill=HIGHLIGHT_FILL):
    """Light background on every cell of a table row (whole-row highlight)."""
    before_shd = {_q(t) for t in ("cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders")}
    for tc in row.findall(_q("tc")):
        tcpr = tc.find(_q("tcPr"))
        if tcpr is None:
            tcpr = etree.Element(_q("tcPr"))
            tc.insert(0, tcpr)
        shd = tcpr.find(_q("shd"))
        if shd is None:
            shd = etree.Element(_q("shd"))
            pos = 0
            for i, child in enumerate(tcpr):      # schema order: ... tcBorders, shd, noWrap, tcMar, ..., vAlign
                if child.tag in before_shd:
                    pos = i + 1
            tcpr.insert(pos, shd)
        shd.set(_q("val"), "clear")
        shd.set(_q("color"), "auto")
        shd.set(_q("fill"), fill)


def find_terms_table(root):
    """The Terms & Conditions table = the one whose header row reads 'Sr.' | 'Terms & Conditions'."""
    for tbl in root.iter(_q("tbl")):
        rows = tbl.findall(_q("tr"))
        if len(rows) > 1:
            head = [_cell_text(tc).strip().lower() for tc in rows[0].findall(_q("tc"))]
            if head == ["sr.", "terms & conditions"]:
                return tbl
    return None


def split_term_rows(tbl):
    """(standard term rows, extra-condition slot rows) - a slot row holds a {{custom_term_N}} token."""
    standard, slots = [], []
    for row in tbl.findall(_q("tr"))[1:]:
        text = _cell_text(row.findall(_q("tc"))[1])
        (slots if CUSTOM_SLOT.search(text) else standard).append(row)
    return standard, slots


def list_terms(template_path):
    """Text of every standard term (in order) as written in the template, so the form always matches the Word file."""
    root = etree.fromstring(zipfile.ZipFile(template_path).read("word/document.xml"))
    tbl = find_terms_table(root)
    if tbl is None:
        return []
    return [_cell_text(row.findall(_q("tc"))[1]).strip() for row in split_term_rows(tbl)[0]]


def count_custom_slots(template_path):
    root = etree.fromstring(zipfile.ZipFile(template_path).read("word/document.xml"))
    tbl = find_terms_table(root)
    return len(split_term_rows(tbl)[1]) if tbl is not None else 0


def apply_terms(root, keep=None, custom_count=0, highlight=None, custom_highlight=None):
    """Keep only the 1-based standard term numbers in `keep` (None = all) plus the first `custom_count`
    extra-condition rows; renumber the Sr. column 1..n in one sequence.
    `highlight` = standard term numbers to shade, `custom_highlight` = 1-based extra-condition positions to shade.
    Rows are removed whole, so every remaining row keeps its own formatting."""
    tbl = find_terms_table(root)
    if tbl is None:
        return
    standard, slots = split_term_rows(tbl)
    all_rows = standard + slots
    if not all_rows:
        return
    # the first data row has no top border (header's coloured line shows through) - remember it
    first_tops = [copy.deepcopy(tc.find("%s/%s/%s" % (_q("tcPr"), _q("tcBorders"), _q("top"))))
                  for tc in all_rows[0].findall(_q("tc"))]
    keep = set(range(1, len(standard) + 1)) if keep is None else set(keep)
    kept = [r for i, r in enumerate(standard, 1) if i in keep] + slots[:max(0, custom_count)]
    if not kept:
        raise ValueError("At least one term must be kept.")
    for r in all_rows:
        if r not in kept:
            tbl.remove(r)
    for n, r in enumerate(kept, 1):
        set_cell_text(r.findall(_q("tc"))[0], str(n))
    for i in (highlight or ()):                       # only rows that are actually printed
        if i in keep and 1 <= i <= len(standard):
            shade_row(standard[i - 1])
    for k in (custom_highlight or ()):
        if 1 <= k <= custom_count and k <= len(slots):
            shade_row(slots[k - 1])
    if kept[0] is not all_rows[0]:
        for tc, top in zip(kept[0].findall(_q("tc")), first_tops):
            borders = tc.find("%s/%s" % (_q("tcPr"), _q("tcBorders")))
            old = borders.find(_q("top")) if borders is not None else None
            if old is not None and top is not None:
                borders.replace(old, top)
            elif old is not None and top is None:
                borders.remove(old)


def fill_docx(template_path, values, keep_terms=None, custom_count=0, highlight_terms=None, highlight_custom=None):
    """Fill {{key}} tokens in the template. Unknown tokens become empty.
    keep_terms: optional iterable of 1-based standard Terms & Conditions numbers to keep (None = keep all).
    custom_count: how many of the extra-condition rows (custom_term_1..N) are filled; the rest are removed.
    highlight_terms / highlight_custom: standard term numbers / extra-condition positions to shade light orange."""
    clean = {k: BAD_XML_CHARS.sub("", str(v)) for k, v in values.items()}

    def transform(name, root):
        if name == "word/document.xml":
            apply_terms(root, keep_terms, custom_count, highlight_terms, highlight_custom)
        for p in root.iter("{%s}p" % W):
            replace_in_paragraph(p, TOKEN, lambda m, _full: clean.get(m.group(1), ""))

    return process_docx(template_path, transform)
