const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'static', 'js', 'roof3d.js'),
  'utf8'
);
const context = { window: {}, console };
vm.createContext(context);
vm.runInContext(source, context);

const geometry = context.window.SIGNIX_ROOF3D_GEOMETRY;
assert.ok(geometry, 'roof geometry test API should be exposed');

const smallView = geometry.cameraFrameForBounds(
  { width: 4, depth: 3, height: 2 },
  16 / 9
);
const largeView = geometry.cameraFrameForBounds(
  { width: 40, depth: 28, height: 8 },
  16 / 9
);
assert.ok(smallView >= 5.5);
assert.ok(largeView > smallView * 3);

const roof = [[0, 0], [10, 0], [10, 8], [0, 8]];
const surface = [[0, 0], [5, 0], [5, 8], [0, 8]];
const validPanel = [[1, 1], [2, 1], [2, 2.5], [1, 2.5]];
const outsideSurface = [[4.5, 1], [5.5, 1], [5.5, 2.5], [4.5, 2.5]];
const obstruction = [[0.8, 0.8], [2.2, 0.8], [2.2, 2.7], [0.8, 2.7]];

assert.equal(geometry.polygonFits(validPanel, surface, []), true);
assert.equal(geometry.polygonFits(validPanel, roof, []), true);
assert.equal(geometry.polygonFits(outsideSurface, surface, []), false);
assert.equal(geometry.polygonFits(validPanel, surface, [obstruction]), false);
assert.equal(geometry.sumSurfacePanelCounts({ east: 7, west: 8 }), 15);
assert.equal(geometry.layoutCapacityKw(15, 545), 8.175);
assert.equal(geometry.layoutDiffersFromEstimate(8.175, 8.0), true);
assert.equal(geometry.layoutDiffersFromEstimate(8.1, 8.0), false);

console.log('roof3d geometry tests passed');
