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
// GIVE THE CHARTS A REAL SIZE.
//
// The first version of this harness left ResizeObserver a no-op and
// getBoundingClientRect returning zeros, so recharts' ResponsiveContainer
// measured 0x0 and drew nothing -- and a chart with its axes wired backwards
// looked exactly like a chart that was merely unmeasured. It passed a screen
// whose bars did not render at all.
//
// Reporting a fixed 820x300 makes the marks real, so "did any bar draw" becomes
// an assertion instead of something only a human eye catches.
const BOX = { width: 820, height: 300, top: 0, left: 0, right: 820, bottom: 300, x: 0, y: 0 };
dom.window.HTMLElement.prototype.getBoundingClientRect = function () { return { ...BOX, toJSON: () => BOX }; };
global.ResizeObserver = class {
  constructor(cb) { this.cb = cb; }
  observe(el) { this.cb([{ target: el, contentRect: BOX }], this); }
  unobserve() {} disconnect() {}
};
dom.window.ResizeObserver = global.ResizeObserver;
Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 820 });
Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 300 });
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

// The shell opens on the estate overview. Assert the ordering of BOTH lists --
// the tab strip and the estate cards are separate arrays that have drifted
// apart before -- then click into PS3 and run the PS3 probes unchanged.
const estate = document.getElementById('root').textContent || '';
console.log('estate overview chars:', estate.length);
console.log(`${estate.includes('Which component an OOS episode is attributed to') ? 'present ' : 'MISSING '} PS3 card copy`);
console.log(`${estate.includes('20 tables from the V26 source-first run') ? 'present ' : 'MISSING '} PS3 listed as live in the wiring panel`);
console.log(`${estate.includes('Notebook under revision. The v2 screen is built once') ? 'LEAKED   PS3 card still shows the WIP note' : 'clean    PS3 card no longer WIP'}`);
console.log(`${estate.includes('Notebook being revised') ? 'note     PS5 still WIP, as expected' : 'note     no WIP rows at all'}`);
const chips = [...document.querySelectorAll('button')].map((b) => b.textContent.trim());
const order = ['PS1 Failure', 'PS2 Cascading', 'PS3 Root cause', 'PS4 Anomaly'].map((l) => chips.indexOf(l));
const sequential = order.every((v, i) => v >= 0 && (i === 0 || v > order[i - 1]));
console.log(`${sequential ? 'ok      ' : 'WRONG   '} tab order PS1 < PS2 < PS3 < PS4  (indices ${order.join(', ')})`);
const cardOrder = ['Failure prediction', 'Cascading failure', 'Root cause and severity', 'Anomaly detection'].map((l) => estate.indexOf(l));
const cardsOk = cardOrder.every((v, i) => v >= 0 && (i === 0 || v > cardOrder[i - 1]));
console.log(`${cardsOk ? 'ok      ' : 'WRONG   '} estate card order matches`);
const ps3Tab = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'PS3 Root cause');
console.log(`${ps3Tab ? 'present ' : 'MISSING '} PS3 tab chip (no asterisk = not WIP)`);
if (ps3Tab) { ps3Tab.click(); await new Promise((r) => setTimeout(r, 2200)); }
const TABS = ['What breaks', 'Root cause & severity', 'Devices', 'Where it happens', 'What repeats', 'How we know'];
const seen = {};
const tabText = {};
for (const label of TABS) {
  const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label);
  if (!btn) { seen[label] = 'TAB BUTTON MISSING'; continue; }
  btn.click();
  await new Promise((r) => setTimeout(r, 1400));
  const t = document.getElementById('root').textContent || '';
  const h = document.getElementById('root').innerHTML;
  seen[label] = {
    chars: t.length,
    svg: (h.match(/<svg/g) || []).length,
    // Counted SEPARATELY. A combined count is not enough: with the axes wired
    // backwards the donut still drew its 8 sectors and only the 8 bars were
    // missing, so the total merely halved and a floor of "at least one mark"
    // passed a broken chart. Bars and sectors are asserted independently.
    bars: (h.match(/recharts-rectangle/g) || []).length,
    sectors: (h.match(/recharts-sector/g) || []).length,
  };
  tabText[label] = t;
}

const html = document.getElementById('root').innerHTML;
const text = document.getElementById('root').textContent || '';
console.log('--- V2 shell render smoke ---');
for (const [k, v] of Object.entries(seen)) console.log(`  tab ${k.padEnd(18)}`, JSON.stringify(v));
// Tabs that carry charts must have drawn marks. This is the assertion the
// first version of this file was missing.
// Minimum bars and sectors each tab must draw, from the real data:
//   What breaks      donut (8 components) + ranked bars (8)
//   Devices          stacked columns, 4 bands x 3 fleets
//   Where it happens two ranked bar charts, 12 rows each
const CHART_TABS = {
  'What breaks':      { bars: 6, sectors: 6 },
  // the root-cause ladder is deliberately NOT a chart: four of its five stages
  // are zero, and a funnel that collapses to nothing reads as a rendering
  // failure rather than as the finding it is

  'Devices':          { bars: 6, sectors: 0 },
  'Where it happens': { bars: 12, sectors: 0 },
};
let markFail = 0;
for (const [tab, need] of Object.entries(CHART_TABS)) {
  const g = seen[tab] || {};
  const okBars = (g.bars || 0) >= need.bars;
  const okSect = (g.sectors || 0) >= need.sectors;
  if (!okBars || !okSect) {
    markFail++;
    console.log(`  MARKS MISSING  ${tab}: bars ${g.bars || 0}/${need.bars}, sectors ${g.sectors || 0}/${need.sectors}`);
  } else {
    console.log(`  marks drawn    ${tab}: bars ${g.bars}, sectors ${g.sectors}`);
  }
}
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
    // the donut legend -- identity must not be colour-alone
    'DAP', 'DEV',
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
  'Root cause & severity': [
    'What PS3 means by a root cause',
    'The chain stops at stage 1',
    '1. Observed component',
    '4. Confirmed root cause',
    '5. Dashboard domain',
    'Component and subsystem',
    'Why no severity is shown',
    'Where root cause would come from',
    'DOPP',            // the subsystem layer that was invisible before
    'GATE_MECH',
  ],
  'How we know': [
    'Read the feature mix before the score',
    'live on the',     // the pointer to where the two moved sections went
  ],
};
const mustNot = ['NaN', 'undefined', '[object Object]', 'Infinity',
  // the exact raw ratios that reached the axis when RankBars was wired
  // backwards -- a category axis should never carry a 15-digit float
  '8.108108108108109', '46.666666666666664', '6.36111111111111'];
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
// ---- the device lookup and the Analyse modal --------------------------
// Building a control is not the same as it working. Type a device id that is
// really in the run, assert its PS3 facts appear, then press Analyse and
// assert the modal mounts.
{
  const devTab = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Devices');
  if (devTab) { devTab.click(); await new Promise((r) => setTimeout(r, 1400)); }
  const input = document.querySelector('input');
  console.log('--- device lookup ---');
  console.log(`${input ? 'present ' : 'MISSING '} lookup input`);
  if (input) {
    const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, 'RVG01601');
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 700));
    const t = document.getElementById('root').textContent || '';
    for (const probe of ['found in this run', 'Fare Gates', 'OOS episodes', 'SET events', 'Median gap']) {
      const ok = t.includes(probe);
      if (!ok) bad++;
      console.log(`${ok ? 'present ' : 'MISSING '} lookup shows ${probe}`);
    }
    // 89 episodes / 341 SET events is what the live run holds for RVG01601
    for (const [label, want] of [['episode count', '89'], ['SET events', '341']]) {
      const ok = t.includes(want);
      if (!ok) bad++;
      console.log(`${ok ? 'ok      ' : 'WRONG   '} lookup ${label} = ${want}`);
    }
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Analyse this device');
    console.log(`${btn ? 'present ' : 'MISSING '} Analyse button`);
    if (btn) {
      btn.click();
      await new Promise((r) => setTimeout(r, 1600));
      const m = document.getElementById('root').textContent || '';
      const opened = m.includes('RVG01601') && m.length > t.length;
      if (!opened) bad++;
      console.log(`${opened ? 'present ' : 'MISSING '} Analyse modal mounted for the device`);
    }
  } else { bad++; }
}

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
process.exit(real.length || bad || markFail ? 1 : 0);
