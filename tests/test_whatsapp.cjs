const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../src/survai/setup.js'), 'utf8');
const start = source.indexOf('// WhatsApp settings can be changed');
const block = source.slice(start, source.indexOf('(async () => {', start));
const saved = { enabled: true, configured: true, has_auth_token: true,
  account_sid: 'ACexample', sender: '+14155238886', recipient: '+15551234567', content_sid: '' };
function fixture(api) {
  const ids = ['enabled', 'account', 'token', 'sender', 'recipient', 'template', 'form', 'save', 'test', 'status'];
  const nodes = Object.fromEntries(ids.map(id => ['whatsapp-' + id, { value: '', dataset: {}, disabled: false }]));
  nodes['whatsapp-form'].reportValidity = () => true;
  nodes['whatsapp-form'].querySelectorAll = () => Object.values(nodes);
  const context = vm.createContext({ $: id => nodes[id], api });
  vm.runInContext(block, context);
  context.showWhatsApp(saved);
  return { context, nodes };
}

test('test button saves the current form, sends one test, and never refills the saved token', async () => {
  const calls = [];
  const { context, nodes } = fixture(async (path, method, body) => {
    calls.push({ path, method, body });
    return path.endsWith('/test') ? { status: 'queued' } : saved;
  });
  assert.equal(nodes['whatsapp-token'].value, '');
  nodes['whatsapp-token'].value = 'new-private-token';
  nodes['whatsapp-recipient'].value = '+15557654321';
  await context.saveWhatsApp(true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].path, '/api/whatsapp');
  assert.equal(calls[0].method, 'PUT');
  assert.equal(calls[0].body.recipient, '+15557654321');
  assert.equal(calls[0].body.auth_token, 'new-private-token');
  assert.equal(calls[1].path, '/api/whatsapp/test');
  assert.equal(calls[1].method, 'POST');
  assert.equal(nodes['whatsapp-token'].value, '');
  assert.match(nodes['whatsapp-status'].textContent, /accepted by Twilio \(queued\)/);
  assert.equal(nodes['whatsapp-test'].disabled, false);
});

test('failed save prevents sending and allows retry without losing the entered token', async () => {
  const calls = [];
  const { context, nodes } = fixture(async path => { calls.push(path); throw new Error('Check your number.'); });
  nodes['whatsapp-token'].value = 'private-token';
  await context.saveWhatsApp(true);
  assert.deepEqual(calls, ['/api/whatsapp']);
  assert.equal(nodes['whatsapp-token'].value, 'private-token');
  assert.equal(nodes['whatsapp-status'].textContent, 'Check your number.');
  assert.equal(nodes['whatsapp-status'].dataset.error, true);
  assert.equal(nodes['whatsapp-test'].disabled, false);
});

test('concurrent clicks send only one test and provider rejection stays visible', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const calls = [];
  const { context, nodes } = fixture(async path => {
    calls.push(path);
    if (path.endsWith('/test')) throw new Error('Use an approved template.');
    await pending;
    return saved;
  });
  const first = context.saveWhatsApp(true);
  await context.saveWhatsApp(true);
  release();
  await first;
  assert.deepEqual(calls, ['/api/whatsapp', '/api/whatsapp/test']);
  assert.equal(nodes['whatsapp-status'].textContent, 'Use an approved template.');
  assert.equal(nodes['whatsapp-test'].disabled, false);
});
