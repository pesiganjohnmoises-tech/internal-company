// Loaded synchronously in <head> so the saved theme is applied before first paint.
(() => {
  const key = 'directory-theme';
  const valid = value => ['light', 'dark', 'system'].includes(value);
  let preference = 'light';
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) preference = saved;
  } catch (error) { /* The selector still works when storage is unavailable. */ }
  const system = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const apply = () => {
    const theme = preference === 'system' ? (system?.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme);
    document.querySelectorAll('[data-theme-select]').forEach(select => { select.value = preference; });
  };
  apply();
  system?.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      preference = valid(event.newValue) ? event.newValue : 'light';
      apply();
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-control]').forEach(control => { control.hidden = false; });
    apply();
    document.querySelectorAll('[data-theme-select]').forEach(select => {
      select.addEventListener('change', () => {
        preference = valid(select.value) ? select.value : 'light';
        try { localStorage.setItem(key, preference); } catch (error) { /* Session-only choice. */ }
        apply();
      });
    });
  });
})();
