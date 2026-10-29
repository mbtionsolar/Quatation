"""Form validation, pricing maths and Indian number formatting for the quotation."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

GST_PERCENT = Decimal("8.9")      # same blended GST shown in the Word template ("GST @ 8.9%")

DEFAULTS = {
    "project_type": "", "customer_name": "", "customer_address": "",
    "module_qty": "", "module_wp": "", "capacity_kw": "",
    "module_make": "", "inverter_make": "", "inverter_qty": "01", "inverter_kw": "",
    "acdb_make": "Havells", "dcdb_make": "Havells",
    "cable_make": "Polycab", "cable_ac_mm": "", "cable_dc_mm": "",
    "earth_make": "Polycab", "earth_mm": "", "la_make": "Polytech", "la_mm": "",
    "structure_make": "Apollo/Hindustar", "gi_pipe": "40X40 & 40X60", "thickness": "2",
    "structure_height": "6/4", "conduit_make": "Polycab",
    "pricing_mode": "total_to_rate", "rate": "", "amount": "", "gst_amount": "", "total_cost": "",
    "warranty_panel": "WAAREE 615Wp", "warranty_panel_years": "25",
    "warranty_inverter": "Solaryaan 15.00 KW", "warranty_inverter_years": "07", "warranty_service_years": "05",
    "contact_name": "JANAK JOSHI", "contact_mobile": "8320565108",
}

TEXT_FIELDS = {  # name -> (label, max length)
    "project_type": ("Project type", 40), "customer_name": ("Customer name", 120),
    "customer_address": ("Customer address", 250), "module_make": ("Module make", 80),
    "inverter_make": ("Inverter make", 80), "acdb_make": ("ACDB make", 80),
    "dcdb_make": ("DCDB make", 80), "cable_make": ("Cable make", 80),
    "earth_make": ("Earthing cable make", 80), "la_make": ("LA cable make", 80),
    "structure_make": ("Structure make", 80), "gi_pipe": ("GI pipe", 40),
    "structure_height": ("Structure height", 20),
    "conduit_make": ("PVC conduit pipe make", 80), "warranty_panel": ("Warranty - panel", 80),
    "warranty_inverter": ("Warranty - inverter", 80), "contact_name": ("Contact person", 60),
}
NUMBER_TEXT_FIELDS = {  # printed as typed, but must look like a number
    "inverter_qty": "Inverter quantity", "inverter_kw": "Inverter capacity (kW)",
    "cable_ac_mm": "AC cable size (mm)", "cable_dc_mm": "DC cable size (mm)",
    "earth_mm": "Earthing cable size (mm)", "la_mm": "LA cable size (mm)",
    "thickness": "Structure thickness (mm)",
    "warranty_panel_years": "Solar panel warranty (years)",
    "warranty_inverter_years": "Inverter warranty (years)",
    "warranty_service_years": "Service warranty (years)",
}
MODES = ("total_to_rate", "rate_to_total", "manual")
CAPACITY_MAX = Decimal("100000")      # kW
CUSTOM_TERMS_MAX = 4        # blank rows available in the Word template (after term 16)
CUSTOM_TERM_LEN = 200
_NUM = re.compile(r"^[0-9]{1,9}(\.[0-9]{1,6})?$")
INT6 = re.compile(r"^[0-9]{1,6}$")          # whole numbers (ASCII digits only, so int() can never fail)
MOBILE = re.compile(r"^[0-9]{10}$")


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_amount(value, label, lo=Decimal("0.01"), hi=Decimal("1e11")):
    """Text -> Decimal, or ValueError with a message that is safe to show to the user."""
    text = re.sub(r"[,\s₹]", "", value or "").rstrip("/-")
    if not text or len(text) > 30:
        raise ValueError(f"{label} must be a number.")
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{label} must be a number.")
    if not d.is_finite():
        raise ValueError(f"{label} must be a number.")
    if d <= 0:
        raise ValueError(f"{label} must be greater than 0.")
    if d < lo:
        raise ValueError(f"{label} is too small.")
    if d >= hi:
        raise ValueError(f"{label} is too large.")
    return d


def q2(d):
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_inr(value, places=2):
    """4,80,257.19 style (lakh/crore grouping)."""
    q = Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole, _, frac = f"{abs(q):.{places}f}".partition(".")
    if len(whole) > 3:
        whole = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", whole[:-3]) + "," + whole[-3:]
    return ("-" if q < 0 else "") + whole + (("." + frac) if places else "")


def fmt_total(d):
    return fmt_inr(d, 0 if d == d.to_integral_value() else 2)


def compute_pricing(mode, capacity, form):
    """Returns (strings_for_template, {field: error}). Mirrors how the sample quote was made:
    total_to_rate: enter the round total -> rate is back-calculated.
    rate_to_total: enter rate per kW -> total is calculated and rounded to the rupee."""
    gst = GST_PERCENT / 100
    try:
        if mode == "total_to_rate":
            total = parse_amount(form.get("total_cost"), "Total project cost")
            rate = q2(total / (1 + gst) / capacity)
            if rate <= 0:
                raise ValueError("Total project cost is too small for this capacity.")
            amount = q2(capacity * rate)
            gst_amt = q2(amount * gst)
        elif mode == "rate_to_total":
            rate = parse_amount(form.get("rate"), "Rate per kW")
            amount = q2(capacity * rate)
            gst_amt = q2(amount * gst)
            total = (amount + gst_amt).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        else:  # manual
            rate = parse_amount(form.get("rate"), "Rate per kW")
            amount = parse_amount(form.get("amount"), "Amount")
            gst_amt = parse_amount(form.get("gst_amount"), "GST amount")
            total = parse_amount(form.get("total_cost"), "Total project cost")
        if amount >= Decimal("1e12") or total >= Decimal("1e12"):
            raise ValueError("Amount is too large. Please check the capacity and rate.")
    except ArithmeticError:           # decimal overflow / invalid operation on absurd numbers
        field = {"total_to_rate": "total_cost", "rate_to_total": "rate"}.get(mode, "rate")
        return None, {field: "These numbers are out of range. Please check the capacity and amount."}
    except ValueError as exc:
        field = {"total_to_rate": "total_cost", "rate_to_total": "rate"}.get(mode)
        msg = str(exc)
        if mode == "manual":
            field = next((f for f, l in (("rate", "Rate"), ("amount", "Amount"), ("gst_amount", "GST"),
                                         ("total_cost", "Total")) if msg.startswith(l)), "rate")
        return None, {field: msg}
    return {
        "rate": fmt_inr(rate), "amount": fmt_inr(amount), "subtotal": fmt_inr(amount),
        "gst_amount": fmt_inr(gst_amt), "total_cost": fmt_total(total),
    }, {}


def parse_custom_terms(form):
    """Filled extra conditions, packed in order: ([(text, highlighted), ...], {field: error})."""
    items, errors = [], {}
    for i in range(1, CUSTOM_TERMS_MAX + 1):
        text = clean(form.get(f"custom_term_{i}"))
        if len(text) > CUSTOM_TERM_LEN:
            errors[f"custom_term_{i}"] = f"Condition is too long (max {CUSTOM_TERM_LEN} characters)."
        if text:
            items.append((text, bool(form.get(f"custom_hl_{i}"))))
    return items, errors


def build_values(form):
    """Validate the submitted form. Returns (values_for_template, errors)."""
    values, errors = {}, {}
    for name, (label, limit) in TEXT_FIELDS.items():
        v = clean(form.get(name))
        if not v:
            errors[name] = f"{label} is required."
        elif len(v) > limit:
            errors[name] = f"{label} is too long (max {limit} characters)."
        values[name] = v
    for name, label in NUMBER_TEXT_FIELDS.items():
        v = clean(form.get(name))
        if not _NUM.match(v):
            errors[name] = f"{label} must be a number."
        values[name] = v

    qty = clean(form.get("module_qty"))
    if not INT6.match(qty) or int(qty) <= 0:
        errors["module_qty"] = "Number of modules must be a whole number."
    values["module_qty"] = qty

    wp = clean(form.get("module_wp"))
    if not INT6.match(wp) or not 1 <= int(wp) <= 2000:
        errors["module_wp"] = "Module wattage must be a whole number (Wp), e.g. 615."
    values["module_wp"] = wp

    cap_text = clean(form.get("capacity_kw"))
    capacity = None
    try:
        capacity = parse_amount(cap_text, "Project capacity", hi=CAPACITY_MAX)
    except ValueError as exc:
        errors["capacity_kw"] = str(exc)
    values["capacity_kw"] = cap_text

    # extra conditions: blanks are skipped and the filled ones are packed into the first rows
    items, cerrors = parse_custom_terms(form)
    errors.update(cerrors)
    for i in range(1, CUSTOM_TERMS_MAX + 1):
        values[f"custom_term_{i}"] = items[i - 1][0] if i <= len(items) else ""

    mobile = re.sub(r"[\s-]", "", form.get("contact_mobile") or "")
    if not MOBILE.match(mobile):
        errors["contact_mobile"] = "Enter a 10 digit mobile number."
    values["contact_mobile"] = mobile

    mode = form.get("pricing_mode") if form.get("pricing_mode") in MODES else "total_to_rate"
    if capacity is not None:
        pricing, perr = compute_pricing(mode, capacity, form)
        errors.update(perr)
        if pricing:
            values.update(pricing)
    return values, errors
