const $ = id => document.getElementById(id);
const zoneNames = {
  dining_zone: ['Dining', '#93d7ff'],
  pay_zone: ['Counter', '#81f4c1'],
  exit_band: ['Exit', '#ffcb91'],
  exit_line: ['Exit', '#ffcb91'],
};
let setup;
let session = { mode: 'idle', previews: {} };
let cards = new Map();
let zone = null;
let landmark = {};
let busy = false;
let frozen = false;
let hasFrames = false;
let revision = 0;

async function api(path, method = 'GET', data) {
  const options = { method, cache: 'no-store' };
  if (data !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map(error => error.msg).join(' ') : body.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return response.status === 204 || response.status === 202 ? undefined : response.json();
}

function message(text = '', error = false) {
  $('message').textContent = text;
  $('message').dataset.error = error;
}

async function action(callback) {
  if (busy) return;
  busy = true;
  message();
  renderControls();
  try {
    await callback();
  } catch (error) {
    message(error.message, true);
  } finally {
    busy = false;
    renderControls();
  }
}

function node(tag, text) {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  return element;
}

function field(text, type, value, onChange) {
  const label = node('label', type === 'checkbox' ? null : text);
  const input = node('input');
  input.type = type;
  if (type === 'checkbox') {
    label.className = 'check';
    input.checked = value;
    label.append(input, document.createTextNode(text));
  } else {
    input.value = value;
    label.append(input);
  }
  input.addEventListener('change', () => onChange(type === 'checkbox' ? input.checked : input.value.trim()));
  return { label, input };
}

function cameraChanged(camera, changes) {
  const original = structuredClone(camera);
  Object.assign(camera, changes);
  const sourceChanged = String(original.source) !== String(camera.source);
  const mirrorOnly = !sourceChanged && original.mirror !== camera.mirror;
  if (mirrorOnly) {
    const zones = cameraZones(camera);
    for (const key of Object.keys(zoneNames)) {
      const points = zones[key];
      camera.zones[key] = points ? points.map(([x, y]) => [1 - x, y]).reverse() : null;
    }
    delete camera.zones.dining;
    delete camera.zones.pay;
    if (camera.door_line) camera.door_line = camera.door_line.map(([x, y]) => [1 - x, y]).reverse();


  }
  if (sourceChanged || mirrorOnly) {
    zone = null;
    camera.floor_points = null;
    if (sourceChanged) {
      camera.door_line = null;
      camera.apron_color = null;
    }
    clearCalibration();
    message('Camera source or mirroring changed. Check zones and redraw shared landmarks.');
  }
  renderCameras();
}

function clearCalibration() {
  setup.calibration = null;
  landmark = {};
}

const doorCountingEnabled = camera => !(camera.walkout_enabled && camera.employee_marker);

function cameraRulesChanged(camera, changes) {
  Object.assign(camera, changes);
  if (!doorCountingEnabled(camera)) {
    if (tool() === 'door_line' && zoneCamera()?.id === camera.id) zone = null;
  }
  updateZonePicker();
}

function cameraCard(camera) {
  const card = node('article');
  card.className = 'card';
  const title = node('h3', camera.name || 'Camera');
  const status = node('small', camera.enabled === false ? 'Paused' : '');
  const fields = node('div');
  fields.className = 'fields';
  const name = field('Camera name', 'text', camera.name, value => {
    camera.name = value || camera.id;
    title.textContent = camera.name;
    updateZonePicker();
  });
  name.input.maxLength = 60;
  const source = field('Camera index or video path', 'text', camera.source, value => {
    cameraChanged(camera, { source: /^\d+$/.test(value) ? Number(value) : value });
  });
  fields.append(name.label, source.label);
  const controls = node('div');
  controls.className = 'toolbar';
  const mirror = field('Mirror', 'checkbox', camera.mirror, value => cameraChanged(camera, { mirror: value }));
  const rules = field('Walk-out rules', 'checkbox', camera.walkout_enabled,
    value => cameraRulesChanged(camera, { walkout_enabled: value }));
  const remove = node('button', 'Remove');
  remove.type = 'button';
  remove.addEventListener('click', () => {
    zone = null;
    setup.cameras = setup.cameras.filter(item => item.id !== camera.id);
    clearCalibration();
    renderCameras();
  });
  const enabled = field('Camera enabled', 'checkbox', camera.enabled !== false, value => {
    camera.enabled = value;
    zone = null;
    landmark = {};
    renderCameras();
  });
  const marker = field('RGB employee marker', 'checkbox', Boolean(camera.employee_marker),
    value => cameraRulesChanged(camera, { employee_marker: value }));
  controls.append(enabled.label, mirror.label, rules.label, marker.label, remove);
  const surface = node('div');
  surface.className = 'surface';
  const image = node('img');
  image.alt = `${camera.name} preview`;
  image.hidden = true;
  const empty = node('p', camera.enabled === false ? 'Enable this camera to connect it.' : 'Connect previews to see this camera.');
  const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  overlay.setAttribute('viewBox', '0 0 100 100');
  overlay.setAttribute('preserveAspectRatio', 'none');
  overlay.setAttribute('aria-hidden', 'true');
  overlay.style.display = 'none';
  overlay.addEventListener('click', event => addPoint(camera.id, event, overlay));
  surface.append(image, empty, overlay);
  card.append(title, status, fields, controls, surface);
  return { card, status, image, overlay, empty, source: source.input, mirror: mirror.input,
    name: name.input, rules: rules.input, enabled: enabled.input, marker: marker.input, remove, fetching: false, url: null };
}

function clearFrame(card) {
  if (card.url) URL.revokeObjectURL(card.url);
  card.url = null;
  card.image.removeAttribute('src');
  card.image.hidden = true;
  card.empty.hidden = false;
  card.overlay.style.display = 'none';
}

function renderCameras() {
  revision++;
  for (const card of cards.values()) clearFrame(card);
  cards = new Map(setup.cameras.map(camera => [camera.id, cameraCard(camera)]));
  $('cameras').replaceChildren(...[...cards.values()].map(card => card.card));
  updateZonePicker();
}

function updateZonePicker() {
  const active = activeCameras();
  const walkout = active.some(camera => camera.walkout_enabled);
  for (const option of $('tool').options) {
    option.hidden = option.value === 'landmark' ? active.length < 2 || active.length < setup.cameras.length :
      option.value === 'door_line' ? !active.some(doorCountingEnabled) : !walkout;
    option.disabled = option.hidden;
  }
  if ($('tool').selectedOptions[0]?.disabled) {
    $('tool').value = [...$('tool').options].find(option => !option.disabled)?.value || '';
  }
  const picker = $('zone-camera');
  const current = picker.value;
  picker.replaceChildren(...active.filter(camera => tool() === 'door_line' ?
    doorCountingEnabled(camera) : camera.walkout_enabled).map(camera => {
    const option = node('option', camera.name);
    option.value = camera.id;
    return option;
  }));
  if ([...picker.options].some(option => option.value === current)) picker.value = current;
  renderControls();
}

const activeCameras = () => setup.cameras.filter(camera => camera.enabled !== false);
const zoneCamera = () => setup.cameras.find(camera => camera.id === $('zone-camera').value);
const tool = () => $('tool').value;
const landmarks = () => setup.calibration?.landmarks || [];

function rectanglePoints(points) {
  if (!points?.length) return [];
  if (points.length === 1) return points;
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const left = Math.min(...xs), right = Math.max(...xs);
  const top = Math.min(...ys), bottom = Math.max(...ys);
  return [[left, top], [right, top], [right, bottom], [left, bottom]];
}

function cameraZones(camera) {
  const zones = { ...setup.walkout, ...camera.zones };
  if (camera.zones.exit_band && !('exit_line' in camera.zones)) zones.exit_line = null;
  if (camera.zones.exit_line && !('exit_band' in camera.zones)) zones.exit_band = null;
  return zones;
}

function validateDraft() {
  if (zone !== null) throw new Error('Apply the zone you’re drawing, or undo its points before saving.');
  if (Object.keys(landmark).length) throw new Error('Finish clicking the current landmark in every camera, or undo it.');
}

function addPoint(id, event, overlay) {
  const card = cards.get(id);
  if (busy || session.mode !== 'preview' || !card.image.complete || !card.url ||
      session.previews[id] !== 'Ready') return;
  const rect = overlay.getBoundingClientRect();
  const point = [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))].map(value => Number(value.toFixed(6)));
  if (tool() === 'landmark') {
    const next = setup.cameras.find(camera => !landmark[camera.id]);
    if (!next || next.id !== id) return;
    landmark[id] = point;
    if (Object.keys(landmark).length === setup.cameras.length) {
      setup.calibration ??= { reference: setup.cameras[0].id, landmarks: [] };
      setup.calibration.landmarks.push({ name: `landmark_${landmarks().length + 1}`, ...landmark });
      landmark = {};
    }
  } else {
    if (zoneCamera()?.id !== id) return;
    if (tool() === 'door_line' && !doorCountingEnabled(zoneCamera())) return;
    if (tool() === 'apron_color') {
      const camera = zoneCamera();
      camera.apron_color = sampleColor(card.image, point);
      message(`Apron color sampled for ${camera.name}. Save setup to keep it.`);
      renderControls();
      return;
    }
    zone ??= [];
    if (zone.length >= (tool() === 'door_line' ? 3 : 2)) return;
    zone.push(point);
  }
  renderControls();
}

function sampleColor(image, [x, y]) {
  const canvas = node('canvas');
  canvas.width = canvas.height = 5;
  const context = canvas.getContext('2d');
  const left = Math.max(0, Math.min(image.naturalWidth - 5, Math.round(x * image.naturalWidth) - 2));
  const top = Math.max(0, Math.min(image.naturalHeight - 5, Math.round(y * image.naturalHeight) - 2));
  context.drawImage(image, left, top, 5, 5, 0, 0, 5, 5);
  const pixels = context.getImageData(0, 0, 5, 5).data;
  const channels = [0, 1, 2].map(channel => {
    let sum = 0;
    for (let i = channel; i < pixels.length; i += 4) sum += pixels[i];
    return Math.round(sum / 25).toString(16).padStart(2, '0');
  });
  return `#${channels.join('')}`;
}

function svg(parent, kind, properties, content) {
  const shape = document.createElementNS('http://www.w3.org/2000/svg', kind);
  for (const [key, value] of Object.entries(properties)) shape.setAttribute(key, value);
  if (content) shape.textContent = content;
  parent.append(shape);
}

const isNormalized = point => Array.isArray(point) && point.every(value => value >= 0 && value <= 1);

function renderOverlays() {
  for (const camera of setup.cameras) {
    const { overlay, url } = cards.get(camera.id);
    overlay.style.display = url ? '' : 'none';
    overlay.replaceChildren();
    const landmarkMode = tool() === 'landmark';
    const next = setup.cameras.find(item => !landmark[item.id]);
    overlay.classList.toggle('draw', Boolean(url && (landmarkMode ? next.id === camera.id : zoneCamera()?.id === camera.id)));
    if (camera.walkout_enabled) {
      const zones = cameraZones(camera);
      for (const [key, [name, color]] of Object.entries(zoneNames)) {
        const editing = !landmarkMode && tool() === key && zone !== null && zoneCamera()?.id === camera.id;
        const raw = editing ? zone : zones[key];
        const points = key === 'exit_line' ? raw : rectanglePoints(raw);
        if (!points?.length) continue;
        const kind = key === 'exit_line' || points.length < 3 ? 'polyline' : 'polygon';
        svg(overlay, kind, {
          points: points.map(([x, y]) => `${x * 100},${y * 100}`).join(' '),
          fill: kind === 'polygon' ? `${color}24` : 'none', stroke: color, 'stroke-width': 2,
        });
        if (editing) {
          for (const [x, y] of points) svg(overlay, 'circle', { cx: x * 100, cy: y * 100, r: .65, fill: color });
        }
        svg(overlay, 'text', { x: points[0][0] * 100 + 1, y: Math.max(5, points[0][1] * 100 - 1),
          fill: color, 'font-size': 3.5 }, key === 'exit_line' ? `${name} A → B` : name);
        if (key === 'exit_line' && points.length === 2) {
          svg(overlay, 'text', { x: points[1][0] * 100 + 1, y: Math.max(5, points[1][1] * 100 - 1),
            fill: color, 'font-size': 3.5 }, 'B');
        }
      }
    }
    const editingDoor = doorCountingEnabled(camera) && tool() === 'door_line' &&
      zoneCamera()?.id === camera.id && zone !== null;
    const door = doorCountingEnabled(camera) ? (editingDoor ? zone : camera.door_line) : null;
    if (door?.length >= 2) {
      const [[ax, ay], [bx, by]] = door;
      svg(overlay, 'polyline', { points: `${ax * 100},${ay * 100} ${bx * 100},${by * 100}`,
        fill: 'none', stroke: '#81f4c1', 'stroke-width': 3, 'stroke-dasharray': '6 4' });
      if (!editingDoor) {
        const length = Math.hypot(bx - ax, by - ay);
        svg(overlay, 'text', { x: (ax + bx) * 50 - (by - ay) / length * 5,
          y: (ay + by) * 50 + (bx - ax) / length * 5, fill: '#81f4c1', 'font-size': 3.5 }, 'INSIDE');
      }
    }
    if (editingDoor) {
      for (const [x, y] of zone) {
        svg(overlay, 'circle', { cx: x * 100, cy: y * 100, r: .8, fill: '#81f4c1' });
      }
    }
    if (landmarkMode) {
      [...landmarks(), landmark].forEach((item, index) => {
        const point = item[camera.id];
        if (!isNormalized(point)) return;
        const [x, y] = point;
        svg(overlay, 'circle', { cx: x * 100, cy: y * 100, r: .85, fill: '#d1fa88', stroke: '#0b111b',
          'stroke-width': .25 });
        svg(overlay, 'text', { x: x * 100 + 1.1, y: Math.max(5, y * 100 - 1), fill: '#d1fa88',
          'font-size': 3.5 }, String(index + 1));
      });
    }
  }
}

function renderControls() {
  if (!setup) return;
  const idle = session.mode === 'idle';
  const preview = session.mode === 'preview';
  const editable = !busy && (idle || preview);
  const landmarkMode = tool() === 'landmark';
  const zoneTarget = zoneCamera();
  const active = activeCameras();
  const walkoutEnabled = active.some(camera => camera.walkout_enabled);
  document.querySelectorAll('[data-walkout]').forEach(field => { field.hidden = !walkoutEnabled; });
  $('marker-help').hidden = !active.some(camera => camera.employee_marker);
  $('floor-grid-field').hidden = active.length < 2;
  const framesReady = preview && Object.values(session.previews).some(status => status === 'Ready') && hasFrames;
  $('preview').disabled = $('scan').disabled = busy || !idle;
  $('preview').disabled ||= active.length === 0;
  const indexes = session.camera_indexes;
  $('scan-result').hidden = session.mode !== 'scanning' && !indexes;
  $('scan-result').textContent = session.mode === 'scanning' ? 'Checking camera indexes 0–5…' :
    indexes?.length ? `Available indexes: ${indexes.join(', ')}. Connect previews to identify each feed.` :
      'No cameras returned frames. Check Camera permissions and the iPhone connection.';
  $('stop').disabled = busy || idle || session.mode === 'stopping';
  $('add-camera').disabled = busy || !idle || setup.cameras.length >= 4;
  $('freeze').disabled = busy || !framesReady;
  $('freeze').textContent = frozen ? 'Resume previews' : 'Freeze previews';
  for (const [id, card] of cards) {
    const camera = setup.cameras.find(item => item.id === id);
    card.enabled.disabled = busy || !idle || (camera.enabled !== false && active.length === 1);
    card.marker.disabled = !editable || camera.enabled === false;
    card.source.disabled = card.mirror.disabled = busy || !idle;
    card.remove.disabled = busy || !idle || setup.cameras.length <= 1 || (camera.enabled !== false && active.length === 1);
    card.name.disabled = !editable;
    card.rules.disabled = !editable || zone !== null;
  }
  const apronMode = tool() === 'apron_color';
  const doorMode = tool() === 'door_line';
  $('zone-camera-field').hidden = landmarkMode;
  $('redraw').hidden = $('apply').hidden = landmarkMode || apronMode;
  $('redraw').textContent = doorMode ? 'Redraw door line' : 'Redraw zone';
  $('apply').textContent = doorMode ? 'Apply door line' : 'Apply zone';
  $('clear-feature').hidden = !apronMode && !doorMode;
  $('clear-feature').textContent = apronMode ? 'Clear apron color' : 'Clear door line';
  $('clear-feature').disabled = !editable || zone !== null ||
    !(apronMode ? zoneTarget?.apron_color : zoneTarget?.door_line);
  $('plane-field').hidden = $('clear-landmarks').hidden = !landmarkMode;
  $('tool').disabled = !editable || zone !== null || Object.keys(landmark).length > 0;
  $('zone-camera').disabled = !editable || zone !== null || !zoneTarget;
  $('redraw').disabled = !editable || !zoneTarget || session.previews[zoneTarget.id] !== 'Ready';
  $('undo').disabled = !editable || !(landmarkMode ? landmarks().length || Object.keys(landmark).length : zone !== null);
  $('apply').disabled = !editable || (zone?.length || 0) < (doorMode ? 3 : 2);
  $('clear-landmarks').disabled = !editable || (!setup.calibration && !Object.keys(landmark).length);
  for (const id of ['dining-time', 'pay-time', 'counter-wait-time', 'exit-time', 'plane', 'record', 'floor-grid', 'save', 'start']) {
    $(id).disabled = !editable;
  }
  const modes = { idle: 'Cameras stopped', preview: 'Setup previews running', monitoring: 'Monitoring is active',
    stopping: 'Releasing cameras…', scanning: 'Checking available cameras…' };
  $('mode').textContent = modes[session.mode];
  if (landmarkMode) {
    const next = setup.cameras.find(camera => !landmark[camera.id]);
    const plane = $('plane').value === 'floor' ? 'the floor' : 'a fixed plane at head height';
    $('geometry-help').textContent = `${landmarks().length} complete. Click point ${landmarks().length + 1} in ${next.name}, then match the same fixed point in the other feeds. Use at least four widely spaced points on ${plane}.`;
  } else if (doorMode && zoneTarget) {
    $('geometry-help').textContent = (zone?.length || 0) < 2 ?
      'Click both ends where the tracking dot will cross. Leave some visible space on both sides of the line.' :
      'Click once on the inside of the doorway to set the entry direction, then apply the line.';
  } else if (apronMode && zoneTarget) {
    const help = $('geometry-help');
    help.textContent = 'Click a solid part of the apron in the preview. Use an apron color that stands out in this room.';
    if (zoneTarget.apron_color) {
      const sample = node('span', '  ■');
      sample.style.color = zoneTarget.apron_color;
      sample.title = `Sampled apron color ${zoneTarget.apron_color}`;
      help.append(sample);
    }
  } else {
    $('geometry-help').textContent = zoneTarget ?
      (tool() === 'exit_line' ? 'Click the top endpoint first for an exit on the right. For an exit on the left, click the bottom endpoint first.' :
        'Click two opposite corners to draw a rectangle with 90-degree angles, then apply the zone.') :
      'Enable walk-out rules on a camera to draw its zones.';
  }
  renderOverlays();
}

async function refresh() {
  const latest = await api('/api/session');
  if (latest.error && latest.error !== session.error) message(latest.error, true);
  if (latest.mode !== 'preview' && (frozen || hasFrames)) {
    revision++;
    frozen = hasFrames = false;
    for (const card of cards.values()) clearFrame(card);
  }
  const redraw = session.mode !== latest.mode ||
    JSON.stringify(session.previews) !== JSON.stringify(latest.previews) ||
    String(session.camera_indexes) !== String(latest.camera_indexes);
  session = latest;
  for (const [id, card] of cards) {
    const camera = setup.cameras.find(item => item.id === id);
    card.status.textContent = camera.enabled === false ? 'Paused' : session.previews[id] || (session.mode === 'preview' ? 'Connecting…' : '');
  }
  if (redraw) renderControls();
}

async function frames() {
  if (session.mode !== 'preview' || frozen) return;
  const current = revision;
  await Promise.all([...cards].map(async ([id, card]) => {
    if (card.fetching || !['Ready', 'Adjusting exposure and focus'].includes(session.previews[id])) return;
    card.fetching = true;
    try {
      const response = await fetch(`/api/session/preview/${encodeURIComponent(id)}`, { cache: 'no-store' });
      if (!response.ok) return;
      const blob = await response.blob();
      if (current !== revision || frozen || session.mode !== 'preview') return;
      const previous = card.url;
      card.url = URL.createObjectURL(blob);
      card.image.src = card.url;
      card.image.hidden = false;
      card.empty.hidden = true;
      card.overlay.style.display = '';
      if (previous) URL.revokeObjectURL(previous);
      if (!hasFrames) {
        hasFrames = true;
        renderControls();
      }
    } finally {
      card.fetching = false;
    }
  }));
}

async function save() {
  validateDraft();
  for (const [id, property] of [
    ['dining-time', 'min_dwell_seconds'], ['pay-time', 'pay_dwell_seconds'], ['exit-time', 'lost_grace_seconds'],
    ['counter-wait-time', 'counter_wait_seconds'],
  ]) {
    if (!$(id).value || !$(id).checkValidity()) throw new Error('Enter positive dwell and grace times.');
    setup.walkout[property] = Number($(id).value);
  }
  setup.plane = $('plane').value;
  await api('/api/setup', 'PUT', setup);
}

$('tool').onchange = updateZonePicker;
$('zone-camera').onchange = renderControls;
$('plane').onchange = renderControls;
$('redraw').onclick = () => { zone = []; renderControls(); };
$('undo').onclick = () => {
  if (tool() === 'landmark') {
    const key = Object.keys(landmark).at(-1);
    if (key) delete landmark[key];
    else landmarks().pop();
    if (!landmarks().length) setup.calibration = null;
  } else {
    zone?.pop();
    if (!zone?.length) zone = null;
  }
  renderControls();
};
$('apply').onclick = () => action(async () => {
  const camera = zoneCamera();
  const key = tool();
  if (key === 'door_line') {
    const [a, b, p] = zone;
    const inside = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
    if (Math.abs(inside) < 0.0001) {
      throw new Error('Undo the last point, then click farther inside the doorway.');
    }
    for (const item of setup.cameras) item.door_line = null;
    camera.door_line = inside > 0 ? [a, b] : [b, a];
    zone = null;
    message('Door line applied. Save setup to keep it.');
    return;
  }
  if (key === 'exit_line') camera.zones.exit_band = null;
  if (key === 'exit_band') camera.zones.exit_line = null;
  const points = key === 'exit_line' ? zone : rectanglePoints(zone);
  if (key !== 'exit_line' && (points[0][0] === points[1][0] || points[0][1] === points[2][1])) {
    throw new Error('Choose two opposite corners with different horizontal and vertical positions.');
  }
  camera.zones[key] = points;
  zone = null;
  message('Zone applied. Save setup to keep your changes.');
});
$('clear-feature').onclick = () => {
  zoneCamera()[tool()] = null;
  renderControls();
};
$('clear-landmarks').onclick = () => { clearCalibration(); renderControls(); };
$('freeze').onclick = () => { frozen = !frozen; revision++; renderControls(); };
$('add-camera').onclick = () => {
  let index = 0;
  while (setup.cameras.some(camera => String(camera.source) === String(index))) index++;
  setup.cameras.push({
    id: `camera-${crypto.randomUUID().slice(0, 8)}`, source: index, name: `Camera ${index}`,
    mirror: true, walkout_enabled: false, zones: {},
  });
  clearCalibration();
  renderCameras();
};
$('scan').onclick = () => action(async () => {
  await api('/api/session/scan', 'POST');
  await refresh();
});
$('preview').onclick = () => action(async () => {
  await api('/api/session/preview', 'POST', { cameras: setup.cameras });
  await refresh();
});
$('stop').onclick = () => action(async () => {
  await api('/api/session/stop', 'POST');
  await refresh();
});
$('save').onclick = () => action(async () => {
  await save();
  message('Setup saved.');
});
$('start').onclick = () => action(async () => {
  await save();
  if (session.mode === 'preview') {
    await api('/api/session/stop', 'POST');
    for (let i = 0; i < 60; i++) {
      await refresh();
      if (session.mode === 'idle') break;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  await api('/api/session/start', 'POST', { record: $('record').checked, floor_grid: $('floor-grid').checked });
  location.assign('/');
});

// WhatsApp settings can be changed while monitoring; delivery reloads them for each alert.
let whatsappBusy = false;
function whatsappMessage(text, error = false) {
  $('whatsapp-status').textContent = text;
  $('whatsapp-status').dataset.error = error;
}
function showWhatsApp(settings) {
  $('whatsapp-enabled').checked = settings.enabled;
  for (const [id, field] of [
    ['account', 'account_sid'], ['sender', 'sender'], ['recipient', 'recipient'], ['template', 'content_sid'],
  ]) $('whatsapp-' + id).value = settings[field];
  $('whatsapp-token').value = '';
  $('whatsapp-token').placeholder = settings.has_auth_token ? 'Saved — leave blank to keep it' : 'Enter your Auth Token';
  whatsappMessage(settings.enabled && settings.configured ? 'WhatsApp alerts enabled for new alerts.' :
    settings.configured ? 'Settings saved. Automatic WhatsApp alerts are off.' : 'Add your Twilio details to connect WhatsApp.');
}
async function saveWhatsApp(test = false) {
  if (whatsappBusy || !$('whatsapp-form').reportValidity()) return;
  whatsappBusy = true;
  const controls = $('whatsapp-form').querySelectorAll('input, button');
  controls.forEach(control => { control.disabled = true; });
  whatsappMessage(test ? 'Saving settings and sending a test…' : 'Saving WhatsApp settings…');
  try {
    const settings = await api('/api/whatsapp', 'PUT', {
      enabled: $('whatsapp-enabled').checked,
      account_sid: $('whatsapp-account').value.trim(), auth_token: $('whatsapp-token').value.trim(),
      sender: $('whatsapp-sender').value.trim(), recipient: $('whatsapp-recipient').value.trim(),
      content_sid: $('whatsapp-template').value.trim(),
    });
    showWhatsApp(settings);
    if (test) {
      const result = await api('/api/whatsapp/test', 'POST');
      whatsappMessage(`Test accepted by Twilio (${result.status}). Check WhatsApp; if it doesn't arrive, check Twilio Messaging logs.`);
    }
  } catch (error) {
    whatsappMessage(error.message, true);
  } finally {
    whatsappBusy = false;
    controls.forEach(control => { control.disabled = false; });
  }
}
$('whatsapp-form').onsubmit = event => { event.preventDefault(); saveWhatsApp(); };
$('whatsapp-test').onclick = () => saveWhatsApp(true);

(async () => {
  try {
    setup = await api('/api/setup');
    $('plane').value = setup.plane;
    $('dining-time').value = setup.walkout.min_dwell_seconds;
    $('pay-time').value = setup.walkout.pay_dwell_seconds;
    $('counter-wait-time').value = setup.walkout.counter_wait_seconds;
    $('exit-time').value = setup.walkout.lost_grace_seconds;
    renderCameras();
    try {
      showWhatsApp(await api('/api/whatsapp'));
    } catch (error) {
      whatsappMessage(error.message, true);
    }
    await refresh();
    setInterval(() => refresh().catch(error => message(error.message, true)), 600);
    setInterval(() => frames().catch(error => message(error.message, true)), 220);
  } catch (error) {
    message(error.message, true);
    document.querySelectorAll('button, input, select').forEach(control => { control.disabled = true; });
  }
})();
