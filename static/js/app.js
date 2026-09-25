document.addEventListener('DOMContentLoaded', () => {
  const query = document.getElementById('directory-query');
  if (query && query.value) query.setSelectionRange(query.value.length, query.value.length);
  document.querySelectorAll('form[onsubmit]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!confirm('Are you sure?')) event.preventDefault();
    });
  });
});

document.addEventListener('DOMContentLoaded', () => {
  const filter = document.getElementById('contact-filter');
  if (!filter) return;
  const cards = [...document.querySelectorAll('#contact-cards .contact-card')];
  const empty = document.getElementById('contact-empty');
  filter.addEventListener('input', () => {
    const terms = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    cards.forEach(card => {
      const match = terms.every(t => card.dataset.search.includes(t));
      card.hidden = !match;
      if (match) shown++;
    });
    empty.hidden = shown > 0;
  });
});

// Account menu (header): close on outside click or Escape.
document.addEventListener('DOMContentLoaded', () => {
  const account = document.querySelector('.account');
  if (!account) return;
  document.addEventListener('click', event => { if (!account.contains(event.target)) account.open = false; });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && account.open) { account.open = false; account.querySelector('summary').focus(); }
  });
});

// Slow forms (imports): show a working state on the pressed button. The button is not disabled,
// so its name/value is still submitted.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('form[data-busy]').forEach(form => {
    form.addEventListener('submit', event => {
      const button = event.submitter || form.querySelector('button');
      if (!button) return;
      button.classList.add('is-busy');
      button.setAttribute('aria-busy', 'true');
      button.textContent = form.dataset.busy;
    });
  });
});
