import React, { createContext, useContext, useState, useCallback, useMemo, useEffect } from 'react';
import { TOC_COMPANIES } from '../data/mockData';
import { useAuth } from '../auth/AuthContext';

const ALL_CITY_OPTIONS = ['CHI', 'BOS', 'LAX', 'TOC'];
// READER reversed Jul-11 to a component-level feature slice (200-series device events embedded
// inside TVM/GATE/VALIDATOR), not a 4th device category — no standalone Reader device filter.
const ALL_DEVICE_OPTIONS = ['TVMs', 'Gates', 'Validators'];
const ALL_TOC_OPTIONS = TOC_COMPANIES.map((t) => t.id);

const FilterContext = createContext(null);

function getDefaultDateRange() {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 30);
  return {
    start: start.toISOString().split('T')[0],
    end: end.toISOString().split('T')[0],
  };
}

export function FilterProvider({ children }) {
  const { currentUser, isAuthenticated } = useAuth();

  // --- Compute allowed options based on RBAC ---
  const allowedCities = useMemo(() => {
    if (!currentUser) return ALL_CITY_OPTIONS;
    if (currentUser.role === 'admin') return ALL_CITY_OPTIONS;
    return ALL_CITY_OPTIONS.filter((c) => currentUser.permissions?.cities?.includes(c));
  }, [currentUser]);

  const allowedDevices = useMemo(() => {
    if (!currentUser) return ALL_DEVICE_OPTIONS;
    if (currentUser.role === 'admin') return ALL_DEVICE_OPTIONS;
    return ALL_DEVICE_OPTIONS.filter((d) => currentUser.permissions?.devices?.includes(d));
  }, [currentUser]);

  const allowedTOCs = useMemo(() => {
    if (!currentUser) return ALL_TOC_OPTIONS;
    if (currentUser.role === 'admin') return ALL_TOC_OPTIONS;
    return ALL_TOC_OPTIONS.filter((t) => currentUser.permissions?.tocs?.includes(t));
  }, [currentUser]);

  // --- State initialised to allowed sets ---
  const [selectedCities, setSelectedCities] = useState([...ALL_CITY_OPTIONS]);
  const [selectedDevices, setSelectedDevices] = useState([...ALL_DEVICE_OPTIONS]);
  const [selectedTOCs, setSelectedTOCs] = useState([...ALL_TOC_OPTIONS]);
  const [dateRange, setDateRange] = useState(getDefaultDateRange);

  // --- Dashboard-wide granular filters (24-Jul-2026) ---------------------
  // deviceQuery: free-text device ID / serial number search, applies to any
  // tab that has row-level device_id or serial_id data (client-side filter,
  // no API/query-param change needed).
  const [deviceQuery, setDeviceQuery] = useState('');

  // selectedFailureTypes: e.g. PS2 error codes / severity bands. Vocabulary
  // is page-specific (PS2's error codes differ from PS3's severity labels),
  // so a page registers its own option list via setFailureTypeOptions on
  // mount (and clears it on unmount) rather than the vocabulary being fixed
  // here -- keeps this context reusable dashboard-wide instead of PS2-only.
  const [failureTypeOptions, setFailureTypeOptions] = useState([]);
  const [selectedFailureTypes, setSelectedFailureTypes] = useState([]);

  // selectedComponents: e.g. PS2 subsystems (SYSTEM, COMMS, CHU, ...). Same
  // page-registered-vocabulary pattern as failure types above.
  const [componentOptions, setComponentOptions] = useState([]);
  const [selectedComponents, setSelectedComponents] = useState([]);

  // When user changes (login/logout), reset selections to their allowed set
  useEffect(() => {
    setSelectedCities([...allowedCities]);
    setSelectedDevices([...allowedDevices]);
    setSelectedTOCs([...allowedTOCs]);
  }, [allowedCities, allowedDevices, allowedTOCs]);

  const toggleDevice = useCallback((device) => {
    if (!allowedDevices.includes(device)) return;
    setSelectedDevices((prev) =>
      prev.includes(device) ? prev.filter((d) => d !== device) : [...prev, device]
    );
  }, [allowedDevices]);

  const toggleTOC = useCallback((tocId) => {
    if (!allowedTOCs.includes(tocId)) return;
    setSelectedTOCs((prev) =>
      prev.includes(tocId) ? prev.filter((t) => t !== tocId) : [...prev, tocId]
    );
  }, [allowedTOCs]);

  const selectAllDevices = useCallback(() => setSelectedDevices([...allowedDevices]), [allowedDevices]);
  const selectAllTOCs = useCallback(() => setSelectedTOCs([...allowedTOCs]), [allowedTOCs]);

  const clearDevices = useCallback(() => setSelectedDevices([]), []);
  const clearTOCs = useCallback(() => setSelectedTOCs([]), []);

  // --- Failure-type / component toggles (same shape as toggleDevice above) ---
  const toggleFailureType = useCallback((val) => {
    setSelectedFailureTypes((prev) => (prev.includes(val) ? prev.filter((v) => v !== val) : [...prev, val]));
  }, []);
  const selectAllFailureTypes = useCallback(() => setSelectedFailureTypes([...failureTypeOptions]), [failureTypeOptions]);
  const clearFailureTypes = useCallback(() => setSelectedFailureTypes([]), []);

  const toggleComponent = useCallback((val) => {
    setSelectedComponents((prev) => (prev.includes(val) ? prev.filter((v) => v !== val) : [...prev, val]));
  }, []);
  const selectAllComponents = useCallback(() => setSelectedComponents([...componentOptions]), [componentOptions]);
  const clearComponents = useCallback(() => setSelectedComponents([]), []);

  // A page registers its option vocabulary (and gets an all-selected default)
  // on mount; call with [] on unmount to hide the facet again in FilterBar.
  const registerFailureTypeOptions = useCallback((opts) => {
    setFailureTypeOptions(opts || []);
    setSelectedFailureTypes(opts && opts.length ? [...opts] : []);
  }, []);
  const registerComponentOptions = useCallback((opts) => {
    setComponentOptions(opts || []);
    setSelectedComponents(opts && opts.length ? [...opts] : []);
  }, []);

  const isTOCSelected = selectedCities.includes('TOC');

  const value = useMemo(
    () => ({
      selectedCities,
      selectedDevices,
      selectedTOCs,
      dateRange,
      setDateRange,
      toggleDevice,
      toggleTOC,
      selectAllDevices,
      selectAllTOCs,
      clearDevices,
      clearTOCs,
      isTOCSelected,
      // Expose allowed options so FilterBar can grey out / hide forbidden ones
      cityOptions: ALL_CITY_OPTIONS,
      deviceOptions: ALL_DEVICE_OPTIONS,
      tocOptions: ALL_TOC_OPTIONS,
      allowedCities,
      allowedDevices,
      allowedTOCs,
      // Dashboard-wide granular filters
      deviceQuery,
      setDeviceQuery,
      failureTypeOptions,
      selectedFailureTypes,
      toggleFailureType,
      selectAllFailureTypes,
      clearFailureTypes,
      registerFailureTypeOptions,
      componentOptions,
      selectedComponents,
      toggleComponent,
      selectAllComponents,
      clearComponents,
      registerComponentOptions,
    }),
    [
      selectedCities, selectedDevices, selectedTOCs, dateRange,
      toggleDevice, toggleTOC,
      selectAllDevices, selectAllTOCs,
      clearDevices, clearTOCs,
      isTOCSelected, allowedCities, allowedDevices, allowedTOCs,
      deviceQuery, failureTypeOptions, selectedFailureTypes,
      toggleFailureType, selectAllFailureTypes, clearFailureTypes, registerFailureTypeOptions,
      componentOptions, selectedComponents,
      toggleComponent, selectAllComponents, clearComponents, registerComponentOptions,
    ]
  );

  return (
    <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
  );
}

export function useFilters() {
  const context = useContext(FilterContext);
  if (!context) {
    throw new Error('useFilters must be used within a FilterProvider');
  }
  return context;
}

export default FilterContext;
