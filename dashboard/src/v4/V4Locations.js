// =====================================================================
// V4Locations -- ONE place that turns a facility_id into a location name.
//
// THE PROBLEM THIS SOLVES.
// Five tabs each had their own idea of what a place is called. PS1 showed
// "North Park" because /ps1/station-summary LEFT JOINs dim_device_station
// for the garage name. PS2, PS3, PS4 and PS5 showed "45", because their
// serving tables carry facility_id and nothing joins the dimension. An
// operations person reading the estate across tabs therefore saw the same
// depot under two different identities, and had no way to know it was one
// place.
//
// WHERE THE NAMES COME FROM, AND WHY THERE ARE TWO SOURCES.
// /ps1/facilities serves the facility DIMENSION: all 2,183 facility_ids in
// dim_device_station, every one of which has a name. That route was added
// on 06-Aug-2026 precisely because the names existed and nothing served
// them -- /ps1/station-summary can only name a facility PS1 actually
// scored, and PS2/PS3/PS4/PS5 reference facilities PS1 never saw.
//
// THE FALLBACK IS DELIBERATE. If /ps1/facilities is missing -- the front
// end is deployed but the Lambda is not yet -- this drops back to
// /ps1/station-summary, which is exactly the old behaviour: fewer names,
// no errors, nothing blank. So the dashboard can ship before the API and
// improves by itself the moment the API catches up. Nothing to coordinate,
// nothing to roll back.
//
// A facility that resolves in NEITHER falls back to its bare id. Inventing
// a label, or hiding the row, would both be worse than showing the number
// the database actually holds.
//
// FLOAT IDS. facility_id reaches PS5 as the string "44.0" because it came
// through a float column, and as "44" everywhere else. Both normalise to
// "44" here, so the join actually lands. This was already worked around
// locally inside V4PS5Overview; it belongs in one place.
// =====================================================================
import { useEffect, useState } from 'react';
import { ps1 as ps1api } from './V4api';

// "44.0" -> "44"; "  45 " -> "45"; null/nan/None -> ''
export function normFacilityId(v) {
  const s = String(v === null || v === undefined ? '' : v).trim();
  if (!s || s === 'nan' || s === 'None' || s === 'null') return '';
  return s.endsWith('.0') ? s.slice(0, -2) : s;
}

const CACHE = new Map();    // city -> Map(id -> {name, operator})
const INFLIGHT = new Map(); // city -> Promise
const SUBS = new Set();     // re-render hooks when a city resolves

function emit() { SUBS.forEach((fn) => { try { fn(); } catch (e) { /* a dead subscriber must not stop the others */ } }); }

export function loadLocations(city) {
  const c = String(city || 'CHI').toUpperCase();
  if (CACHE.has(c)) return Promise.resolve(CACHE.get(c));
  if (INFLIGHT.has(c)) return INFLIGHT.get(c);
  const collect = (rowsIn) => {
    const m = new Map();
    (rowsIn || []).forEach((r) => {
      const id = normFacilityId(r.facility_id);
      if (!id) return;
      // Collapse whitespace runs here too. The API normalises, but the
      // fallback route does not, and "North  Park" must not read as a
      // different place from "North Park".
      const name = String(r.facility_name || '').replace(/\s+/g, ' ').trim();
      // An empty facility_name is NOT a name. Storing '' would make the
      // lookup succeed and then render nothing, which reads as a bug.
      if (name) m.set(id, { name, operator: String(r.operator_name || '').trim() });
    });
    return m;
  };

  const p = ps1api.facilities(c)
    .then((rowsIn) => {
      const m = collect(rowsIn);
      // An empty result means the route is not deployed yet (or returned
      // nothing useful). Fall back rather than cache a blank map.
      if (m.size) return m;
      return ps1api.stations(c).then(collect);
    })
    .catch(() => ps1api.stations(c).then(collect).catch(() => new Map()))
    .then((m) => {
      CACHE.set(c, m);
      INFLIGHT.delete(c);
      emit();
      return m;
    })
    .catch(() => {
      // Both routes failed. Degrade to ids -- never blank the tab that
      // called it. Cache the empty map so we do not retry on every render.
      CACHE.set(c, new Map());
      INFLIGHT.delete(c);
      emit();
      return CACHE.get(c);
    });
  INFLIGHT.set(c, p);
  return p;
}

// The hook every tab uses. Returns stable helpers that fall back to the id.
export function useLocations(city = 'CHI') {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    SUBS.add(fn);
    loadLocations(city);
    return () => { SUBS.delete(fn); };
  }, [city]);

  const c = String(city || 'CHI').toUpperCase();
  const m = CACHE.get(c);

  return {
    ready: !!m,
    // "North Park" when known, otherwise the bare id, otherwise "--".
    name: (id) => {
      const k = normFacilityId(id);
      if (!k) return '--';
      const hit = m && m.get(k);
      return hit ? hit.name : k;
    },
    // "North Park (45)" -- for tables where the id still has to be findable.
    label: (id) => {
      const k = normFacilityId(id);
      if (!k) return '--';
      const hit = m && m.get(k);
      return hit ? `${hit.name} (${k})` : k;
    },
    operator: (id) => {
      const hit = m && m.get(normFacilityId(id));
      return hit ? hit.operator : '';
    },
    // True when the id resolved to a real name. Lets a caller mark unmapped
    // rows rather than silently presenting an id as if it were a name.
    known: (id) => !!(m && m.get(normFacilityId(id))),
  };
}

export default useLocations;
