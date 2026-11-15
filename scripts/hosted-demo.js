(() => {
  const visits = new Map();
  const alerts = new Set();
  let dashboard;
  let nextId = 1;
  const canvas = document.createElement('canvas');
  canvas.id = 'demo-canvas';
  canvas.width = 720;
  canvas.height = 405;
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Simulated dining, payment, and exit zones');
  const view = canvas.getContext('2d');

  function render() {
    view.fillStyle = '#0b1220';
    view.fillRect(0, 0, 720, 405);
    view.font = '16px monospace';
    for (const [name, x, y, w, h, color] of [
      ['DINING', 38, 58, 380, 296, '#90d3ff'],
      ['COUNTER', 445, 215, 158, 139, '#81f4c1'],
      ['EXIT', 632, 28, 66, 327, '#ffc781'],
    ]) {
      view.strokeStyle = view.fillStyle = color;
      view.strokeRect(x, y, w, h);
      view.fillText(name, x + 9, y + 23);
    }
    for (const [index, guest] of [...visits.values()].entries()) {
      const paid = guest.state === 'PAID';
      const x = paid ? 468 : 70 + (index % 3) * 105;
      const y = paid ? 245 : 105 + Math.floor(index / 3) * 100;
      view.strokeStyle = view.fillStyle = guest.state === 'PAID' ? '#81f4c1' : '#90d3ff';
      view.strokeRect(x, y, 68, 76);
      view.fillText(`#${guest.track_id} ${guest.state}`, x, y + 95);
    }
    dashboard.renderVisits([...visits.values()]);
    dashboard.status.textContent = `${alerts.size} ${alerts.size === 1 ? 'alert' : 'alerts'}`;
    dashboard.status.className = alerts.size ? 'alert' : 'clear';
    for (const button of document.querySelectorAll('[data-visit-action]')) {
      const guest = visits.values().next().value;
      button.disabled = !guest || (button.dataset.visitAction === 'pay' && guest.state === 'PAID');
    }
    document.querySelector('#demo-add').disabled = visits.size >= 6;
  }

  function simulatedPhoto(id) {
    const photo = document.createElement('canvas');
    photo.width = 116;
    photo.height = 144;
    const image = photo.getContext('2d');
    image.fillStyle = '#16263b';
    image.fillRect(0, 0, 116, 144);
    image.fillStyle = '#81f4c1';
    image.beginPath();
    image.arc(58, 49, 19, 0, Math.PI * 2);
    image.fill();
    image.beginPath();
    image.arc(58, 112, 34, Math.PI, Math.PI * 2);
    image.fill();
    image.font = '12px monospace';
    image.fillText(`DEMO #${id}`, 28, 131);
    return photo.toDataURL('image/png');
  }

  window.deleteHostedAlert = id => {
    alerts.delete(id);
    dashboard.removeAlert(id);
    render();
  };

  window.markHostedGuestPaid = id => {
    const guest = visits.get(id);
    if (guest) guest.state = 'PAID';
    render();
  };

  window.startHostedDemo = controls => {
    dashboard = controls;
    dashboard.cameraStatus.textContent = 'Simulated feed';
    document.querySelector('#feed').replaceWith(canvas);
    const actions = document.createElement('div');
    actions.className = 'demo-actions';
    document.querySelector('.viewport').append(actions);

    function addButton(label, onClick, action) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      if (action) button.dataset.visitAction = action;
      else button.id = 'demo-add';
      button.addEventListener('click', onClick);
      actions.append(button);
    }

    addButton('Add guest', () => {
      const track_id = nextId++;
      const first_seen = new Date().toISOString();
      visits.set(track_id, {
        track_id, state: 'DINING', first_seen, visible: true, earlierPhoto: simulatedPhoto(track_id),
      });
      render();
    });
    addButton('Mark first guest paid', () => {
      const guest = visits.values().next().value;
      guest.state = 'PAID';
      render();
    }, 'pay');
    addButton('First guest exits', () => {
      const guest = visits.values().next().value;
      visits.delete(guest.track_id);
      if (guest.state !== 'PAID') {
        const id = `demo-${guest.track_id}`;
        alerts.add(id);
        dashboard.displayAlert({
          id, type: 'possible_walkout', severity: 'warning', timestamp: new Date().toISOString(),
          snapshot_path: guest.earlierPhoto,
          message: `Simulated guest #${guest.track_id} exited without a payment confirmation.`,
        });
      }
      render();
    }, 'exit');
    render();
  };
})();
