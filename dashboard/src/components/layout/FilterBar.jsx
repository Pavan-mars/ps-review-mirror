import React from 'react';
import { useFilters } from '../../context/FilterContext';

const FilterBar = () => {
  const {
    selectedDevices,
    toggleDevice,
    selectAllDevices,
    allowedDevices,
  } = useFilters();

  // READER is a component (embedded in TVM/GATE/VALIDATOR), not a filterable device type
  const allDevices = ['TVMs', 'Gates', 'Validators'];
  const visibleDevices = allDevices.filter((d) => allowedDevices.includes(d));

  const allDevicesSelected = allowedDevices.length > 0 && allowedDevices.every((d) => selectedDevices.includes(d));

  return (
    <div>
      <div className="filter-bar">
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
      </div>
    </div>
  );
};

export default FilterBar;
