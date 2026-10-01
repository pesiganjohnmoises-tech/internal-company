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

// Company page: sheet tabs, filter, hide-empty rows, contact order and copy-as-TSV.
document.addEventListener('DOMContentLoaded', () => {
  const book = document.getElementById('workbook');
  if (!book) return;
  const tabs = [...book.querySelectorAll('.sheet-tab')];
  const panels = [...book.querySelectorAll('.sheet-panel')];
  const filter = document.getElementById('sheet-filter');
  const hideEmpty = document.getElementById('hide-empty');
  const status = document.getElementById('sheet-status');
  const copy = document.getElementById('sheet-copy');
  const sortWrap = document.getElementById('contact-sort-wrap');
  // A cell's visible value: screen-reader text, the empty-value dash and buttons are left out.
  const cellText = cell => {
    const main = cell.cloneNode(true);
    main.querySelectorAll('.sr-only, .value-empty, button, summary').forEach(x => x.remove());
    return main.textContent.replace(/\s+/g, ' ').trim();
  };
  book.classList.add('is-tabbed');
  // Grid header rows pin just below whatever stays pinned above them: the top bar, and the sheet tabs
  // where they are sticky (not on phones).
  const tabStrip = book.querySelector('.sheet-tabs');
  const setTop = () => {
    const bar = document.querySelector('.topbar');
    let top = bar && getComputedStyle(bar).position === 'sticky' ? bar.offsetHeight : 0;
    if (getComputedStyle(tabStrip).position === 'sticky') top += tabStrip.offsetHeight;
    book.style.setProperty('--xl-top', top + 'px');
  };
  setTop();
  window.addEventListener('resize', setTop);
  let active = [];

  // A row stays when it matches; a group heading stays while any of its rows does.
  // Field sheets have one field per row, the contacts sheet one contact per row.
  const filterRows = (table, terms, noun) => {
    let shown = 0;
    const rows = [...table.querySelectorAll('tr[data-row]')];
    rows.forEach(row => {
      const keep = terms.every(t => row.textContent.toLowerCase().includes(t)) && !(hideEmpty.checked && row.classList.contains('is-empty'));
      row.hidden = !keep;
      if (keep) shown++;
    });
    table.querySelectorAll('tr[data-group]').forEach(g => {
      g.hidden = !table.querySelector(`tr[data-in="${g.dataset.group}"]:not([hidden])`);
    });
    return [shown, rows.length, noun];
  };

  const refresh = () => {
    const terms = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
    // Stacked contact rows (phones) also drop their empty cells.
    book.classList.toggle('hide-empty', hideEmpty.checked);
    const parts = [];
    active.forEach(panel => {
      let any = false, tables = 0;
      panel.querySelectorAll('table.xl-grid').forEach(table => {
        const r = filterRows(table, terms, table.classList.contains('xl-contacts') ? 'contact' : 'field');
        table.closest('.xl-scroll').hidden = r[0] === 0 && terms.length > 0;
        if (r[0]) any = true;
        tables++;
        parts.push(r);
      });
      const empty = panel.querySelector('.empty-filter');
      if (empty) empty.hidden = any || !tables;
    });
    const busy = terms.length || hideEmpty.checked;
    status.textContent = busy ? parts.map(([s, t, k]) => `${s} of ${t} ${k}${t === 1 ? '' : 's'}`).join(' · ') : '';
    copy.disabled = !parts.some(p => p[0]);
  };

  const select = (tab, focus) => {
    tabs.forEach(t => {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
    });
    active = panels.filter(p => p.id === tab.getAttribute('aria-controls'));
    panels.forEach(p => { p.hidden = !active.includes(p); });
    if (sortWrap) sortWrap.hidden = !active.some(p => p.querySelector('.xl-contacts'));
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

  // Contact order: as in the workbook, by name, or by contact type (empty types last).
  const sort = document.getElementById('contact-sort');
  if (sort) sort.addEventListener('change', () => {
    const body = document.getElementById('contact-cards').tBodies[0];
    const key = sort.value;
    const by = row => key === 'index' ? '' : (row.dataset[key] || '');
    [...body.querySelectorAll('tr[data-row]')].sort((a, b) => {
      const x = by(a), y = by(b);
      if (x !== y && (!x || !y)) return (!x) - (!y);
      return x.localeCompare(y, undefined, { numeric: true, sensitivity: 'base' }) || a.dataset.index - b.dataset.index;
    }).forEach(row => body.append(row));
  });

  // Copy what is shown as tab-separated text, which pastes into Excel as cells; a blank line separates tables.
  copy.addEventListener('click', () => {
    if (!navigator.clipboard) return;
    const blocks = [];
    active.forEach(panel => panel.querySelectorAll('[data-copy-block]').forEach(table => {
      if (table.closest('[hidden]')) return;
      const lines = [...table.tBodies[0].rows].filter(r => !r.hidden && !r.classList.contains('xl-group'))
        .map(r => [...r.querySelectorAll(':scope > [data-cell]')].filter(c => !c.hidden).map(cellText).join('\t'));
      if (lines.length > 1) blocks.push(lines.join('\n'));
    }));
    navigator.clipboard.writeText(blocks.join('\n\n')).then(() => {
      const label = copy.querySelector('span');
      label.textContent = 'Copied';
      copy.classList.add('copied');
      setTimeout(() => { label.textContent = 'Copy'; copy.classList.remove('copied'); }, 1400);
    });
  });

  // Open the sheet named in the URL (#sec-contact), or the one holding the element it names
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
