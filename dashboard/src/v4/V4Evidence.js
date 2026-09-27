// =====================================================================
// V4Evidence -- the WHY, in words, for anything that shows a device.
//
// WHAT THIS REPLACES.
// /ps1/device-360 returns cross_ps.signals pre-formatted as internal codes:
//   "PS1 above threshold"   "PS2 cascade rank #707"
//   "PS3 130 OOS incident(s)"   "Peers: Normal"
// Those are problem-statement labels. A depot supervisor does not know what
// PS3 is, so the chip made them ask someone rather than act. Every line here
// is composed from the SAME structured fields the API used to build those
// strings -- ps1.predicted_label / decision_threshold, ps2.cascade_rank,
// ps3.n_incidents, ps4.alert_count -- so the numbers stay live and no second
// source of truth is created. If a field is missing the raw signal string is
// shown rather than nothing.
//
// IT LIVES IN ONE FILE because the popup and the full Device 360 tab both
// show this evidence. Two copies would drift the first time one is edited.
//
// COMPONENTS vs COMPONENT TYPES. TVMSBC and AV2_SAM are component TYPES -- a
// class of board. BHU and CSC_READER are the actual components inside them,
// and they are what a technician removes. `componentsOf` returns the real
// components, ranked by attributed incidents.
// =====================================================================
// The names these models are given everywhere else on the screen. The reader
// never sees "PS2".
export const METHOD = {
  ps1: 'Failure Prediction',
  ps2: 'Failure Pattern & Cascade Identification',
  ps3: 'Failure Severity and Device Reliability',
  ps4: 'Anomaly & Outlier Analysis',
  ps5: 'Remaining Useful Life & SLA Breach',
};

const num = (v) => (v === null || v === undefined || v === '' ? null : Number(v));
const has = (v) => v !== null && v !== undefined && v !== '';
const n0 = (v) => {
  const x = Number(v);
  return Number.isFinite(x) ? x.toLocaleString('en-US') : String(v);
};
const p0 = (v) => {
  const x = Number(v);
  return Number.isFinite(x) ? `${Math.round(x * 100)}%` : '--';
};

// The actual components attributed to this device, worst first.
export function componentsOf(d) {
  const rc = (d && d.ps3_v2_rootcause_360) || {};
  const r = Array.isArray(rc.rootcause) ? rc.rootcause : [];
  return r
    .filter((x) => x && has(x.component_label))
    .map((x) => ({
      name: String(x.component_label),
      n: Number(x.incident_count || 0),
      serial: x.serial_number,
    }))
    .sort((a, b) => b.n - a.n)
    .slice(0, 3);
}

// tone: 'bad' | 'warn' | 'ok' | 'flat'
// WHAT THE CARD LEADS WITH, in falling order of how specific it is:
//   1. the COMPONENT incidents were attributed to  (BHU, CSC_READER)
//   2. the SUBSYSTEM a method points at            (COMMS, CSC_READER)
//   3. the component TYPE, and only then, labelled as a type (AV2_SAM)
// A component type is a class of board. It is not the thing a technician
// removes, and it is not the thing that carries a remaining life -- that
// belongs to a serial-numbered component.
export function focusOf(d) {
  const o = d || {};
  const xp = o.cross_ps || {};
  const parts = componentsOf(o);
  if (parts.length) {
    return {
      kind: 'component',
      name: parts[0].name,
      sub: `${n0(parts[0].n)} incident${parts[0].n === 1 ? '' : 's'} attributed -- observed, not confirmed`,
    };
  }
  const subsys = xp.ps3_subsystem || xp.ps2_subsystem;
  if (subsys) {
    const who = xp.ps3_subsystem ? METHOD.ps3 : METHOD.ps2;
    return { kind: 'subsystem', name: String(subsys), sub: `${who} points here` };
  }
  const c = Array.isArray((o.ps5 || {}).components) ? o.ps5.components : [];
  const worst = c.length
    ? c.slice().sort((a, b) => Number(a.expected_component_rul_days ?? 1e9) - Number(b.expected_component_rul_days ?? 1e9))[0]
    : null;
  if (worst) {
    return {
      kind: 'type',
      name: String(worst.component_type_name || 'Unnamed').toUpperCase(),
      sub: 'Component type -- no component attributed on this device',
    };
  }
  return null;
}

export function evidenceLines(d) {
  const o = d || {};
  const p1 = o.ps1 || {};
  const p2 = o.ps2 || {};
  const p3 = o.ps3 || {};
  const p4 = o.ps4 || {};
  const p4v3 = o.ps4_v3_360 || {};
  const xp = o.cross_ps || {};
  const parts = componentsOf(o);
  const lead = parts.length ? parts[0] : null;
  const weeks = Array.isArray(p4v3.weeks) ? p4v3.weeks : [];
  const week = weeks.length ? weeks[weeks.length - 1] : null;

  const out = [];

  if (p1.found && p1.predicted_label) {
    const prob = num(p1.failure_probability);
    const thr = num(p1.decision_threshold);
    out.push({
      t: (prob !== null && thr !== null && thr > 0)
        ? `Failure predicted for this device: risk ${p0(prob)} is above the ${p0(thr)} action threshold`
        : 'Failure predicted for this device: risk is above the action threshold',
      tone: 'bad',
    });
  }

  if (p2.in_top_devices && has(p2.cascade_rank)) {
    // THE BAND IS A DISPLAY CONVENTION, NOT A MODEL OUTPUT. The cascade feed
    // returns a rank and no denominator, so the band is read off the rank
    // WITHIN ITS OWN FLEET and is worded "in its fleet" so that nobody quotes
    // it back as a probability.
    const inCat = num(p2.cascade_rank_in_category);
    const band = inCat === null ? null : inCat <= 10 ? 'high' : inCat <= 50 ? 'medium' : 'low';
    out.push({
      t: 'Device has previous failure patterns and may trigger a cascade'
        + ` -- cascade rank #${n0(p2.cascade_rank)}`
        + (inCat !== null ? ` (#${n0(inCat)} in its fleet)` : '')
        + (band ? `, ${band} concern` : '')
        + (p2.chronic ? '. Recurring.' : ''),
      tone: 'warn',
    });
  } else if (p2.in_catalog) {
    out.push({ t: 'Device is present in the cascade catalogue, outside the ranked drivers', tone: 'flat' });
  }

  if (p3.found && p3.n_incidents) {
    const c = Number(p3.n_incidents);
    out.push({
      t: `Root cause and severity assessed on ${n0(c)} previous out-of-service incident${c === 1 ? '' : 's'}`
        + (lead ? `, most attributed to the ${lead.name}` : ''),
      tone: 'warn',
    });
  }

  if (p4.alert_count) {
    const a = Number(p4.alert_count);
    out.push({
      t: `Anomaly detected: ${n0(a)} actionable week${a === 1 ? '' : 's'} in the last 12 where this device behaved unlike its peer group`,
      tone: 'bad',
    });
  } else if (week && week.severity) {
    const norm = /normal|none|low/i.test(String(week.severity));
    out.push({
      t: norm
        ? 'No anomaly or outlier detected -- device sits inside its normal peer cluster'
        : `Peer comparison flags this device as ${String(week.severity).toLowerCase()}`,
      tone: norm ? 'ok' : 'warn',
    });
  }

  // STATE WHAT THE METHOD FOUND, NOT WHAT IT FAILED TO DO.   07-Aug-2026
  // These read "Only one method names a subsystem (COMMS) -- nothing
  // corroborates it". Both halves are negative framing of a positive finding:
  // a method DID identify a subsystem. Name the method and what it points at;
  // corroboration is conveyed by whether one line appears or two, which is
  // information the reader can act on without being told it is a shortfall.
  if (xp.subsystem_verdict === 'single_source') {
    const who = xp.ps2_subsystem ? METHOD.ps2 : METHOD.ps3;
    out.push({ t: `${who} points towards ${xp.ps2_subsystem || xp.ps3_subsystem}`, tone: 'flat' });
  } else if (xp.subsystem_verdict === 'agree') {
    out.push({ t: `${METHOD.ps2} and ${METHOD.ps3} both point towards ${xp.ps2_subsystem}`, tone: 'bad' });
  } else if (xp.subsystem_verdict === 'disagree') {
    out.push({
      t: `${METHOD.ps2} points towards ${xp.ps2_subsystem}; ${METHOD.ps3} points towards ${xp.ps3_subsystem}`,
      tone: 'warn',
    });
  }

  // Derived nothing: fall back to whatever the API composed rather than an
  // empty panel, which reads as a failed load.
  if (!out.length) (xp.signals || []).forEach((sg) => out.push({ t: String(sg), tone: 'flat' }));
  return out;
}

export default evidenceLines;
