// ============================================================================
// CityDashboard — City-based dashboard with 6 tabs (Overview + 5 PS)
// Reads cityId from URL params; renders tab components for the selected city
// ============================================================================

import React, { useState, useMemo, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useFilters } from '../context/FilterContext';
import { useAuth } from '../auth/AuthContext';
import { CITIES, TOC_COMPANIES } from '../data/mockData';

// --- Tab Components ---
import CityOverviewTab from '../components/tabs/CityOverviewTab';
import PS1FailurePredictionTab from '../components/tabs/PS1FailurePredictionTab';
import PS2CascadingFailureTab from '../components/tabs/PS2CascadingFailureTab';
import PS4AnomalyDetectionTab from '../components/tabs/PS4AnomalyDetectionTab';
import PS5SLAReliabilityTab from '../components/tabs/PS5SLAReliabilityTab';

// --- Tab definitions ---
const TABS = [
  { id: 'overview',   label: 'Overview',            Component: CityOverviewTab },
  { id: 'ps1',        label: 'Failure Prediction',   Component: PS1FailurePredictionTab },
  { id: 'ps2',        label: 'Cascading Failure',    Component: PS2CascadingFailureTab },
  // PS3 RETIRED FROM THIS ROUTER, 04-Aug-2026. PK's call.
  //
  // Two reasons, and the second is the one that matters now:
  //
  //  1. It is superseded. PS3RootCauseTab reads /ps3/device-predictions,
  //     which is a CAPPED browse list -- 300 rows by default against a run
  //     covering thousands of devices -- and presents it as the fleet. The
  //     v2 PS3 tab at /v2 takes every total from a rollup instead.
  //  2. IT IS MISLABELLED. This entry called it 'Failure Severity', and
  //     this run publishes NO severity: severity_shippable is false on
  //     every row, because no source column survived the non-triviality
  //     gate. A tab named for a thing the data does not contain is the
  //     worst kind of wrong -- it is wrong in the navigation, before
  //     anyone has read a number.
  //
  // NOT deleted, and the API route is NOT removed. The component file is
  // left in place unimported, and /ps3/device-predictions still serves --
  // it now returns the CURRENT run (real components across all three
  // fleets, verified live 04-Aug), not the superseded 26-Jul one, and
  // ps3_device_predictions is the same table /ps1/device-360 reads for its
  // PS3 block. Removing the route would take Device 360 down with it.
  //
  // Restoring is putting this row and its import back.
  { id: 'ps4',        label: 'Anomaly Detection',     Component: PS4AnomalyDetectionTab },
  { id: 'ps5',        label: 'SLA & Reliability',     Component: PS5SLAReliabilityTab },
];

// --- City color map ---
const CITY_COLORS = { CHI: '#6366f1', BOS: '#f59e0b', LAX: '#ef4444', TOC: '#10b981' };

export default function CityDashboard() {
  const { cityId } = useParams();
  const { selectedDevices, selectedTOCs, toggleTOC } = useFilters();
  const { currentUser } = useAuth();

  const [activeTab, setActiveTab] = useState('overview');
  const [selectedTOCCompany, setSelectedTOCCompany] = useState('');

  // --- Resolve city metadata ---
  const city = useMemo(
    () => CITIES.find((c) => c.id === cityId) || { id: cityId, name: cityId, color: '#6366f1' },
    [cityId],
  );

  const cityColor = CITY_COLORS[cityId] || '#6366f1';

  // --- RBAC pre-selection for TOC company dropdown ---
  useEffect(() => {
    if (cityId !== 'TOC') return;
    const userTOCs = currentUser?.permissions?.tocs;
    if (userTOCs && userTOCs.length > 0 && userTOCs.length < TOC_COMPANIES.length) {
      // User has restricted TOC access — pre-select the first allowed one
      setSelectedTOCCompany(userTOCs[0]);
    } else {
      setSelectedTOCCompany('');
    }
  }, [cityId, currentUser]);

  // --- Reset tab when city changes ---
  useEffect(() => {
    setActiveTab('overview');
  }, [cityId]);

  // --- Handle TOC company selection ---
  const handleTOCChange = (e) => {
    const tocId = e.target.value;
    setSelectedTOCCompany(tocId);
    // Sync with global filter context
    if (tocId) {
      // Ensure only this TOC is selected in the filter
      selectedTOCs.forEach((t) => {
        if (t !== tocId) toggleTOC(t);
      });
      if (!selectedTOCs.includes(tocId)) toggleTOC(tocId);
    }
  };

  // --- Active tab component ---
  const activeTabDef = TABS.find((t) => t.id === activeTab) || TABS[0];
  const ActiveComponent = activeTabDef.Component;

  return (
    <div style={{ padding: 0 }}>
      {/* ---- Header ---- */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 20,
        }}
      >
        <div
          style={{
            width: 6,
            height: 32,
            borderRadius: 3,
            background: cityColor,
          }}
        />
        <h1
          style={{
            fontSize: 22,
            fontWeight: 700,
            color: 'var(--text)',
            margin: 0,
          }}
        >
          {city.name}
        </h1>

        {/* Pilot badge for Chicago */}
        {cityId === 'CHI' && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: '#6366f1',
              background: '#eef2ff',
              padding: '3px 10px',
              borderRadius: 12,
              letterSpacing: 0.3,
            }}
          >
            PILOT
          </span>
        )}

        {/* Company count badge for TOC */}
        {cityId === 'TOC' && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: '#10b981',
              background: '#ecfdf5',
              padding: '3px 10px',
              borderRadius: 12,
              letterSpacing: 0.3,
            }}
          >
            11 Companies
          </span>
        )}
      </div>

      {/* ---- TOC Company Dropdown (only for TOC city) ---- */}
      {cityId === 'TOC' && (
        <div
          style={{
            background: '#fff',
            border: '1px solid var(--border)',
            borderRadius: 10,
            padding: '12px 16px',
            marginBottom: 16,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <label
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--text)',
              whiteSpace: 'nowrap',
            }}
          >
            TOC Company
          </label>
          <select
            value={selectedTOCCompany}
            onChange={handleTOCChange}
            style={{
              padding: '6px 12px',
              borderRadius: 6,
              border: '1px solid var(--border)',
              fontSize: 13,
              color: 'var(--text)',
              background: '#fff',
              minWidth: 240,
              cursor: 'pointer',
              outline: 'none',
            }}
          >
            <option value="">All Companies</option>
            {TOC_COMPANIES.map((toc) => (
              <option key={toc.id} value={toc.id}>
                {toc.name} ({toc.id})
              </option>
            ))}
          </select>
        </div>
      )}

      {/* ---- Tab Bar ---- */}
      <div
        style={{
          display: 'flex',
          gap: 0,
          borderBottom: '2px solid var(--border)',
          marginBottom: 20,
        }}
      >
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '10px 20px',
              border: 'none',
              borderBottom:
                activeTab === tab.id
                  ? `2px solid ${cityColor}`
                  : '2px solid transparent',
              background: 'transparent',
              cursor: 'pointer',
              fontWeight: activeTab === tab.id ? 600 : 400,
              color:
                activeTab === tab.id
                  ? cityColor
                  : 'var(--text-secondary)',
              fontSize: 13,
              transition: 'all 0.15s',
              marginBottom: -2,
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ---- Active Tab Content ---- */}
      <ActiveComponent city={cityId} selectedDevices={selectedDevices} />
    </div>
  );
}
