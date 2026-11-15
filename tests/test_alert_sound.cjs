const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const html = readFileSync(join(__dirname, '../src/survai/dashboard.html'), 'utf8');
const source = html.slice(html.indexOf('class AlertSound {'), html.indexOf('  const alertSound ='));

function fixture({ supported = true, resume } = {}) {
  const voices = [];
  const parameter = { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} };
  const audio = {
    state: 'suspended', currentTime: 0, destination: {},
    async resume() { if (resume) await resume(); this.state = 'running'; },
    createGain() { return { gain: parameter, connect() {}, disconnect() {} }; },
    createOscillator() {
      const voice = { frequency: parameter, connect() {}, disconnect() {}, start() {}, stop() {} };
      voices.push(voice);
      return voice;
    },
  };
  const window = supported ? { AudioContext: function () { return audio; } } : {};
  const context = vm.createContext({ window });
  const Sound = vm.runInContext(`${source}\nAlertSound`, context);
  const toggle = { setAttribute(name, value) { this[name] = value; } };
  const testButton = {};
  const label = {};
  const sound = new Sound(toggle, testButton, label);
  const finish = () => voices.forEach(voice => voice.onended?.());
  return { sound, toggle, testButton, label, voices, audio, finish };
}

test('saved history is silent; new counter and walk-out alerts chime once, even after reconnect', async () => {
  const { sound, voices, finish } = fixture();
  await sound.setEnabled(true);
  assert.equal(voices.length, 2, 'enabling confirms audio with a chime');
  finish();
  sound.receive({ id: 'saved', type: 'possible_walkout' });
  assert.equal(voices.length, 2);
  sound.live = true; // server sends ready after saved history
  sound.receive({ id: 'saved', type: 'possible_walkout' });
  sound.receive({ id: 'counter', type: 'counter_wait' });
  assert.equal(voices.length, 4);
  finish();
  sound.receive({ id: 'counter', type: 'counter_wait', clip_status: 'ready' });
  sound.live = true; // reconnect must not replay old alerts
  sound.receive({ id: 'saved', type: 'possible_walkout' });
  assert.equal(voices.length, 4);
  sound.receive({ id: 'walkout', type: 'possible_walkout' });
  assert.equal(voices.length, 6);
});

test('mute silences alerts and test; enabling and testing each play a chime', async () => {
  const { sound, toggle, testButton, voices, finish } = fixture();
  sound.live = true;
  sound.receive({ id: 'before-enable' });
  await toggle.onclick();
  assert.equal(toggle['aria-pressed'], 'true');
  assert.equal(testButton.disabled, false);
  finish();
  await testButton.onclick();
  assert.equal(voices.length, 4);
  await toggle.onclick();
  assert.equal(toggle['aria-pressed'], 'false');
  assert.equal(testButton.disabled, true);
  sound.receive({ id: 'muted' });
  await testButton.onclick();
  assert.equal(voices.length, 4);
});

test('simultaneous alerts share a chime', async () => {
  const { sound, voices, finish } = fixture();
  await sound.setEnabled(true);
  finish();
  sound.live = true;
  sound.receive({ id: 'one' });
  sound.receive({ id: 'two' });
  assert.equal(voices.length, 4);
});

test('unsupported or blocked audio does not interrupt alerts', async () => {
  for (const options of [{ supported: false }, { resume: async () => { throw Error('blocked'); } }]) {
    const { sound, label, testButton } = fixture(options);
    await sound.setEnabled(true);
    assert.equal(sound.enabled, false);
    assert.equal(testButton.disabled, true);
    assert.match(label.textContent, /Could not play sound/);
    sound.live = true;
    assert.doesNotThrow(() => sound.receive({ id: 'still-visible' }));
  }
});

test('muting while audio resumes cancels pending playback', async () => {
  let resume;
  const waiting = new Promise(resolve => { resume = resolve; });
  const { sound, voices } = fixture({ resume: () => waiting });
  const enabling = sound.setEnabled(true);
  await sound.setEnabled(false);
  resume();
  await enabling;
  assert.equal(voices.length, 0);
});
