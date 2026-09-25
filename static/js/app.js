document.addEventListener('DOMContentLoaded', () => {
  const query = document.getElementById('directory-query');
  if (query && query.value) query.setSelectionRange(query.value.length, query.value.length);
  // Destructive forms carry their own question in data-confirm.
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!confirm(form.dataset.confirm)) event.preventDefault();
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

// Copy buttons (data-copy): copy the value and confirm on the button itself.
document.addEventListener('click', event => {
  const button = event.target.closest('[data-copy]');
  if (!button || !navigator.clipboard) return;
  event.preventDefault();
  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    button.classList.add('copied');
    button.setAttribute('aria-label', 'Copied');
    setTimeout(() => button.classList.remove('copied'), 1400);
  });
});

// Recently viewed agents: kept in this browser only, per signed-in user, and cleared on sign-out.
// Opening a link still goes through the server's access check.
(() => {
  const user = document.body.dataset.user;
  if (!user) return;
  const key = 'recent-agents:' + user;
  const read = () => { try { return JSON.parse(localStorage.getItem(key)) || []; } catch (e) { return []; } };
  const write = list => { try { localStorage.setItem(key, JSON.stringify(list)); } catch (e) { /* storage unavailable */ } };
  const current = document.getElementById('recent-item');
  if (current) {
    try {
      const item = JSON.parse(current.textContent);
      write([item, ...read().filter(x => x.id !== item.id)].slice(0, 8));
    } catch (e) { /* malformed item: skip */ }
  }
  const list = document.getElementById('recent-list');
  if (list) {
    const items = read();
    items.forEach(item => {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = '/company/' + encodeURIComponent(item.id);
      const name = document.createElement('span'); name.className = 'recent-name'; name.textContent = item.name;
      const meta = document.createElement('span'); meta.className = 'recent-meta';
      meta.textContent = [item.agent, item.place, item.country].filter(Boolean).join(' · ');
      a.append(name, meta); li.append(a); list.append(li);
    });
    list.hidden = items.length === 0;
    document.getElementById('recent-empty').hidden = items.length > 0;
  }
  document.querySelectorAll('form[data-signout]').forEach(form => form.addEventListener('submit', () => {
    try { localStorage.removeItem(key); } catch (e) { /* storage unavailable */ }
  }));
})();
