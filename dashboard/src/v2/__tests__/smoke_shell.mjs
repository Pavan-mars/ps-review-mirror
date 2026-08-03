// Render PS3Overview for real, in jsdom, against the LIVE responses captured
// from the deployed API on 03-Aug-2026. Parsing proves syntax; this proves the
// component runs -- wrong field names, undefined access and bad chart props all
// surface here and nowhere earlier.
//
// Every one of the five sub-tabs is clicked, because a panel that is never
// mounted is a panel that was never tested.
import fs from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://localhost/', pretendToBeVisual: true,
});
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, 'navigator', { value: dom.window.navigator, configurable: true });
global.HTMLElement = dom.window.HTMLElement;
global.Element = dom.window.Element;
global.Node = dom.window.Node;
global.getComputedStyle = dom.window.getComputedStyle;
global.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
global.cancelAnimationFrame = (id) => clearTimeout(id);
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
dom.window.ResizeObserver = global.ResizeObserver;
global.Blob = dom.window.Blob;
global.URL.createObjectURL = () => 'blob:x';

const fixtures = JSON.parse(fs.readFileSync('./fixtures/shell.json', 'utf8'));
const served = new Set();
let unmatched = [];
global.fetch = async (url) => {
  const path = new URL(url, 'http://x').pathname;
  if (path in fixtures) { served.add(path); return { ok: true, json: async () => fixtures[path] }; }
  unmatched.push(path);
  return { ok: false, status: 404, json: async () => ({ error: 'not in fixtures' }) };
};

const errors = [];
const origError = console.error;
console.error = (...a) => { errors.push(a.map(String).join(' ')); };

const React = (await import('react')).default;
const { createRoot } = await import('react-dom/client');
const Shell = (await import('./build/V2Shell.js')).default;

const root = createRoot(document.getElementById('root'));
await new Promise((res) => { root.render(React.createElement(Shell, { city: 'CHI' })); setTimeout(res, 2500); });

// Estate overview first, then into PS3, then every sub-tab. The point is that
// the PS3 tab is reachable from the shell at all -- it was a WIP placeholder.
const estate = document.getElementById('root').textContent || '';
console.log('estate overview chars:', estate.length);
console.log(`${estate.includes('Which component an OOS episode is attributed to') ? 'present ' : 'MISSING '} PS3 card copy`);
// "Notebook being revised" is still correct copy -- for PS5. Probe the PS3 row
// specifically rather than the phrase, or PS5's honest WIP label reads as a
// PS3 regression.
console.log(`${estate.includes('20 tables from the V26 source-first run') ? 'present ' : 'MISSING '} PS3 listed as live in the wiring panel`);
console.log(`${estate.includes('Notebook under revision. The v2 screen is built once') ? 'LEAKED   PS3 card still shows the WIP note' : 'clean    PS3 card no longer WIP'}`);
console.log(`${estate.includes('Notebook being revised') ? 'note     PS5 still WIP, as expected' : 'note     no WIP rows at all'}`);
const ps3Tab = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'PS3 Root cause');
console.log(`${ps3Tab ? 'present ' : 'MISSING '} PS3 tab chip (no asterisk = not WIP)`);
if (ps3Tab) { ps3Tab.click(); await new Promise((r) => setTimeout(r, 2200)); }
const TABS = ['What breaks', 'Devices', 'Where it happens', 'What repeats', 'How we know'];
const seen = {};
const tabText = {};
for (const label of TABS) {
  const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label);
  if (!btn) { seen[label] = 'TAB BUTTON MISSING'; continue; }
  btn.click();
  await new Promise((r) => setTimeout(r, 1400));
  const t = document.getElementById('root').textContent || '';
  seen[label] = { chars: t.length, svg: (document.getElementById('root').innerHTML.match(/<svg/g) || []).length };
  tabText[label] = t;
}

const html = document.getElementById('root').innerHTML;
const text = document.getElementById('root').textContent || '';
console.log('--- PS3 render smoke ---');
for (const [k, v] of Object.entries(seen)) console.log(`  tab ${k.padEnd(18)}`, JSON.stringify(v));
console.log('routes served  :', served.size, '/', Object.keys(fixtures).length);
console.log('unmatched      :', [...new Set(unmatched)].join(', ') || '(none)');
const real = errors.filter((e) => !/not wrapped in act|useLayoutEffect does nothing|MODULE_TYPELESS_PACKAGE_JSON|Reparsing as ES module/i.test(e));
console.log('react errors   :', real.length);
real.slice(0, 8).forEach((e) => console.log('   !', e.slice(0, 260)));

// Content probes, checked against the tab that owns them. Checking the final
// DOM only would report every earlier tab's copy as missing, because the shell
// unmounts a view when you leave it.
const must = {
  'What breaks': [
    'Severity and confirmed root cause are not available',
    'One mode dominates every unfiltered total',
    'zero episodes carry the commanded signal',
    'Which component the episode is attributed to',
    'How soon the same component comes back',
  ],
  'Devices': [
    'percentiles of observed episode counts, not a model score',
    'distinct serial values',
  ],
  'Where it happens': ['Episodes per device is the comparable one'],
  'What repeats': [
    'Components the run itself flagged as weak overlap',
    'Components with adequate common support',
    'Covariate balance',
  ],
  'How we know': [
    'Read the feature mix before the score',
    'Why severity was refused',
    'Where root cause would come from',
  ],
};
const mustNot = ['NaN', 'undefined', '[object Object]', 'Infinity'];
console.log('--- content ---');
let bad = 0;
for (const [tab, probes] of Object.entries(must)) {
  for (const p of probes) {
    const ok = (tabText[tab] || '').includes(p);
    if (!ok) bad++;
    console.log(`${ok ? 'present ' : 'MISSING '} [${tab}] ${p}`);
  }
}
for (const [tab, t] of Object.entries(tabText)) {
  for (const p of mustNot) {
    if (t.includes(p)) { bad++; console.log(`LEAKED   [${tab}] ${p}`); }
  }
}
if (!bad) console.log('clean    no NaN / undefined / [object Object] / Infinity on any tab');
const status = tabText['What breaks'] || '';
console.log(`${status.includes('Replay run, not a live score') ? 'present ' : 'MISSING '} [status bar] Replay run, not a live score`);

// The numbers on the screen must be the numbers in the data. Text probes prove
// the copy rendered; these prove the arithmetic behind it.
console.log('--- rendered figures ---');
const wb = tabText['What breaks'] || '';
const checks = [
  ['total episodes', '54,239'],
  ['GATE episodes', '38,448'],
  ['TVM episodes', '5,665'],
  ['VALIDATOR episodes', '10,126'],
  ['GATE share of all', '71%'],
  ['DAP episodes', '23,412'],
  ['DAP share of gate', '61%'],
  ['DAP share of estate', '43%'],
  ['attributed coverage', '100%'],
];
for (const [label, want] of checks) {
  const ok = wb.includes(want);
  if (!ok) bad++;
  console.log(`${ok ? 'ok      ' : 'WRONG   '} ${label.padEnd(22)} expected "${want}"`);
}
const hw = tabText['How we know'] || '';
for (const [label, want] of [['GATE clock share', '49%'], ['GATE macro F1', '0.583'], ['majority baseline', '0.185']]) {
  const ok = hw.includes(want);
  if (!ok) bad++;
  console.log(`${ok ? 'ok      ' : 'WRONG   '} ${label.padEnd(22)} expected "${want}"`);
}
process.exit(real.length || bad ? 1 : 0);
