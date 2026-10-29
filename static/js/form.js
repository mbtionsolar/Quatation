/* MBTION Quotation Generator - form logic (pricing, terms, review, download, share) */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const app = $('#app');
  const cfg = { calcUrl: app.dataset.calcUrl, loginUrl: app.dataset.loginUrl, gst: app.dataset.gst };
  const form = $('#quotation-form');
  const csrf = $('meta[name="csrf-token"]').content;
  const alertBox = $('#alert-box');
  const hint = $('#calc-hint');
  const hintText = hint.textContent;
  const f = (name) => form.elements[name];
  const val = (name) => (f(name) ? f(name).value.trim() : '');
  const touched = new Set();               // auto-filled fields the user has edited by hand
  const PDF_MIME = 'application/pdf';

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const inrFmt = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });
  function inr(v) {
    const raw = String(v || '').replace(/[,\s₹]/g, '').replace(/\/-$/, '');
    const n = parseFloat(raw);
    return raw !== '' && isFinite(n) ? inrFmt.format(n) : '';
  }
  const money = (v) => (inr(v) ? '₹ ' + inr(v) : '');

  const SVG = {
    alert: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    check: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>',
    x: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    phone: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
  };

  // ---------- toast ----------
  function toast(text, type = '') {
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'err' ? ' err' : '');
    el.innerHTML = (type === 'err' ? SVG.alert : SVG.check) + '<span></span>';
    el.lastChild.textContent = text;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  // ---------- safety net: unexpected script errors never fail silently ----------
  let lastCrash = 0;
  function crashed(msg) {
    if (/ResizeObserver|Script error/i.test(String(msg || ''))) return;
    if (Date.now() - lastCrash < 8000) return;
    lastCrash = Date.now();
    toast('Something went wrong on this page. Please refresh and try again.', 'err');
  }
  window.addEventListener('error', (e) => crashed(e.message));
  window.addEventListener('unhandledrejection', (e) => crashed(e.reason && e.reason.message));

  function friendly(status, serverMsg) {
    if (serverMsg) return serverMsg;
    if (status === 413) return 'The data is too large. Please shorten the text and try again.';
    if (status === 429) return 'Too many requests. Please wait a moment and try again.';
    if (status === 502 || status === 503 || status === 504) return 'The server is starting up or busy. Please wait about 30 seconds and tap Download again.';
    if (status >= 500) return 'Server error. Please try again.';
    return 'Request failed (' + status + '). Please try again.';
  }

  // ---------- session expiry: keep what the user typed ----------
  const DRAFT_KEY = 'mbtion-quote-draft-v1';
  function saveDraft() {
    try {
      const pairs = [...new FormData(form)].filter(([k, v]) => k !== 'csrf_token' && typeof v === 'string');
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ t: Date.now(), pairs }));
    } catch (_) { /* storage blocked - nothing to save */ }
  }
  function sessionExpired() {
    saveDraft();
    toast('Session expired. Taking you to login - your entries will be restored.', 'err');
    setTimeout(() => { location.href = cfg.loginUrl + '?next=' + encodeURIComponent('/quotation'); }, 1200);
  }
  function restoreDraft() {
    let raw = null;
    try { raw = sessionStorage.getItem(DRAFT_KEY); sessionStorage.removeItem(DRAFT_KEY); } catch (_) { return false; }
    if (!raw) return false;
    try {
      const draft = JSON.parse(raw);
      if (!draft || !Array.isArray(draft.pairs) || Date.now() - draft.t > 30 * 60 * 1000) return false;
      const by = {};
      draft.pairs.forEach(([k, v]) => { (by[k] = by[k] || []).push(String(v)); });
      [...form.elements].forEach((el) => {
        if (!el.name || el.name === 'csrf_token' || ['hidden', 'button', 'submit', 'reset'].includes(el.type)) return;
        const vals = by[el.name];
        if (el.type === 'checkbox' || el.type === 'radio') el.checked = !!vals && vals.includes(el.value);
        else if (vals) el.value = vals[0];
      });
      ['capacity_kw', 'warranty_panel', 'warranty_inverter'].forEach((n) => { if (val(n)) touched.add(n); });
      setTimeout(() => toast('Your entries were restored.'), 400);
      return true;
    } catch (_) { return false; }
  }

  // ---------- alerts / field errors ----------
  function showAlert(type, text) {
    alertBox.innerHTML = '';
    const el = document.createElement('div');
    el.className = `alert alert-${type}`;
    el.setAttribute('role', 'alert');
    el.innerHTML = (type === 'success' ? SVG.check : SVG.alert) + '<span></span><button type="button" class="alert-close" aria-label="Dismiss">' + SVG.x + '</button>';
    el.children[1].textContent = text;
    el.lastChild.addEventListener('click', () => el.remove());
    alertBox.appendChild(el);
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function clearErrors() {
    alertBox.innerHTML = '';
    $$('.is-invalid', form).forEach((el) => el.classList.remove('is-invalid'));
    $$('.js-error', form).forEach((el) => el.remove());
  }
  function clearFieldError(el) {
    el.classList.remove('is-invalid');
    const host = el.closest('.field');
    if (host) $$('.js-error', host).forEach((e) => e.remove());
  }
  function showFieldErrors(errors, banner = true) {
    let first = null;
    const loose = [];                       // messages with no matching input must still be shown
    Object.entries(errors).forEach(([name, msg]) => {
      const isTerms = name === 'terms';
      const el = isTerms ? $('#terms-box') : f(name);
      if (!el || el.length) { loose.push(msg); return; }
      el.classList.add('is-invalid');
      const msgEl = document.createElement('div');
      msgEl.className = 'field-error js-error';
      msgEl.textContent = msg;
      if (isTerms) el.after(msgEl); else (el.closest('.field') || el.parentNode).appendChild(msgEl);
      first = first || el;
    });
    if (banner) {
      const text = ((first ? 'Please correct the highlighted fields. ' : '') + loose.join(' ')).trim();
      showAlert('danger', text || 'Please correct the highlighted fields.');
    }
    if (first) {
      first.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (first.focus && first.id !== 'terms-box') setTimeout(() => first.focus({ preventScroll: true }), 350);
    }
  }
  form.addEventListener('input', (e) => { if (e.target.classList && e.target.classList.contains('is-invalid')) clearFieldError(e.target); });

  // ---------- auto-fill helpers ----------
  const firstWord = (s) => s.trim().split(/\s+/)[0] || '';
  function syncAuto() {
    const qty = parseInt(f('module_qty').value, 10), wp = parseInt(f('module_wp').value, 10);
    if (!touched.has('capacity_kw')) {
      f('capacity_kw').value = qty > 0 && wp > 0 ? String(parseFloat((qty * wp / 1000).toFixed(3))) : '';
    }
    if (!touched.has('warranty_panel')) {
      const brand = firstWord(f('module_make').value);
      f('warranty_panel').value = brand && wp > 0 ? `${brand} ${wp}Wp` : '';
    }
    if (!touched.has('warranty_inverter')) {
      const make = f('inverter_make').value.trim(), kw = f('inverter_kw').value.trim();
      f('warranty_inverter').value = make && kw ? `${make} ${kw} KW` : '';
    }
    recalc();
  }
  ['capacity_kw', 'warranty_panel', 'warranty_inverter'].forEach((n) =>
    f(n).addEventListener('input', () => (f(n).value.trim() ? touched.add(n) : touched.delete(n))));
  ['module_qty', 'module_wp', 'module_make', 'inverter_make', 'inverter_kw'].forEach((n) => f(n).addEventListener('input', syncAuto));

  // ---------- pricing ----------
  const mode = () => form.querySelector('[name="pricing_mode"]:checked').value;
  function applyMode() {
    const m = mode();
    f('total_cost').readOnly = m === 'rate_to_total';
    f('rate').readOnly = m === 'total_to_rate';
    f('amount').readOnly = f('gst_amount').readOnly = m !== 'manual';
    f('amount').required = f('gst_amount').required = m === 'manual';
    recalc();
  }
  let timer, seq = 0;
  function setHint(text, isErr) { hint.textContent = text; hint.className = 'calc-hint' + (isErr ? ' err' : ''); }
  function recalc() {
    clearTimeout(timer);
    const m = mode();
    if (m === 'manual') { setHint('Manual mode: type all amounts exactly as they should appear.'); updateSummary(); return; }
    timer = setTimeout(async () => {
      const mine = ++seq;
      const driver = m === 'total_to_rate' ? 'total_cost' : 'rate';
      const computed = m === 'total_to_rate' ? ['rate', 'amount', 'gst_amount'] : ['total_cost', 'amount', 'gst_amount'];
      try {
        const r = await fetch(cfg.calcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
          body: JSON.stringify({ mode: m, capacity_kw: f('capacity_kw').value, rate: f('rate').value, total_cost: f('total_cost').value }),
        });
        if (r.status === 401) { sessionExpired(); return; }
        const d = await r.json();
        if (mine !== seq) return;
        if (d.error) {
          computed.forEach((n) => (f(n).value = ''));
          const typed = f(driver).value.trim() !== '';
          setHint(typed ? d.error : hintText, typed);
        } else {
          computed.forEach((n) => (f(n).value = d[n]));
          setHint(hintText);
        }
      } catch (_) {
        // preview only - the server recalculates on submit
        if (mine === seq) setHint('Could not reach the server. Amounts are calculated again when you download.', false);
      }
      updateSummary();
    }, 250);
  }
  $$('[name="pricing_mode"]', form).forEach((el) => el.addEventListener('change', applyMode));
  ['total_cost', 'rate'].forEach((n) => f(n).addEventListener('input', () => mode() !== 'manual' && recalc()));
  f('capacity_kw').addEventListener('input', recalc);

  // ---------- terms & conditions ----------
  const termItems = () => $$('.term', form);
  const termBoxes = () => $$('input[name="terms"]', form);
  const customRows = () => $$('.custom-row', form);
  const customInput = (row) => $('input[type="text"]', row);
  const customFilled = () => customRows().filter((r) => customInput(r).value.trim()).length;
  const termsOn = () => termBoxes().filter((b) => b.checked).length;

  function updateTerms() {
    const all = termBoxes(), on = termsOn(), extra = customFilled();
    let n = 0;
    termItems().forEach((item) => {
      const main = $('input[name="terms"]', item), hl = $('input[name="highlight"]', item);
      item.classList.toggle('is-off', !main.checked);
      $('.term-no', item).textContent = main.checked ? ++n : '–';
      hl.disabled = !main.checked;
      item.classList.toggle('is-hl', main.checked && hl.checked);
    });
    const badge = $('#terms-count');
    badge.textContent = `${on} of ${all.length} selected` + (extra ? ` + ${extra} extra` : '');
    badge.classList.toggle('is-bad', on + extra === 0);
    let filledSoFar = 0, emptySoFar = 0;
    customRows().forEach((row) => {
      const inp = customInput(row), tag = $('.custom-no', row), hl = $('input[type="checkbox"]', row), filled = inp.value.trim() !== '';
      tag.textContent = filled ? on + ++filledSoFar : on + extra + ++emptySoFar;
      tag.classList.toggle('is-idle', !filled);
      row.classList.toggle('is-hl', hl.checked && filled);
    });
    if (on + extra > 0) { $('#terms-box').classList.remove('is-invalid'); $$('#terms-box + .js-error').forEach((e) => e.remove()); }
    updateSummary();
  }
  $('#terms-box').addEventListener('change', updateTerms);
  $$('.custom-row input', form).forEach((i) => i.addEventListener(i.type === 'checkbox' ? 'change' : 'input', updateTerms));
  $('#terms-all').addEventListener('click', () => { termBoxes().forEach((b) => (b.checked = true)); updateTerms(); });
  $('#terms-none').addEventListener('click', () => { termBoxes().forEach((b) => (b.checked = false)); updateTerms(); });

  // ---------- live summary + section progress ----------
  function setSum(key, text, empty) {
    $$(`[data-sum="${key}"]`).forEach((el) => { el.textContent = text; el.classList.toggle('is-empty', !!empty); });
  }
  function updateSummary() {
    const name = val('customer_name'), kw = val('capacity_kw');
    setSum('customer', name || 'Customer name…', !name);
    setSum('project', [val('project_type') && `${val('project_type')} Rooftop Solar`, kw && `${kw} kW`].filter(Boolean).join(' · '));
    setSum('total', money(f('total_cost').value) || '₹ –');
    setSum('capacity', kw ? `${kw} kW` : '–');
    setSum('rate', money(f('rate').value) || '–');
    setSum('amount', money(f('amount').value) || '–');
    setSum('gst', money(f('gst_amount').value) || '–');
    const on = termsOn(), extra = customFilled();
    setSum('terms', `${on + extra} lines`);
    updateChipsDone();
  }

  const sections = $$('section.card', form);
  const chips = $$('.chip');
  function sectionDone(sec) {
    if (sec.id === 's5') return termsOn() + customFilled() > 0;
    return $$('[required]', sec).every((el) => el.value.trim() !== '');
  }
  function updateChipsDone() {
    sections.forEach((sec, i) => chips[i] && chips[i].classList.toggle('is-done', sectionDone(sec)));
  }
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        const i = sections.indexOf(en.target);
        chips.forEach((c, k) => c.classList.toggle('is-active', k === i));
        const c = chips[i], box = $('#chips');
        if (c && box.scrollWidth > box.clientWidth) box.scrollTo({ left: c.offsetLeft - 16, behavior: 'smooth' });
      });
    }, { rootMargin: '-130px 0px -65% 0px', threshold: 0 });
    sections.forEach((s) => io.observe(s));
  }
  form.addEventListener('input', updateSummary);

  // Enter key moves to the next field instead of submitting (nicer on phone keyboards)
  form.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.target.tagName !== 'INPUT' || ['checkbox', 'radio'].includes(e.target.type)) return;
    e.preventDefault();
    const els = $$('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([readonly]), textarea', form).filter((el) => !el.disabled);
    const next = els[els.indexOf(e.target) + 1];
    next ? next.focus() : e.target.blur();
  });
  form.addEventListener('submit', (e) => { e.preventDefault(); openReview(); });

  // ---------- validation before review ----------
  function validate() {
    clearErrors();
    const errs = {};
    $$('[required]', form).forEach((el) => {
      if (!el.name || el.readOnly || el.type === 'radio') return;
      if (!el.value.trim()) errs[el.name] = 'This field is required.';
    });
    const m = mode();
    if (m === 'total_to_rate' && !val('total_cost')) errs.total_cost = 'Enter the total project cost.';
    if (m === 'rate_to_total' && !val('rate')) errs.rate = 'Enter the rate per kW.';
    if (m !== 'manual' && !errs.total_cost && !errs.rate && (!val('rate') || !val('total_cost'))) errs[m === 'total_to_rate' ? 'total_cost' : 'rate'] = 'Could not calculate. Check the capacity and amount.';
    if (m === 'manual') ['rate', 'amount', 'gst_amount', 'total_cost'].forEach((n) => { if (!val(n)) errs[n] = 'This field is required.'; });
    if (val('contact_mobile') && !/^\d{10}$/.test(val('contact_mobile'))) errs.contact_mobile = 'Enter a 10 digit mobile number.';
    if (val('module_qty') && !(parseInt(val('module_qty'), 10) > 0)) errs.module_qty = 'Must be 1 or more.';
    if (val('module_wp') && !(parseInt(val('module_wp'), 10) > 0)) errs.module_wp = 'Must be 1 or more.';
    if (termsOn() + customFilled() === 0) errs.terms = 'Select at least one Terms & Conditions item or add an extra condition.';
    if (Object.keys(errs).length) { showFieldErrors(errs); return false; }
    return true;
  }

  // ---------- review sheet ----------
  const sheet = $('#review');
  const rvBody = $('#review-body');
  const rvState = $('#rv-state'), rvText = $('#rv-state-text');
  const btnDownload = $('#btn-download'), btnShareFile = $('#btn-share-file');
  let lastFocus = null;
  const gen = { key: null, file: null, token: 0, busy: false, ctl: null };

  const formKey = () => JSON.stringify([...new FormData(form)].filter(([k]) => k !== 'csrf_token'));

  function tickedTerms() {
    const list = [];
    termItems().forEach((item) => {
      if (!$('input[name="terms"]', item).checked) return;
      list.push({ text: $('.term-text', item).textContent.trim(), hl: $('input[name="highlight"]', item).checked });
    });
    customRows().forEach((row) => {
      const t = customInput(row).value.trim();
      if (t) list.push({ text: t, hl: $('input[type="checkbox"]', row).checked });
    });
    return list;
  }

  function summaryText(rich) {
    const b = (t) => (rich ? `*${t}*` : t);
    const kw = val('capacity_kw');
    const lines = [
      b('MBTION Engineering – Solar Quotation'),
      `Customer: ${val('customer_name')}`,
      `Project: ${val('project_type')} Rooftop Solar EPC${kw ? ` – ${kw} kW` : ''}`,
      `Total cost (incl. GST): ${money(f('total_cost').value)}`,
      `Rate: ${money(f('rate').value)} per kW + GST @ ${cfg.gst}% (${money(f('gst_amount').value)})`,
      '',
      `Contact: ${val('contact_name')} +91 ${val('contact_mobile')}`,
    ];
    return lines.join('\n');
  }

  function renderReview() {
    const kw = val('capacity_kw');
    const specs = [
      ['PV modules', `${val('module_make')} – ${val('module_qty')} × ${val('module_wp')}Wp`],
      ['Inverter', `${val('inverter_make')} – ${val('inverter_qty')} nos × ${val('inverter_kw')} kW`],
      ['ACDB / DCDB', `${val('acdb_make')} / ${val('dcdb_make')}`],
      ['Cables', `${val('cable_make')} – AC ${val('cable_ac_mm')} mm / DC ${val('cable_dc_mm')} mm (Cu)`],
      ['Earthing cable', `${val('earth_make')} – ${val('earth_mm')} mm`],
      ['LA cable', `${val('la_make')} – ${val('la_mm')} mm`],
      ['GI structure', `${val('structure_make')} – GI pipe ${val('gi_pipe')}, ${val('thickness')} mm thick, ${val('structure_height')} ft`],
      ['PVC conduit', val('conduit_make')],
    ];
    const rows = (arr) => arr.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('');
    const terms = tickedTerms();
    const logo = $('.brand img').src;
    rvBody.innerHTML = `
      <article class="paper">
        <div class="paper-top"><img src="${esc(logo)}" alt=""><div><div class="t1">MB<b>T</b>ION</div><div class="t2">Engineering · Solar EPC</div></div></div>
        <div class="paper-title">
          <small>Quotation</small>
          <h3>${esc(val('project_type'))} Rooftop Solar EPC Project${kw ? ` – ${esc(kw)} kW` : ''}</h3>
          <p><strong>${esc(val('customer_name'))}</strong><br>${esc(val('customer_address')).replace(/\n/g, '<br>')}</p>
        </div>
        <div class="rv-sec"><h4><span>03</span>Specifications</h4><div class="rv-wrap"><table class="rv-table"><tbody>${rows(specs)}</tbody></table></div></div>
        <div class="rv-sec"><h4><span>04</span>Rates &amp; total cost</h4><div class="rv-wrap"><table class="rv-table"><tbody>
          <tr><th>Capacity</th><td class="rv-num">${esc(kw)} kW</td></tr>
          <tr><th>Rate per kW</th><td class="rv-num">${esc(money(f('rate').value))}</td></tr>
          <tr><th>Sub-total</th><td class="rv-num">${esc(money(f('amount').value))}</td></tr>
          <tr><th>GST @ ${esc(cfg.gst)}%</th><td class="rv-num">${esc(money(f('gst_amount').value))}</td></tr>
          <tr class="rv-total"><th>Total cost</th><td class="rv-num">${esc(money(f('total_cost').value))}</td></tr>
        </tbody></table></div></div>
        <div class="rv-sec"><h4><span>05</span>Terms &amp; conditions (${terms.length})</h4>
          <ol class="rv-terms">${terms.map((t, i) => `<li class="${t.hl ? 'hl' : ''}"><b>${i + 1}</b><span>${esc(t.text)}</span></li>`).join('')}</ol></div>
        <div class="rv-sec"><h4><span>06</span>Warranty</h4><div class="rv-wrap"><table class="rv-table"><tbody>${rows([['Solar panel', `${val('warranty_panel')} – ${val('warranty_panel_years')} Years`], ['Inverter', `${val('warranty_inverter')} – ${val('warranty_inverter_years')} Years`], ['All service', `${val('warranty_service_years')} Years`]])}</tbody></table></div></div>
        <div class="paper-foot"><span>${SVG.phone}${esc(val('contact_name'))}</span><span>+91 ${esc(val('contact_mobile'))}</span></div>
      </article>
      <p class="rv-note">Quick preview of your entries. The PDF keeps your original MBTION layout, fonts and tables.</p>`;
    rvBody.scrollTop = 0;
  }

  function setState(kind, text) {
    rvState.className = 'rv-state ' + (kind || 'is-loading');
    rvText.textContent = text;
  }
  const canShareFile = (file) => !!(navigator.canShare && file && navigator.canShare({ files: [file] }));

  const GEN_TIMEOUT_MS = 100000;
  async function prepareFile() {
    const key = formKey();
    if (gen.file && gen.key === key) { setReady(); return gen.file; }
    if (navigator.onLine === false) {
      setState('fail', 'No internet connection. Connect and tap Download to try again.');
      btnDownload.disabled = false;
      return null;
    }
    const mine = ++gen.token;
    gen.file = null; gen.busy = true;
    btnDownload.disabled = true; btnShareFile.disabled = true;
    setState('', 'Preparing PDF…');
    const ctl = new AbortController();
    gen.ctl = ctl;
    const killTimer = setTimeout(() => ctl.abort(), GEN_TIMEOUT_MS);
    const slowTimer = setTimeout(() => { if (mine === gen.token && gen.busy) setState('', 'Still working… the server may be waking up, please wait.'); }, 12000);
    try {
      const r = await fetch(form.action, { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: new FormData(form), signal: ctl.signal, credentials: 'same-origin' });
      if (mine !== gen.token) return null;
      if (r.status === 401) { sessionExpired(); return null; }
      const ctype = r.headers.get('Content-Type') || '';
      if (!r.ok) {
        const d = ctype.includes('json') ? await r.json().catch(() => ({})) : {};
        if (d.errors) { closeReview(); showFieldErrors(d.errors); return null; }
        throw new Error(friendly(r.status, d.error));
      }
      if (!ctype.includes('pdf')) throw new Error('The server sent an unexpected reply. Please try again.');
      const m = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '');
      const name = m ? m[1] : 'Quotation.pdf';
      const blob = await r.blob();
      if (mine !== gen.token) return null;
      if (!blob.size) throw new Error('The PDF came back empty. Please try again.');
      gen.file = new File([blob], name, { type: PDF_MIME });
      gen.key = key;
      setReady();
      return gen.file;
    } catch (err) {
      if (mine !== gen.token) return null;
      let msg;
      if (err && err.name === 'AbortError') msg = 'This is taking too long. Check your connection and tap Download to try again.';
      else if (err instanceof TypeError) msg = 'Could not reach the server. Check your internet and tap Download to try again.';
      else msg = (err && err.message) || 'Could not prepare the PDF. Tap Download to try again.';
      setState('fail', msg);
      btnDownload.disabled = false;
      return null;
    } finally {
      clearTimeout(killTimer); clearTimeout(slowTimer);
      if (mine === gen.token) gen.busy = false;
    }
  }
  function setReady() {
    setState('ready', `Ready: ${gen.file.name}`);
    btnDownload.disabled = false;
    const share = canShareFile(gen.file);
    btnShareFile.hidden = !share;
    btnShareFile.disabled = !share;
    $('#share-tip').hidden = share;
  }

  function saveFile(file) {
    try {
      const url = URL.createObjectURL(file);
      const a = Object.assign(document.createElement('a'), { href: url, download: file.name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      toast('Quotation downloaded');
    } catch (_) {
      toast('Download could not start. Open this page in Chrome / Safari and try again.', 'err');
    }
  }

  btnDownload.addEventListener('click', async () => {
    if (gen.busy) return;
    const file = gen.file && gen.key === formKey() ? gen.file : await prepareFile();
    if (file) saveFile(file);
  });
  btnShareFile.addEventListener('click', async () => {
    const file = gen.file;
    if (!file) return;
    try {
      await navigator.share({ files: [file], title: `Quotation – ${val('customer_name')}`, text: summaryText(false) });
    } catch (err) {
      if (err && err.name === 'AbortError') return;       // user closed the share sheet
      toast('Sharing not available. Downloading instead.', 'err');
      saveFile(file);
    }
  });
  $('#btn-wa').addEventListener('click', () => window.open('https://wa.me/?text=' + encodeURIComponent(summaryText(true)), '_blank', 'noopener'));
  $('#btn-mail').addEventListener('click', () => {
    location.href = 'mailto:?subject=' + encodeURIComponent(`Quotation – ${val('customer_name')}`) + '&body=' + encodeURIComponent(summaryText(false));
  });
  $('#btn-copy').addEventListener('click', async () => {
    const text = summaryText(false);
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const ta = Object.assign(document.createElement('textarea'), { value: text });
      document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch (__) { /* ignore */ }
      ta.remove();
    }
    toast('Summary copied');
  });

  function openReview(trigger) {
    if (!validate()) return;
    lastFocus = trigger || document.activeElement;
    renderReview();
    sheet.hidden = false;
    document.body.classList.add('no-scroll');
    $('#btn-edit').focus({ preventScroll: true });
    prepareFile();
  }
  function closeReview() {
    if (sheet.hidden) return;
    gen.token++;                       // ignore any in-flight request
    gen.busy = false;
    if (gen.ctl) { try { gen.ctl.abort(); } catch (_) { /* already finished */ } }
    sheet.hidden = true;
    document.body.classList.remove('no-scroll');
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeReview();
    if (e.key === 'Tab' && !sheet.hidden) {                       // keep focus inside the dialog
      const items = $$('button:not([disabled]):not([hidden]), a[href]', sheet).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // ---------- reset ----------
  function resetForm() {
    if (!confirm('Clear all fields and start a new quotation?')) return;
    form.reset();
    setTimeout(() => { touched.clear(); clearErrors(); gen.file = null; applyMode(); syncAuto(); updateTerms(); window.scrollTo({ top: 0, behavior: 'smooth' }); }, 0);
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === 'review') openReview(btn);
    else if (act === 'close-review') closeReview();
    else if (act === 'reset') resetForm();
  });

  restoreDraft();
  applyMode();
  syncAuto();
  updateTerms();
})();
