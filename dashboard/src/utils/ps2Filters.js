// ============================================================================
// ps2Filters.js -> src/utils/ps2Filters.js
// NEW (24-Jul-2026): shared client-side row filtering for the PS2 tab, driven
// by the dashboard-wide FilterContext (device/serial search, device type,
// component/subsystem, failure-type/error-code). Client-side because the
// underlying tables are already fully fetched per city (small, pre-aggregated
// PS2 notebook exports) -- no new API query params needed, and it keeps
// filtering instant/local rather than a round-trip per toggle.
//
// Every matcher is permissive-by-default: if a row doesn't carry the relevant
// field (e.g. a subsystem-grain row has no device_id), that filter is treated
// as "not applicable" rather than hiding the row -- so filters only ever
// narrow tables that actually have the matching column.
// ============================================================================

// Exported so other PS2 components (e.g. the device-type breakdown donut in
// PS2CascadingFailureTab.jsx) can map a raw category ('TVM'/'GATE'/'VALIDATOR')
// to the FilterBar's UI label ('TVMs'/'Gates'/'Validators') without duplicating
// this table.
export const DEVICE_CATEGORY_LABEL = { TVM: 'TVMs', GATE: 'Gates', VALIDATOR: 'Validators' };

export function matchesDeviceQuery(row, deviceQuery) {
  const q = (deviceQuery || '').trim().toLowerCase();
  if (!q) return true;
  const candidates = [row.device_id, row.serial_id, row.serial, row.entity_id];
  return candidates.some((v) => v != null && String(v).toLowerCase().includes(q));
}

export function matchesDeviceType(row, selectedDevices) {
  const category = row.category || row.device_category;
  if (!category) return true; // no device-type column on this row shape -> not applicable
  if (!selectedDevices) return true;
  const label = DEVICE_CATEGORY_LABEL[category] || category;
  return selectedDevices.includes(label);
}

export function matchesComponent(row, selectedComponents, componentOptions) {
  if (!componentOptions || componentOptions.length === 0) return true; // facet not active
  const candidates = [
    row.sub_a, row.sub_b, row.subsystem, row.dom_subsystem, row.top_subsystem,
    row.from_sub, row.to_sub, row.node_id, row.subsystem_from, row.subsystem_to,
  ];
  const hasComponentField = candidates.some((v) => v != null);
  if (!hasComponentField) return true; // row shape doesn't carry a component field -> not applicable
  if (!selectedComponents || selectedComponents.length === 0) return false;
  return candidates.some((v) => v != null && selectedComponents.includes(v));
}

export function matchesFailureType(row, selectedFailureTypes, failureTypeOptions) {
  if (!failureTypeOptions || failureTypeOptions.length === 0) return true; // facet not active
  const candidates = [row.error_code, row.dom_error_code, row.from_code, row.to_code];
  const hasFailureField = candidates.some((v) => v != null);
  if (!hasFailureField) return true; // row shape doesn't carry a failure-type field -> not applicable
  if (!selectedFailureTypes || selectedFailureTypes.length === 0) return false;
  return candidates.some((v) => v != null && selectedFailureTypes.includes(String(v)));
}

// filters: the object returned by useFilters() (deviceQuery, selectedDevices,
// selectedComponents, componentOptions, selectedFailureTypes, failureTypeOptions).
export function applyPS2Filters(rows, filters) {
  if (!Array.isArray(rows)) return rows;
  const { deviceQuery, selectedDevices, selectedComponents, componentOptions, selectedFailureTypes, failureTypeOptions } = filters || {};
  return rows.filter((row) =>
    matchesDeviceQuery(row, deviceQuery) &&
    matchesDeviceType(row, selectedDevices) &&
    matchesComponent(row, selectedComponents, componentOptions) &&
    matchesFailureType(row, selectedFailureTypes, failureTypeOptions)
  );
}

// True while any facet is actively narrowing (used to show a small "N of M
// filtered" hint rather than silently returning fewer rows with no context).
export function isAnyPS2FilterActive(filters) {
  if (!filters) return false;
  const { deviceQuery, selectedDevices, selectedComponents, componentOptions, selectedFailureTypes, failureTypeOptions } = filters;
  if (deviceQuery && deviceQuery.trim()) return true;
  if (componentOptions && componentOptions.length > 0 && selectedComponents && selectedComponents.length < componentOptions.length) return true;
  if (failureTypeOptions && failureTypeOptions.length > 0 && selectedFailureTypes && selectedFailureTypes.length < failureTypeOptions.length) return true;
  if (selectedDevices && selectedDevices.length < 3) return true; // 3 = TVMs/Gates/Validators
  return false;
}
