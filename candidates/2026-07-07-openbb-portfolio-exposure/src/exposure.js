(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ApexPortfolioExposure = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_HOLDINGS = 1000;

  function round12(value) {
    if (Object.is(value, -0)) return 0;
    const rounded = Math.round((value + Number.EPSILON) * 1e12) / 1e12;
    return Object.is(rounded, -0) ? 0 : rounded;
  }

  function assertFiniteNumber(value, field, index) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      throw new TypeError(`holding ${index} ${field} must be a finite number`);
    }
  }

  function label(value, fallback) {
    if (value === undefined || value === null || String(value).trim() === '') {
      return fallback;
    }
    return String(value);
  }

  function addToBucket(bucket, key, value) {
    bucket[key] = round12((bucket[key] || 0) + value);
  }

  function sortObjectByKey(object) {
    const sorted = {};
    for (const key of Object.keys(object).sort()) {
      sorted[key] = object[key];
    }
    return sorted;
  }

  function weightsFor(exposures, totalValue) {
    const weights = {};
    for (const key of Object.keys(exposures).sort()) {
      weights[key] = totalValue === 0 ? 0 : round12(exposures[key] / totalValue);
    }
    return weights;
  }

  function calculateExposure(input) {
    if (!input || !Array.isArray(input.holdings)) {
      throw new TypeError('input.holdings must be an array');
    }
    if (input.holdings.length > MAX_HOLDINGS) {
      throw new RangeError('input.holdings supports at most 1000 holdings');
    }

    const rawPositions = [];
    const exposureBySector = {};
    const exposureByCurrency = {};
    let totalValue = 0;

    input.holdings.forEach((holding, index) => {
      if (!holding || typeof holding !== 'object') {
        throw new TypeError(`holding ${index} must be an object`);
      }
      assertFiniteNumber(holding.quantity, 'quantity', index);
      assertFiniteNumber(holding.price, 'price', index);
      const symbol = label(holding.symbol, `#${index + 1}`);
      const sector = label(holding.sector, 'Unclassified');
      const currency = label(holding.currency, 'UNKNOWN');
      const value = round12(holding.quantity * holding.price);
      totalValue = round12(totalValue + value);
      rawPositions.push({ symbol, value, sector, currency });
      addToBucket(exposureBySector, sector, value);
      addToBucket(exposureByCurrency, currency, value);
    });

    const positions = rawPositions.map((position) => ({
      symbol: position.symbol,
      value: position.value,
      weight: totalValue === 0 ? 0 : round12(position.value / totalValue),
    }));

    let largestPosition = null;
    for (const position of positions) {
      if (
        largestPosition === null ||
        Math.abs(position.value) > Math.abs(largestPosition.value)
      ) {
        largestPosition = { ...position };
      }
    }

    return {
      total_value: totalValue,
      positions,
      exposure_by_sector: sortObjectByKey(exposureBySector),
      weights_by_sector: weightsFor(exposureBySector, totalValue),
      exposure_by_currency: sortObjectByKey(exposureByCurrency),
      weights_by_currency: weightsFor(exposureByCurrency, totalValue),
      largest_position: largestPosition,
      position_count: positions.length,
    };
  }

  return { calculateExposure, MAX_HOLDINGS };
});
