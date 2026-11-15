# Surveillance.ai

Surveillance.ai is a camera monitoring application for restaurants. It brings live video, guest tracking, occupancy counts, payment-status controls, and alerts into one browser dashboard.

The application processes camera footage on the computer connected to the cameras. Staff use the dashboard to monitor activity, confirm payments, and review situations that may need attention.

1. Live camera dashboard

   We built a dark dashboard with the camera feed as its main focus. It displays tracking boxes, guest IDs, guest states, and the configured dining, counter, and exit areas directly on the footage.

   The dashboard includes live camera status, FPS information, prominent summary counts, expandable alert cards, an activity table, and a Guests in view panel. The layout adjusts for smaller screens.
2. Guest detection and tracking

   The system uses YOLO person detection and tracking logic to follow people through the camera view. Each tracked guest receives a temporary ID, such as Guest #7, which connects their camera label, guest card, and alerts.

   Tracking considers movement, position, and clothing appearance to maintain continuity when people move or briefly overlap. IDs belong to the tracking session; they are not permanent customer identities.

   Guests move through these states:

   | State | Meaning |
   |---|---|
   | NEW | A newly tracked guest |
   | DINING | The guest has spent the configured time in the dining zone |
   | PAID | Staff confirmed payment, or the configured counter-dwell rule marked them paid |
   | EXITED | The visit has ended |
   | EMPLOYEE | The tracked person has been classified as an employee |

3. Configurable dining, counter, and exit rules

   The browser setup page lets users draw zones over a camera preview. Dining, counter, and exit zones are rectangles with 90-degree corners. An exit line is available as an alternative to an exit rectangle.

   Users can adjust how long someone must remain in a zone before a rule applies. The system uses these locations and timers to interpret a guest’s visit.

   Payment status currently comes from staff confirmation or a counter-dwell assumption. Verifying an actual transaction would require a connection to a payment or point-of-sale system.
4. Individual payment confirmation

   Each eligible guest card has a Has paid button. Clicking it submits a payment update for that specific guest and changes their state to PAID in the camera overlay.

   The button shows an updating state while the tracking process applies the change, then displays Paid. Other guests remain unchanged. We also retained the group action for marking dining guests paid.

   Guest cards use simple icons, following your preference to avoid real guest thumbnails.
5. New-customer counter-wait alerts

   When a NEW customer stays in the counter zone for the configured duration, the system creates a counter-wait alert. We set the standard threshold to 5 seconds, and users can change it in Camera setup.

   This can draw attention to someone waiting to order or receive assistance. The alert appears in the dashboard and activity log, and it can trigger sound and WhatsApp notifications.

   Counter-wait alerts do not create a saved photograph or video clip for that alert.
6. Possible walk-out detection and evidence

   The system can flag a guest who qualified as dining, was observed at an exit, and then leaves without a payment confirmation.

   It includes grace periods, cooldowns, and checks for camera coverage to reduce alerts caused by brief tracking loss. With multiple cameras, it considers whether the guest remains visible in another feed.

   A possible walk-out can generate:
   - An alert containing the guest ID and time.
   - A selected person snapshot from recent footage.
   - A short clip covering footage around the event.
   - A face crop when a suitable face is visible.

   Face crops support human review. Their availability depends on image quality, angle, and visibility. Alerts are labeled possible walk-outs because staff still need to verify what happened.
7. Employee identification

   We added two ways to classify employees: an RGB marker displayed on a phone and a sampled apron color.

   Employees receive employee labels and are excluded from guest dining, payment, and walk-out rules while classified as employees. Their cards do not show guest payment controls.

   We also implemented your door-overlay preference: enabling both Walk-out rules and RGB employee marker hides the door line visually while preserving its saved counting geometry.
8. Doorway counts and persistent activity logging

   A configurable doorway line records crossings and determines entry or exit direction. Summary cards display:

   | Count | What it represents |
   |---|---|
   | Inside | Estimated occupancy from recorded entries minus exits |
   | Entered | Recorded entries during the session |
   | Exited | Recorded exits during the session |
   | Staff in view | Currently visible tracked employees |

   The activity log records timestamped entries, exits, and alerts, including guest or employee IDs and the counts associated with a crossing.

   Activity is saved locally and restored when the dashboard starts again. Guest tracking and session counters have their own session lifecycle.
9. Sound and WhatsApp notifications

   Browser alert sounds include Enable sound, Mute, and Test sound controls. New alerts can play a chime; replaying saved history does not repeatedly trigger sounds.

   We added a WhatsApp setup section using Twilio. Users can enter their account credentials and sender/recipient numbers, enable automatic notifications, and send a test message.

   New counter-wait and possible walk-out alerts can send text containing the guest ID, alert message, and timestamp. Photos and clips stay local. The integration supports an approved template Content SID and displays useful errors when a test cannot be accepted.

   Credentials are stored in the local ignored .env file. Real WhatsApp delivery requires the user’s Twilio setup and an internet connection.
10. Multiple cameras, setup tools, and distribution

The application supports one to four camera inputs, along with replaying local video files. Optional shared-landmark calibration enables a floor map and coordinated tracking across camera views.

Camera setup includes camera discovery, previews, mirroring, enable/disable controls, zone drawing, employee settings, timing controls, and optional recording.

We added Windows setup and launch files, a macOS launcher, manual installation instructions, and troubleshooting documentation. Your current working camera layout is included as walkout.multi.example.yaml. Launchers copy it into a local configuration when needed, and the README explains how another user can reconfigure it.

The main technology stack is:

| Component | Technology |
|---|---|
| Application and monitoring logic | Python |
| Person detection | Ultralytics YOLO and PyTorch |
| Video processing and image analysis | OpenCV and NumPy |
| Tracking | ByteTrack plus additional identity and multi-camera logic |
| Local web server and API | FastAPI and Uvicorn |
| Dashboard and setup interface | HTML, CSS, and JavaScript |
| Configuration | YAML |
| Activity and alert records | Local JSONL files |
| WhatsApp delivery | Twilio through HTTP requests |
| Verification | Pytest, Node.js tests, and Ruff |

## Install and run on Windows

Use Python **3.11** with the Python launcher (`py`) installed. Internet access is needed to install dependencies and download YOLO weights on the first monitoring run. Node.js is optional and only needed for the static demo and JavaScript tests.

1. Clone this repository or extract its ZIP, then open the project folder.
2. Double-click **Setup Surveillance.bat**. It creates `.venv`, installs dependencies, and runs a synthetic check.
3. Double-click **Open Surveillance.bat**. It copies the example camera setup if no local setup exists and opens the dashboard.
4. Choose **Camera setup**, connect previews, and adjust the configuration for your cameras before starting monitoring.

Keep the launcher window open while the app runs. Use **Stop cameras** to release the cameras and Ctrl+C in the launcher to stop the server.

PowerShell installation alternative, from the project folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item walkout.multi.example.yaml walkout.multi.yaml
.\.venv\Scripts\python.exe -m survai.main --web
```

Run the copy command only on the first setup: it replaces the destination if it already exists. The normal launchers preserve an existing local configuration.

## Install and run on macOS or Linux

Use Python 3.11 and run these commands from the project folder:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n walkout.multi.example.yaml walkout.multi.yaml
.venv/bin/python -m survai.main --web
```

On macOS, after installation you can also run `zsh "Open Surveillance.command"`. Allow your terminal or IDE to use the camera when the operating system requests it. Python 3.12 and 3.13 are permitted by the package metadata, but the Windows setup script specifically selects 3.11.

## The included example and how to reconfigure it

[`walkout.multi.example.yaml`](walkout.multi.example.yaml) is a snapshot of the project's current working camera layout. It includes:

| Setting | Example value |
| --- | --- |
| Camera | One enabled camera, named **Cam**, source index `0` |
| Mirroring | Enabled |
| Walk-out rules | Enabled |
| RGB employee marker | Enabled |
| Dining zone | Most of the left half of the image |
| Counter zone | Lower-right area |
| Exit zone and saved door line | Near the right edge |
| Shared-floor calibration | None |

These coordinates fit the original camera view. Redraw them for your camera position and room. Both **Walk-out rules** and **RGB employee marker** are enabled in this example, which hides the door-line overlay; saved door geometry can still be used for counting. To see and edit the door line, temporarily uncheck either setting, then restore the combination you want.

Make changes in **Camera setup**:

1. **Stop monitoring first.** Camera and geometry settings can be edited while stopped or while setup previews are running.
2. Use **Find cameras**, then change the **Camera index or video path** for each input. Camera `0` is only an example; other computers may use different indices. Enable the cameras you want, set their names, and check **Mirror** against the actual preview. Every enabled live camera needs a different index.
3. Click **Connect previews**. Wait for a usable image. You can freeze the preview while drawing.
4. Select **Dining zone**, **Counter zone**, or **Exit zone**, choose the camera, click **Redraw zone**, and click two opposite corners. Click **Apply zone**. Rectangles have 90-degree angles. Use **Exit line** instead if you want a line rather than an exit rectangle.
5. To count doorway crossings, select **Door counting line**, click its two endpoints, then click inside the store to set the entry direction. Apply it. Use one doorway-counting camera.
6. Turn **Walk-out rules** and **RGB employee marker** on or off as appropriate. When marker detection is enabled, download the marker from the page and display it as instructed. **Employee apron color** is an alternative that you can sample from the preview.
7. Adjust **Dining dwell**, **Counter payment dwell**, **New customer wait**, and **Exit grace**. This example inherits the demo defaults: 3 seconds dining, 2 seconds counter payment, 5 seconds new-customer wait, and 1.5 seconds exit grace. Change these to suit your operation.
8. For multiple cameras, add each input and optionally calibrate **Shared landmarks** if you need a shared floor map. Changing camera source or mirroring can require redrawing calibration.
9. Click **Save setup**, then **Save and start monitoring** when ready.

Browser changes are saved to **`walkout.multi.yaml`**, which is ignored by Git. They do not modify the published example. To reset, stop the app, back up your local YAML, then copy `walkout.multi.example.yaml` over it. Edit the example intentionally if you want to publish a different default.

The visible **Counter** zone is stored internally as `pay_zone`. The root `config.yaml` belongs to older rule tests; use Camera setup or `walkout.multi.yaml` for the current app.

## Alerts and optional WhatsApp

Enable sound in the dashboard to hear new alerts. **Has paid** confirms the selected guest's payment; employees do not have guest payment states. A possible walk-out is a prompt to review the evidence and verify payment.

Counter-wait alerts create log entries without saving a photo or clip for that alert. Walk-out alerts can save a snapshot, clip, and best-effort face crop when available. Guest cards use icons. Evidence is stored under `alerts/`, and full-feed recordings are optional under `recordings/`.

For WhatsApp, open **Camera setup → WhatsApp notifications** and follow [`WHATSAPP_SETUP.md`](WHATSAPP_SETUP.md). Enter your own Twilio account details locally, enable alerts, and send a test. Credentials are not included in the repository. The computer needs to remain running and online for notifications.

## Checks and troubleshooting

Windows commands:

```powershell
.\.venv\Scripts\python.exe -m survai.main --selftest
.\.venv\Scripts\python.exe -m survai.main --selftest-multi
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
node --test tests/test_alert_sound.cjs tests/test_guest_payment.cjs tests/test_whatsapp.cjs
```

On macOS/Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`. The synthetic checks do not require a camera or downloaded detection weights.

One existing retention test creates a symbolic link. On Windows without symlink privileges it fails with `WinError 1314`. You can run the remaining tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -k "not test_evidence_retention_scopes_cleanup_and_updates_history"
```

- **Camera will not open:** close other apps using it, check OS camera permissions, stop the current session, and use **Find cameras** to confirm the index.
- **Missing/incomplete dependencies:** rerun **Setup Surveillance.bat**, or reinstall using the virtual environment's Python and `requirements.txt`.
- **Port 8765 is busy:** use `python -m survai.main --web --port 8766` with the appropriate virtual-environment Python.
- **No weights available offline:** download weights first or supply a local model with `--weights path/to/yolov8n.pt`. Weight downloads are excluded from Git.

## Static demonstration

```sh
node scripts/build-dashboard.mjs
```

This generates `dist/index.html` with simulated guests and alerts. `vercel.json` selects this static build. The live Python camera server is a separate process; publishing the static demo does not start it.
