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

// Company page workbook: sheet tabs, row filter, column sort, hide-empty and copy-as-TSV.
document.addEventListener('DOMContentLoaded', () => {
  const book = document.getElementById('workbook');
  if (!book) return;
  const tabs = [...book.querySelectorAll('.sheet-tab')];
  const panels = [...book.querySelectorAll('.sheet-panel')];
  const filter = document.getElementById('sheet-filter');
  const hideEmpty = document.getElementById('hide-empty');
  const hideWrap = document.getElementById('hide-empty-wrap');
  const status = document.getElementById('sheet-status');
  const copy = document.getElementById('sheet-copy');
  // A cell's visible value: screen-reader text and the empty-value dash are left out.
  const cellText = cell => {
    const main = (cell.querySelector('.cell-main') || cell).cloneNode(true);
    main.querySelectorAll('.sr-only, .value-empty, button').forEach(x => x.remove());
    return main.textContent.replace(/\s+/g, ' ').trim();
  };
  book.classList.add('is-tabbed');
  let active = [];

  const refresh = () => {
    const terms = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0, total = 0;
    active.forEach(panel => {
      const rows = [...panel.querySelectorAll('tbody tr')];
      let visible = 0;
      rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        const keep = terms.every(t => text.includes(t)) && !(hideEmpty.checked && row.classList.contains('is-empty'));
        row.hidden = !keep;
        if (keep) visible++;
      });
      const empty = panel.querySelector('.empty-filter');
      if (empty) empty.hidden = visible > 0 || rows.length === 0;
      // In "All fields", a section with no matching rows is left out entirely.
      panel.classList.toggle('no-match', active.length > 1 && visible === 0);
      shown += visible; total += rows.length;
    });
    status.textContent = total ? (shown === total ? `${total} row${total === 1 ? '' : 's'}` : `${shown} of ${total} rows`) : '';
    copy.disabled = shown === 0;
  };

  const select = (tab, focus) => {
    tabs.forEach(t => {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
    });
    const ids = tab.getAttribute('aria-controls').split(/\s+/).filter(Boolean);
    active = panels.filter(p => ids.includes(p.id));
    panels.forEach(p => { p.hidden = !active.includes(p); });
    book.classList.toggle('show-all', active.length > 1);
    hideWrap.hidden = !active.some(p => p.querySelector('.field-row'));
    // Keep the selected tab in view when the tab strip scrolls sideways (phones).
    const strip = tab.parentNode;
    if (tab.offsetLeft < strip.scrollLeft || tab.offsetLeft + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = tab.offsetLeft - 8;
    if (focus) tab.focus();
    refresh();
  };

  tabs.forEach(tab => tab.addEventListener('click', event => {
    event.preventDefault();
    select(tab);
    history.replaceState(null, '', tab.getAttribute('href'));
  }));
  // Arrow keys move between tabs, as in a standard tab list.
  book.querySelector('.sheet-tabs').addEventListener('keydown', event => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const tab = tabs[(next + tabs.length) % tabs.length];
    select(tab, true);
    history.replaceState(null, '', tab.getAttribute('href'));
  });
  filter.addEventListener('input', refresh);
  hideEmpty.addEventListener('change', refresh);

  // Click a column header to sort; click again to reverse. Empty cells always sort last.
  book.querySelectorAll('th[data-sort]').forEach(th => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'sort-btn';
    button.append(...th.childNodes);
    th.append(button);
    button.addEventListener('click', () => {
      const table = th.closest('table');
      const col = [...th.parentNode.children].indexOf(th);
      const dir = th.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
      table.querySelectorAll('th[aria-sort]').forEach(x => x.removeAttribute('aria-sort'));
      th.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
      const body = table.tBodies[0];
      const value = row => { const c = row.children[col]; return cellText(c); };
      [...body.rows].sort((a, b) => {
        const x = value(a), y = value(b);
        if (!x || !y) return (!x) - (!y);
        return dir * x.localeCompare(y, undefined, { numeric: true, sensitivity: 'base' });
      }).forEach(row => body.append(row));
    });
  });

  // Copy the visible rows (with headers) as tab-separated text, which pastes into Excel as cells.
  copy.addEventListener('click', () => {
    if (!navigator.clipboard) return;
    // Every table on the sheet, each with its header row; a blank line separates tables.
    const clean = cells => cells.filter(c => !c.classList.contains('row-num')).map(cellText);
    const blocks = [];
    active.forEach(panel => panel.querySelectorAll('table').forEach(table => {
      const rows = [...table.tBodies[0].rows].filter(r => !r.hidden);
      if (rows.length) blocks.push([table.tHead.rows[0], ...rows].map(r => clean([...r.cells]).join('\t')).join('\n'));
    }));
    navigator.clipboard.writeText(blocks.join('\n\n')).then(() => {
      const label = copy.querySelector('span');
      label.textContent = 'Copied';
      copy.classList.add('copied');
      setTimeout(() => { label.textContent = 'Copy rows'; copy.classList.remove('copied'); }, 1400);
    });
  });

  // Open the sheet named in the URL (#sec-contact), or the sheet holding the element it names
  // (#sec-contacts), else the first one.
  const fromHash = () => {
    if (location.hash.length < 2) return null;
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    return tabs.find(t => t.getAttribute('href') === location.hash)
      || (target && tabs.find(t => document.getElementById(t.getAttribute('aria-controls'))?.contains(target)));
  };
  select(fromHash() || tabs[0]);
  window.addEventListener('hashchange', () => { const t = fromHash(); if (t) select(t); });
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
