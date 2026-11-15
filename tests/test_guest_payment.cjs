const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const html = readFileSync(join(__dirname, '../src/survai/dashboard.html'), 'utf8');
const source = html.slice(html.indexOf("  let lastVisitCards = ''"), html.indexOf('  function showAlertThumbnail('));
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ''; }
  setAttribute(name, value) { this[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const find = (node, predicate) => [node, ...node.children.flatMap(child => find(child, () => true))].filter(predicate);
const guest = (id, state = 'DINING', employee = false) => ({
  track_id: id, state, employee, visible: true, first_seen: new Date().toISOString(), cameras: ['cam1'],
});
function fixture() {
  const visits = new Element('div');
  const calls = [];
  const context = vm.createContext({
    document: { createElement: tag => new Element(tag), querySelector: () => ({ hidden: false }) },
    visits, window: {}, webMode: true, cameraStatus: { dataset: { live: 'true' } },
    makeIcon: () => new Element('svg'), formatDwell: String,
    markGuestPaid: id => calls.push(id),
  });
  vm.runInContext(source, context);
  return { context, visits, calls };
}

test('each guest has an independent payment button; employees have none', () => {
  const { context, visits, calls } = fixture();
  context.renderVisits([guest(1), guest(2), guest(3, 'EMPLOYEE', true)]);
  const buttons = find(visits, node => node.tag === 'button');
  assert.equal(buttons.length, 2);
  assert.deepEqual(buttons.map(button => button.textContent), ['Has paid', 'Has paid']);
  buttons[1].onclick();
  assert.deepEqual(calls, [2]);
  context.renderVisits([guest(1), guest(2, 'PAID'), guest(3, 'EMPLOYEE', true)]);
  const updated = find(visits, node => node.tag === 'button');
  assert.equal(updated[0].disabled, false);
  assert.equal(updated[1].textContent, 'Paid');
  assert.equal(updated[1].disabled, true);
});

test('payment controls disable when stopped or out of view and disappear in people-only mode', () => {
  const { context, visits } = fixture();
  context.cameraStatus.dataset.live = 'false';
  context.renderVisits([guest(1)]);
  assert.equal(find(visits, node => node.tag === 'button')[0].disabled, true);
  context.cameraStatus.dataset.live = 'true';
  context.renderVisits([guest(1)]);
  assert.equal(find(visits, node => node.tag === 'button')[0].disabled, false);
  context.renderVisits([{ ...guest(1), visible: false }], true);
  assert.equal(find(visits, node => node.tag === 'button')[0].disabled, true);
  context.renderVisits([guest(1)], false, true);
  assert.equal(find(visits, node => node.tag === 'button').length, 0);
});

test('clicking payment posts only that guest ID and waits for tracker confirmation', async () => {
  const { context } = fixture();
  const requests = [];
  context.fetch = async (url, options) => { requests.push([url, options.method]); return { ok: true }; };
  context.showSessionMessage = () => {};
  context.refresh = async () => {};
  const action = html.slice(html.indexOf('  async function markGuestPaid('), html.indexOf('  async function cameraAction('));
  vm.runInContext(action, context);
  const button = {};
  await context.markGuestPaid(17, button);
  assert.deepEqual(requests, [['/api/visits/17/pay', 'POST']]);
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Updating\u2026');
  assert.equal(vm.runInContext('pendingPayments.has(17)', context), true);
  context.renderVisits([guest(17, 'PAID')]);
  assert.equal(vm.runInContext('pendingPayments.has(17)', context), false);
});
