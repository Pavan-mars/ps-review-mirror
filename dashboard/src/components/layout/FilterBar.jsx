import React from 'react';
import { useFilters } from '../../context/FilterContext';

const FilterBar = () => {
  const {
    selectedDevices,
    toggleDevice,
    selectAllDevices,
    allowedDevices,
    deviceQuery,
    setDeviceQuery,
    componentOptions,
    selectedComponents,
    toggleComponent,
    selectAllComponents,
    failureTypeOptions,
    selectedFailureTypes,
    toggleFailureType,
    selectAllFailureTypes,
  } = useFilters();

  // READER is a component (embedded in TVM/GATE/VALIDATOR), not a filterable device type
  const allDevices = ['TVMs', 'Gates', 'Validators'];
  const visibleDevices = allDevices.filter((d) => allowedDevices.includes(d));

  const allDevicesSelected = allowedDevices.length > 0 && allowedDevices.every((d) => selectedDevices.includes(d));
  const allComponentsSelected = componentOptions.length > 0 && componentOptions.every((c) => selectedComponents.includes(c));
  const allFailureTypesSelected = failureTypeOptions.length > 0 && failureTypeOptions.every((ft) => selectedFailureTypes.includes(ft));

  return (
    <div>
      <div className="filter-bar">
        <div className="filter-group">
          <label className="filter-label">DEVICE / SERIAL</label>
          <input
            type="text"
            value={deviceQuery}
            onChange={(e) => setDeviceQuery(e.target.value)}
            placeholder="Search device ID or serial no…"
            style={{
              padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)',
              fontSize: 12, minWidth: 200, color: 'var(--text)', background: '#fff',
            }}
          />
          {deviceQuery && (
            <button className="filter-btn" onClick={() => setDeviceQuery('')} title="Clear device/serial search">×</button>
          )}
        </div>

        <div className="filter-group">
          <label className="filter-label">DEVICES</label>
          {visibleDevices.length > 1 && (
            <button
              className={`filter-btn${allDevicesSelected ? ' all-active' : ''}`}
              onClick={selectAllDevices}
            >
              All
            </button>
          )}
          {visibleDevices.map((device) => (
            <button
              key={device}
              className={`filter-btn${selectedDevices.includes(device) ? ' active' : ''}`}
              onClick={() => toggleDevice(device)}
            >
              {device}
            </button>
          ))}
        </div>

        {/* Component (e.g. PS2 subsystem) and Failure-Type (e.g. PS2 error code)
            facets only appear once the active page registers its option
            vocabulary via useFilters().registerComponentOptions /
            registerFailureTypeOptions -- keeps this bar dashboard-wide without
            showing empty/irrelevant filters on pages that don't have that
            concept yet. */}
        {componentOptions.length > 0 && (
          <div className="filter-group">
            <label className="filter-label">COMPONENT</label>
            <button
              className={`filter-btn${allComponentsSelected ? ' all-active' : ''}`}
              onClick={selectAllComponents}
            >
              All
            </button>
            {componentOptions.map((c) => (
              <button
                key={c}
                className={`filter-btn${selectedComponents.includes(c) ? ' active' : ''}`}
                onClick={() => toggleComponent(c)}
              >
                {c}
              </button>
            ))}
          </div>
        )}

        {failureTypeOptions.length > 0 && (
          <div className="filter-group">
            <label className="filter-label">FAILURE TYPE</label>
            <button
              className={`filter-btn${allFailureTypesSelected ? ' all-active' : ''}`}
              onClick={selectAllFailureTypes}
            >
              All
            </button>
            {failureTypeOptions.map((ft) => (
              <button
                key={ft}
                className={`filter-btn${selectedFailureTypes.includes(ft) ? ' active' : ''}`}
                onClick={() => toggleFailureType(ft)}
              >
                {ft}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default FilterBar;
