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
    }),
    [
      selectedCities, selectedDevices, selectedTOCs, dateRange,
      toggleDevice, toggleTOC,
      selectAllDevices, selectAllTOCs,
      clearDevices, clearTOCs,
      isTOCSelected, allowedCities, allowedDevices, allowedTOCs,
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
