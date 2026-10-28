# MBTION Quotation Generator (Flask)

Login -> Form -> **Review & Share** -> **PDF quotation** -> Logout.
Quotation ab **PDF** mein milta hai (Download aur Share dono PDF). Design aapke original Word template se hi aata hai (fonts, tables, colours, header/footer sab same);
server us template ko bharke LibreOffice se PDF bana deta hai.
Sirf website ka UI naya hai; 3 pages wahi hain: **Login, Quotation form, Logout**.

## Naya UI (kya kya milega)
- Mobile-first, fully responsive (phone / tablet / laptop), bade touch-friendly buttons, koi Bootstrap/CDN nahi (fast + offline-safe CSS)
- Form ke upar section chips (Customer, Capacity, Specs...) jisse seedha us section par jaa sakte ho, poora hone par chip hara ho jaata hai
- Live **Quotation summary** (laptop par side panel, phone par neeche bar) - total, rate, GST, terms count
- **Review**: Review dabate hi quotation ka preview khulta hai (customer, specs, rates, ticked terms, warranty, contact). Yahin PDF ready ho jaati hai
- **Download PDF**, **Share file** (phone par native share sheet se PDF seedha WhatsApp/Gmail/Drive...), **WhatsApp / Email / Copy** (short summary text)
- PWA ready: phone ke browser se "Add to Home screen" karke app jaisa chala sakte ho (icon, full screen)

> Note: browser se WhatsApp link par file attach nahi ho sakti. Phone par "Share file" use karo (Chrome Android / Safari iOS 15+, HTTPS par).
> Laptop par Download karke file attach karni hoti hai; WhatsApp/Email button sirf summary message bhejte hain.

## Local run
```bash
cd quotation_app
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac / Linux
pip install -r requirements.txt

copy .env.example .env         # Mac/Linux: cp .env.example .env
# .env kholke SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD badal lein

python app.py
```
Browser: http://127.0.0.1:5000 . Agar `.env` nahi banaya to default login `admin / admin123` (sirf testing ke liye).

## Hosting

> **PDF ke liye LibreOffice chahiye**, isliye app ab **Docker** par chalta hai (`Dockerfile` ready hai). **Render** use karo.
> **Vercel par ab PDF nahi banega** (Vercel par LibreOffice install nahi ho sakta) - `vercel.json` ab kaam ka nahi.
> Local run par bhi PDF ke liye LibreOffice install hona chahiye (Windows: libreoffice.org se; Ubuntu: `sudo apt install libreoffice-writer fonts-crosextra-carlito`).
> Free plan par PDF banne mein 5-15 second lag sakte hain (pehli baar zyada).

### Render (Docker, recommended)
1. Project ko GitHub par push karo.
2. Render -> **New -> Blueprint** -> repo select karo (`render.yaml` apne aap padh leta hai; region Singapore hai = India ke sabse paas).
   Ya **New -> Web Service** lo aur **Language: Docker** chuno (Dockerfile apne aap detect hota hai, Build/Start command khali chhodo).
3. Environment mein `ADMIN_USERNAME` aur `ADMIN_PASSWORD` daalo (`SECRET_KEY` Blueprint khud bana deta hai).
4. Deploy ke baad `https://<aapka-app>.onrender.com/readyz` kholo. Teen cheezein `true` honi chahiye: `template`, `pdf_engine`, `login_configured`.
5. Free plan 15 min idle ke baad sleep hota hai, pehla load 30-60 sec le sakta hai (login page par hi PDF engine garam ho jaata hai).

Render par HTTPS apne aap milta hai aur secure cookie apne aap on ho jaati hai. Strong password rakho (ya `.env.example` mein bataya `ADMIN_PASSWORD_HASH` use karo).

### Environment variables
| Name | Zaroori? | Matlab |
|---|---|---|
| `ADMIN_USERNAME` | haan | login username |
| `ADMIN_PASSWORD` | haan | login password. Render par ye na ho to login band rehta hai aur login page par set-up message dikhta hai (default `admin123` host par kabhi kaam nahi karta) |
| `SECRET_KEY` | blueprint bana deta hai | session ki chaabi. Na ho to login ke username/password se stable chaabi ban jaati hai (restart / sleep par logout nahi hota) |
| `ADMIN_PASSWORD_HASH` | optional | plain password ki jagah hash |
| `PDF_TIMEOUT` | optional (60) | ek PDF banne ka max time (seconds) |
| `PDF_LOCK_WAIT` | optional (30) | dusri PDF chal rahi ho to kitna intezaar |
| `PDF_WARMUP` | optional (1) | 1 = server start par PDF engine pehle se garam kar do |
| `LOG_LEVEL` | optional (INFO) | DEBUG / INFO / WARNING / ERROR |

## Error handling (kya kya sambhala gaya hai)
Har error ka seedha insaani message aata hai; server crash (500) ki jagah saaf jawab milta hai. Detail Render -> **Logs** mein jaati hai
aur user ko chhota **ref** milta hai (jaise `ref: 3fa91c0b`), Logs mein usi ref ko search karo.

**Server**
- Galat / ajeeb input (jaise `1e-30`, `nan`, Unicode digits, bahut lamba number, galat JSON) -> 400 + field ke saamne error, crash nahi
- Session expire / CSRF galat -> form ko 401 JSON (login par bhejta hai), browser ko login page; non-ASCII token se bhi crash nahi
- Word template gayab ya kharab -> saaf message + `/readyz` mein `template: false`
- PDF: timeout, LibreOffice crash, kharab output, ya dusri PDF chal rahi ho -> 503 + "thodi der baad try karo"; timeout par LibreOffice ka poora process-tree kill hota hai (RAM nahi phansti)
- Har HTTP error (404, 405, 413, 429...) ke liye friendly page (form/API ke liye JSON); error page khud fail ho to bhi plain text
- Login par 5 galat try ke baad 5 minute ka lock (Render proxy ke peeche asli IP se), `next=` open-redirect block
- Quotation ki date India time (IST) mein; Render UTC par chalta hai
- Gujarati / Hindi naam-address PDF mein sahi chhapte hain (Docker image mein Noto fonts)

**Browser**
- Internet band hone par, server ke jaldi na jawab dene par (100 sec timeout), ya Render sleep se jaag raha ho to alag-alag saaf message; "Download" dobara dabane se retry
- Session expire hone par form mein bhari hui entries save hoke login ke baad wapas aa jaati hain (sirf us tab mein, 30 min tak)
- Server ke jo errors kisi field se match nahi karte wo bhi dikhte hain (chup nahi rehte)
- Page ka koi script error aaye to bhi toast aata hai; service worker offline mein crash nahi karta

### Kuch galat ho to
| Dikhta hai | Matlab / kya karein |
|---|---|
| Login page par "Login is not set up yet" | Render -> Environment mein `ADMIN_USERNAME`, `ADMIN_PASSWORD` daalo, redeploy |
| "The server is starting up or busy" | Free plan jaag raha hai. 30 sec ruk ke Download dobara dabao |
| "The server is busy making another quotation" | Ek saath do PDF; kuch second baad dobara |
| "PDF creation took too long" | Pehli baar slow ho sakta hai; dobara try karo. Roz hota ho to `PDF_TIMEOUT` badhao ya paid plan lo |
| "Word template is missing or damaged" | `docx_template/quotation_template.docx` wapas rakho; `/readyz` se confirm karo |
| "Something went wrong (ref: ...)" | Render -> Logs mein ref search karo |

### Tests
```bash
python -m unittest discover -s tests -v
```
27 checks (galat input, session, template, PDF engine, login lock, asli PDF). LibreOffice na ho to asli-PDF wala test apne aap skip ho jaata hai.

## Future mobile app
- Abhi: PWA (Home screen par install). Android/iOS store app chahiye to isi site ko Capacitor/TWA se wrap kar sakte ho, backend wahi rahega.
- Backend endpoints: `POST /api/calc` (pricing), `POST /generate` (PDF), `GET /healthz`. Native app ke liye token-based login (JWT) aur `/api/...` JSON endpoints baad mein aasaani se jode ja sakte hain.

## Pricing kaise calculate hota hai
Aapke sample quotation ke numbers (5,23,000 / 31,236.24 / 4,80,257.19 / 42,742.89) exactly match karte hain.
- **Total pata hai**: total (GST ke saath) daalo -> rate = total / 1.089 / kW, amount = kW x rate, GST = 8.9% of amount.
- **Rate pata hai**: rate/kW daalo -> amount, GST aur total (nearest rupee) nikalte hain.
- **Manual**: saare amounts khud type karo.
Numbers Indian format (lakh/crore commas) mein print hote hain. GST % `quotation.py` mein `GST_PERCENT` se badal sakte hain
(Word template mein "GST @ 8.9%" text fixed hai, use bhi Word mein edit karna hoga).

## Terms & Conditions (tick / untick)
Form ke section 05 mein Word template ki saari Terms & Conditions checkbox ke saath dikhti hain, shuru mein **sab tick** hoti hain.
Jo term nahi chahiye use untick karein (ya "Deselect all" dabake sirf kuch tick karein). Quotation mein sirf tick wali terms aati hain
aur Sr. number apne aap 1, 2, 3... ho jaata hai. Row ki formatting (borders, yellow highlight) bilkul same rehti hai.
Kam se kam ek term tick honi chahiye (ya ek extra condition likhi honi chahiye). Term ka text form mein template se hi aata hai, isliye Word template mein text badlenge to form mein bhi badal jayega.

### Highlight (optional)
Har T&C line ke saath **Highlight** ka tick hai (extra conditions ke saath bhi). Tick karoge to quotation mein **poori row light orange**
(`FFF2CC`) ho jayegi; tick nahi karoge to row normal rehti hai. Form mein bhi row usi rang mein dikhti hai. Rang badalna ho to
`docxfill.py` mein `HIGHLIGHT_FILL` aur `static/css/style.css` ke `#FFF2CC` badlo. Untick ki hui term par highlight ka koi asar nahi hota.

### Extra conditions (17, 18, 19, 20)
Terms & Conditions ke neeche **Additional conditions** ke 4 boxes hain. Jo likhoge wo tick ki hui terms ke baad agle number se print hoga
(sab 16 tick hon to 17, 18, 19, 20; 3 terms untick karoge to 14, 15...). Khali box print nahi hota. Zyada se zyada 4 conditions, har ek 200 characters tak.
Ye Word template ke T&C table ki aakhri 4 khali rows mein jaati hain. Agar kam/zyada slot chahiye to Word mein khali rows kam/zyada karke
`python tools/build_template.py "naya_word_file.docx"` chalao, aur `CUSTOM_TERMS_MAX` (quotation.py) badlo.

### Warranty hamesha next page par
T&C kitni bhi rows ki ho (1 se 20), "06 Warranty Details" hamesha page 3 se shuru hota hai. Iske liye template mein T&C table ke baad
ke pehle khali paragraph par "page break before" laga hai (`tools/build_template.py` ye apne aap lagata hai).

## Word template badalna
`docx_template/quotation_template.docx` hi quotation ka design hai. Isse Word mein khol ke koi bhi text/format badal sakte hain.
Sirf `{{field_name}}` wale tokens ko mat todna. Fields: project_type, customer_name, customer_address, module_qty,
module_wp, capacity_kw, module_make, inverter_make, inverter_kw, acdb_make, dcdb_make, cable_make, cable_ac_mm, cable_dc_mm,
earth_make, earth_mm, la_make, structure_make, structure_height, conduit_make, rate, amount, subtotal, gst_amount,
total_cost, warranty_panel, warranty_inverter, contact_name, contact_mobile, custom_term_1 ... custom_term_4.

Naya field jodne ke liye: (1) Word template mein `{{naya_field}}` likho, (2) `templates/form.html` mein input jodo (name="naya_field"),
(3) `quotation.py` ke `TEXT_FIELDS` aur `DEFAULTS` mein add karo.

## Files
- `app.py` - routes: /login, /quotation, /generate, /api/calc, /logout, /healthz, /readyz, error handlers, PWA (/manifest.webmanifest, /sw.js), security (CSRF, CSP, session)
- `quotation.py` - validation + pricing + number format
- `pdfconv.py` - filled Word file -> PDF (LibreOffice)
- `Dockerfile` - LibreOffice + fonts ke saath server image (normal user, healthcheck)
- `gunicorn.conf.py` - server settings (1 worker, 4 threads, logs stdout par)
- `tests/test_app.py` - automatic checks
- `docxfill.py` - Word template fill + Terms & Conditions rows select/renumber (formatting touch nahi karta)
- `templates/` - `base.html`, `login.html`, `form.html`, `logout.html`, `_icons.html`
- `static/css/style.css` - poora design system (colours, buttons, form, review sheet)
- `static/js/form.js` - pricing, terms, validation, **review + download + share**; `login.js`, `pwa.js`, `sw.js`; `templates/error.html` friendly error page
- `vercel.json`, `render.yaml`, `Procfile` - hosting config
- `tools/build_template.py` - original `<...>` wali Word file se template banane ka tool (ek baar chala chuka hai)

## Brand colours badalna
`static/css/style.css` ke sabse upar `:root` mein `--navy`, `--orange`, `--hl` badlo, poori site ka rang badal jaayega.
