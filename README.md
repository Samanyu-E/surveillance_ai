
# Surveillance.ai

**Surveillance.ai** is a camera-monitoring application for restaurants. It combines live video, guest tracking, estimated occupancy, payment-status controls, and alerts in a browser-based dashboard.

Video processing runs locally on the computer connected to the cameras. Staff can use the dashboard to monitor activity, mark guests as paid, and review events that may need attention.

> **Important:** A **PAID** status is based on staff confirmation or a configured counter-dwell rule; it does **not** verify a transaction with a point-of-sale (POS) system. Likewise, a **possible walk-out** alert is a prompt for staff review, not proof that someone left without paying.

## Features

### Live dashboard

- Live camera feeds with person-tracking boxes, temporary IDs, and guest states.
- Dining, counter, and exit zones displayed over the footage.
- Camera connection status and frames-per-second (FPS) information.
- Occupancy summaries, expandable alerts, an activity table, and a **Guests in view** panel.
- A responsive dark interface for different screen sizes.

### Guest detection and tracking

Ultralytics YOLO detects people, while ByteTrack and additional tracking logic help maintain guest identities across movement and brief overlaps. Tracking considers position, movement, and clothing appearance.

Each person receives a temporary session ID (for example, `Guest #7`) that links the camera overlay, guest card, and relevant alerts. **IDs are not permanent customer identities.**

| State | Meaning |
| --- | --- |
| `NEW` | A newly tracked guest |
| `DINING` | The guest has remained in the dining zone for the configured duration |
| `PAID` | Staff marked the guest as paid, or a configured counter-dwell rule applied |
| `EXITED` | The tracked visit ended |
| `EMPLOYEE` | The person has been classified as an employee |

### Configurable zones and payment controls

Use **Camera setup** to draw rectangular **Dining**, **Counter**, and **Exit** zones on a camera preview. An exit line can be used instead of an exit rectangle. Zone dwell times and exit grace periods are configurable.

Each eligible guest card includes a **Has paid** button that updates only that guest. The button shows an updating state until the change is applied, then displays **Paid**. A separate group action can mark dining guests as paid. Guest cards use icons rather than guest photographs.

The **Counter** zone is stored internally as `pay_zone`. Its payment-dwell rule is an assumption, not POS verification.

### Counter-wait and possible walk-out alerts

**Counter-wait alert:** When a `NEW` guest remains in the counter zone beyond the configured threshold (5 seconds by default), the system creates an alert that can appear on the dashboard, enter the activity log, and trigger sound or WhatsApp notifications. This alert does **not** save a photo or clip.

**Possible walk-out alert:** The system can flag a guest who met the dining requirement, was observed at an exit, and then left without a recorded payment confirmation. Grace periods, cooldowns, and camera-coverage checks help reduce false alerts. With multiple cameras, the system also checks whether the guest is still visible in another feed.

Possible walk-out evidence can include:

- A guest ID and event timestamp.
- A selected snapshot from recent footage.
- A short video clip around the event.
- A best-effort face crop, when the image angle and quality allow it.

Saved evidence supports **human review**. A missing payment confirmation or tracking error does not establish that a guest failed to pay.

### Employee identification

Employees can be classified using either an **RGB marker** displayed on a phone or a **sampled apron color**. While classified as employees, they have employee labels, do not show guest payment controls, and are excluded from guest dining, payment, and walk-out rules.

When both **Walk-out rules** and **RGB employee marker** are enabled, the door-counting line is hidden from the video overlay. Its saved counting geometry remains available.

### Doorway counts and activity history

A configurable doorway line identifies entry and exit crossings. Dashboard summary cards include:

| Metric | Description |
| --- | --- |
| **Inside** | Estimated occupancy based on recorded entries minus exits |
| **Entered** | Entries recorded during the session |
| **Exited** | Exits recorded during the session |
| **Staff in view** | Tracked employees currently visible |

Timestamped entries, exits, and alerts are written to a local activity log. Crossing records include relevant guest or employee IDs and counts. Activity history is restored when the dashboard restarts; guest tracking and session counters have separate lifecycles.

### Notifications and multiple cameras

- **Sound:** Enable, mute, or test alert chimes. Reloading saved alert history does not replay notification sounds.
- **WhatsApp:** Send new counter-wait and possible walk-out notifications through Twilio, including the guest ID, message, and timestamp. Photos and clips stay local.
- **Camera inputs:** Use **1–4 cameras** or replay local video files.
- **Multi-camera calibration:** Optionally define shared landmarks for a floor map and coordinated tracking between views.
- **Camera configuration:** Discover cameras, preview feeds, mirror images, enable or disable sources, draw zones, configure employee detection and timers, and enable optional recording.

## Technology stack

| Component | Technology |
| --- | --- |
| Application and monitoring logic | Python |
| Person detection | Ultralytics YOLO, PyTorch |
| Image and video processing | OpenCV, NumPy |
| Tracking | ByteTrack and additional identity/multi-camera logic |
| Local web server and API | FastAPI, Uvicorn |
| Dashboard and setup UI | HTML, CSS, JavaScript |
| Configuration | YAML |
| Activity and alert records | Local JSONL files |
| WhatsApp notifications | Twilio via HTTP requests |
| Tests and linting | Pytest, Node.js tests, Ruff |

## Installation

### Requirements

- **Python 3.11** (recommended for both supported setup paths).
- A camera connected to the computer, or a local video file for replay.
- Internet access for dependency installation and the initial download of YOLO weights.
- **Node.js (optional):** Required only for the static demo build and JavaScript tests.

### Windows: recommended setup

1. Clone this repository or download and extract its ZIP file.
2. Open the project folder and double-click **`Setup Surveillance.bat`**. This creates `.venv`, installs dependencies, and runs a synthetic check.
3. Double-click **`Open Surveillance.bat`**. It creates a local camera configuration from the example **only if one does not already exist**, then opens the dashboard.
4. Open **Camera setup**, connect the camera previews, adjust the zones and settings for your environment, and start monitoring.

Keep the launcher window open while using the app. Use **Stop cameras** to release camera devices, and press **Ctrl+C** in the launcher window to stop the server.

**Windows: manual PowerShell setup**

Run these commands from the project directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path walkout.multi.yaml)) {
    Copy-Item walkout.multi.example.yaml walkout.multi.yaml
}
.\.venv\Scripts\python.exe -m survai.main --web
```

The conditional copy preserves an existing `walkout.multi.yaml` file.

### macOS or Linux

Run these commands from the project directory:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n walkout.multi.example.yaml walkout.multi.yaml
.venv/bin/python -m survai.main --web
```

On macOS, after installation you can alternatively launch with:

```sh
zsh "Open Surveillance.command"
```

Grant camera permissions when prompted by the operating system. The package metadata also permits Python 3.12 and 3.13, but the Windows setup script specifically uses Python 3.11.

## Camera setup and configuration

The repository includes [`walkout.multi.example.yaml`](walkout.multi.example.yaml), a sample configuration based on the project's working camera layout.

| Setting | Included example |
| --- | --- |
| Camera | One enabled camera named **Cam**, source index `0` |
| Mirroring | Enabled |
| Walk-out rules | Enabled |
| RGB employee marker | Enabled |
| Dining zone | Most of the left half of the camera image |
| Counter zone | Lower-right area |
| Exit zone and saved door line | Near the right edge |
| Shared-floor calibration | None |

**These zones are specific to the original camera position.** Redraw them before using a different camera or physical layout.

### Configure your cameras

1. **Stop monitoring** before editing camera and geometry settings. Setup previews may remain active.
2. In **Camera setup**, click **Find cameras**. Set each **Camera index or video path**, choose a name, enable the desired inputs, and check **Mirror** against the preview. Each enabled live camera needs a distinct index.
3. Click **Connect previews**. Once the image appears, you can freeze it while drawing zones.
4. Choose **Dining zone**, **Counter zone**, or **Exit zone** and select the relevant camera. Click **Redraw zone**, choose two opposite corners, then click **Apply zone**. Use **Exit line** if a line is more appropriate than a rectangle.
5. To record doorway counts, select **Door counting line**, click its endpoints, and click inside the store to indicate the entry direction. Apply the line. Use one doorway-counting camera.
6. Configure **Walk-out rules** and employee identification. For RGB markers, download the marker in the setup page and display it as instructed. Alternatively, sample an **Employee apron color** from the preview.
7. Adjust the timing rules. The included example inherits these demo defaults:

   | Rule | Default |
   | --- | --- |
   | Dining dwell | 3 seconds |
   | Counter payment dwell | 2 seconds |
   | New customer wait | 5 seconds |
   | Exit grace | 1.5 seconds |

8. For multiple cameras, add the additional inputs. If you need a shared floor map, configure **Shared landmarks**. Changing a camera source or mirroring may require recalibration.
9. Click **Save setup**, then **Save and start monitoring**.

**Door-line visibility:** If both **Walk-out rules** and **RGB employee marker** are on, the door line is visually hidden. Temporarily disable either option to view or edit the line, then restore the desired settings.

### Where settings are saved

Changes made in the browser are saved to `walkout.multi.yaml`, which is ignored by Git. They do not modify `walkout.multi.example.yaml`.

To reset the configuration, stop the application, back up your current YAML file, and replace it with a copy of the example. Only edit the example itself when you intentionally want to publish different defaults.

The root `config.yaml` is used by older rule tests. Use **Camera setup** or `walkout.multi.yaml` for the current application.

## Alerts, recordings, and WhatsApp

The dashboard can play a sound for new alerts after **Enable sound** is selected. Payment updates are guest-specific; employee cards have no guest payment controls.

| Event | Activity log | Local media evidence | Optional notification |
| --- | --- | --- | --- |
| Counter-wait alert | Yes | No photo or clip | Sound and WhatsApp |
| Possible walk-out | Yes | Snapshot, clip, and optional face crop | Sound and WhatsApp |

Evidence is stored locally under `alerts/`. Optional full-feed recordings are stored under `recordings/`.

To set up WhatsApp:

1. Open **Camera setup → WhatsApp notifications**.
2. Follow [`WHATSAPP_SETUP.md`](WHATSAPP_SETUP.md) to configure your own Twilio credentials and sender/recipient numbers.
3. Enable automatic notifications and send a test message.

The integration supports an approved Twilio template **Content SID** and reports test-message errors. Credentials are stored in a local, Git-ignored `.env` file; they are not supplied with the repository. The computer must be running and connected to the internet for WhatsApp delivery.

Because the application can save images and videos of people, limit access to its local evidence and recordings and follow applicable privacy and camera-notice requirements.

## Testing and troubleshooting

### Run checks on Windows

```powershell
.\.venv\Scripts\python.exe -m survai.main --selftest
.\.venv\Scripts\python.exe -m survai.main --selftest-multi
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
node --test tests/test_alert_sound.cjs tests/test_guest_payment.cjs tests/test_whatsapp.cjs
```

On macOS and Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`. The synthetic self-tests do not need a camera or downloaded model weights. The Node.js command requires Node.js to be installed.

**Windows symlink permission issue:** One evidence-retention test creates a symbolic link and may fail with `WinError 1314` on a Windows system without symlink privileges. To run the other tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -k "not test_evidence_retention_scopes_cleanup_and_updates_history"
```

### Common issues

| Problem | What to try |
| --- | --- |
| Camera does not open | Close other apps using it, check OS camera permissions, stop the current session, and use **Find cameras** to verify the source index. |
| Dependencies are missing | Run **`Setup Surveillance.bat`** again or install `requirements.txt` with the virtual environment's Python. |
| Port `8765` is occupied | Start the server with a different port, such as `.\.venv\Scripts\python.exe -m survai.main --web --port 8766` on Windows. |
| Model weights are unavailable offline | Download the model weights while online or pass a local model with `--weights path/to/yolov8n.pt`. Model downloads are not tracked in Git. |

## Static demo

The repository also contains a **static dashboard demo** with simulated guest activity and alerts. Build it with Node.js:

```sh
node scripts/build-dashboard.mjs
```

This creates `dist/index.html`. The `vercel.json` configuration selects this static build for deployment.

**The static demo is not the live monitoring application.** Deploying it does not start the Python camera-processing server; the live application must run separately.
