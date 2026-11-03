(() => {
  'use strict';
  const pw = document.getElementById('password'), tg = document.getElementById('toggle-pass');
  const form = document.getElementById('login-form'), btn = document.getElementById('login-btn');
  if (tg) {
    tg.addEventListener('click', () => {
      const show = pw.type === 'password';
      pw.type = show ? 'text' : 'password';
      tg.classList.toggle('is-shown', show);
      tg.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      pw.focus();
    });
  }
  if (form) form.addEventListener('submit', () => { btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true'); });
  // back/forward cache: never leave the button stuck in loading state
  window.addEventListener('pageshow', () => btn && btn.classList.remove('is-loading'));
})();
