const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { calculateExposure } = require('./src/exposure.js');

const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, 'test_vectors.json'), 'utf8'));

function assertDeepAlmostEqual(actual, expected, label) {
  if (typeof expected === 'number') {
    assert.equal(typeof actual, 'number', `${label}: expected number`);
    assert.ok(Math.abs(actual - expected) <= 1e-12, `${label}: expected ${expected}, got ${actual}`);
    return;
  }
  if (Array.isArray(expected)) {
    assert.equal(actual.length, expected.length, `${label}: array length`);
    expected.forEach((item, index) => assertDeepAlmostEqual(actual[index], item, `${label}[${index}]`));
    return;
  }
  if (expected && typeof expected === 'object') {
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), `${label}: object keys`);
    for (const key of Object.keys(expected)) {
      assertDeepAlmostEqual(actual[key], expected[key], `${label}.${key}`);
    }
    return;
  }
  assert.equal(actual, expected, label);
}

for (const vector of vectors) {
  const actual = calculateExposure(vector.input);
  assertDeepAlmostEqual(actual, vector.expected, vector.name);
}

assert.throws(
  () => calculateExposure({ holdings: [{ symbol: 'BAD', quantity: '1', price: 2 }] }),
  /quantity must be a finite number/,
  'rejects non-numeric quantity'
);

assert.throws(
  () => calculateExposure({ holdings: new Array(1001).fill({ symbol: 'X', quantity: 1, price: 1 }) }),
  /at most 1000 holdings/,
  'enforces bounded input size'
);

console.log(JSON.stringify({ ok: true, vectors: vectors.length, assertions: vectors.length + 2 }));
