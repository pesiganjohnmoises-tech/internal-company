// Run with Node: no app startup, browser dependencies, or real browser storage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/theme.js'), 'utf8');

function page(saved, dark = false, blocked = false) {
  const documentEvents = {}, windowEvents = {}, selectEvents = {};
  const select = {value: '', addEventListener: (type, fn) => { selectEvents[type] = fn; }};
  const control = {hidden: true}, root = {dataset: {}, style: {}};
  let mediaChange, stored = saved, scheme;
  const media = {matches: dark, addEventListener: (type, fn) => { mediaChange = fn; }};
  vm.runInNewContext(source, {
    localStorage: {
      getItem: () => { if (blocked) throw Error('Blocked'); return stored; },
      setItem: (key, value) => { if (blocked) throw Error('Blocked'); stored = value; }
    },
    window: {matchMedia: () => media, addEventListener: (type, fn) => { windowEvents[type] = fn; }},
    document: {
      documentElement: root,
      querySelector: () => ({setAttribute: (name, value) => { scheme = value; }}),
      querySelectorAll: selector => selector === '[data-theme-control]' ? [control] : [select],
      addEventListener: (type, fn) => { documentEvents[type] = fn; }
    }
  });
  return {
    root, select, control,
    ready: () => documentEvents.DOMContentLoaded(),
    choose: value => { select.value = value; selectEvents.change(); },
    system: value => { media.matches = value; mediaChange(); },
    storage: value => windowEvents.storage({key: 'directory-theme', newValue: value}),
    stored: () => stored, scheme: () => scheme
  };
}

for (const invalid of [null, '', 'invalid']) {
  const p = page(invalid, true); assert.equal(p.root.dataset.theme, 'light');
}
const dark = page('dark');
assert.equal(dark.root.dataset.theme, 'dark'); // Applied before DOMContentLoaded.
assert.equal(dark.scheme(), 'dark'); assert.equal(dark.root.style.colorScheme, 'dark');
dark.ready(); assert.equal(dark.select.value, 'dark'); assert.equal(dark.control.hidden, false);
dark.choose('light'); assert.equal(dark.stored(), 'light'); assert.equal(dark.root.dataset.theme, 'light');
dark.system(true); assert.equal(dark.root.dataset.theme, 'light'); // Explicit choice ignores OS changes.
dark.choose('system'); assert.equal(dark.root.dataset.theme, 'dark');
dark.system(false); assert.equal(dark.root.dataset.theme, 'light'); assert.equal(dark.select.value, 'system');
dark.storage('dark'); assert.equal(dark.root.dataset.theme, 'dark'); assert.equal(dark.select.value, 'dark');
dark.storage(null); assert.equal(dark.root.dataset.theme, 'light');
const blocked = page(null, false, true); blocked.ready(); blocked.choose('dark');
assert.equal(blocked.root.dataset.theme, 'dark');
blocked.choose('system'); blocked.system(true); assert.equal(blocked.root.dataset.theme, 'dark');
assert.equal(page('system', true).root.dataset.theme, 'dark');
console.log('Theme checks passed: initial paint, defaults, choices, OS changes, storage sync and blocked storage.');
