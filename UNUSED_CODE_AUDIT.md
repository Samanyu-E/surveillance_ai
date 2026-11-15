Unused-code review — November 14, 2026

Reviewed `C:\hackathon\serv-main\ServeillanceAi`: all 39 Python source modules, 16 Python test files, the dashboard and setup HTML/JavaScript, the hosted demo/build scripts, launchers, configuration, package metadata, and requirements. This is a repository-wide static review of references and startup paths, including optional CLI modes. External programs calling this project's Python interfaces or HTTP endpoints cannot be ruled out by searching this repository.

No application code, dependencies, evidence, or recordings were removed. This report is the only file added by this review.

**Confirmed unused by the application's current entry points**

| Item | Location | Evidence and cleanup implications |
| --- | --- | --- |
| Old unattended-bag detector | `src/survai/detectors/bag.py:17` | `BagDetector` is instantiated only in `tests/test_rules.py`. Its helper `Bag` belongs to the same unused implementation. No CLI or web runtime imports this module. |
| Old crowd detector | `src/survai/detectors/crowd.py:5` | `CrowdDetector` is instantiated only in `tests/test_rules.py`. No application entry point imports it. |
| Old exit/payment detector | `src/survai/detectors/exit.py:17` | `ExitDetector` and its helper `Visit` are used only by the old rule tests. The active implementation is `detectors/walkout.py`, together with `multi_visit.py`; those must stay. |
| Old queue/wait detector | `src/survai/detectors/queue.py:7` | `QueueDetector` is instantiated only in `tests/test_rules.py`. Current counter-wait alerts run through the active walk-out/room-visit code instead. |
| Old detector protocol | `src/survai/detectors/__init__.py:7` | No code imports or annotates with `Detector`. Remove the protocol if cleaning up, while retaining the package's `__init__.py` file. |
| Old configuration model and loader | `src/survai/config.py:11`, `:41`; root `config.yaml` | `load_config()` is called only by `tests/test_rules.py`. Normal startup loads `WalkoutConfig` and `MultiCameraConfig`, not this root YAML file. Legacy fields include `queue_count`, `crowd_count`, bag settings, `cooldown_seconds`, `snapshot_days`, and `summary_model`. `Config` is still mentioned in the unused overlay and unused Vision constructor option below, so remove those references together. Retain the other models and `save_config()`. |
| Legacy Vision configuration option | `src/survai/vision.py:20` | No repository caller supplies the `config` argument. All callers use `weights`/`image_size` or their defaults. The `config.weights`, `config.image_size`, and bag-class selection `[0, 24, 26, 28]` branches therefore do not run. Keep `Vision` itself: single-camera preview and diagnostics use it, and `MultiVision` inherits from it. |
| Old camera overlay | `src/survai/vision.py:65` | `annotate()` has no callers, including tests. Active camera overlays use `hud.annotate_walkout()`. |
| Old intermediate alert type | `src/survai/events.py:10` | `Signal` is referenced only by the unused detectors/protocol, `EventEngine`, and old rule tests. The separate `Event` dataclass in this file is actively used throughout the app and must stay. |
| Old alert cooldown engine | `src/survai/events.py:35` | `EventEngine.emit()` is called only in `tests/test_rules.py`. Current walk-out/counter logic handles its own alert transitions. |
| Superseded snapshot cleanup | `src/survai/alerts/__init__.py:69` | `expire_snapshots()` has no callers, including tests. Active retention uses `evidence.expire_evidence()` and `State.expire_evidence()`, which also handle clips, face images, and alert-history updates. |
| Unread frame sequence counter | `src/survai/state.py:33`, `:95` | `frame_id` is initialized and incremented but never read or returned by an API. The video stream checks the frame bytes directly instead. |
| Unused direct dependency | `requirements.txt:12` | The app, tests, and scripts do not import `rich` or invoke its CLI. Installed metadata only shows it as an optional dependency of HTTPX's CLI extra, which this app does not use. It can be removed from the project's direct requirements if that CLI is not intended. |

The four obsolete detector files contain 137 lines combined. Their five tests in `tests/test_rules.py` are the only test coverage exercising that old detector/config/engine stack. Remove or replace those tests as part of retiring that stack; do not confuse them with the active counter, walk-out, employee, or payment tests.

**Disconnected features and API data: review before removing**

| Item | Location | Current behavior |
| --- | --- | --- |
| AI-written session summary | `src/survai/llm.py:10`; `src/survai/server.py:44`, `:136` | Every repository caller of `create_app()` passes `summary_model=None`. Consequently the normal app never calls `write_summary()`. The legacy `config.yaml` model setting is not connected to dashboard startup. `/api/summary` still exists and returns template text if called manually. The visible Summary count cards use `door_counts`, not this endpoint or an AI model. |
| Old queue/session summary bookkeeping | `src/survai/state.py:38`, `:104`, `:155`; `src/survai/preview.py:157`; `src/survai/multi_camera.py:481` | `people_by_minute`, `peak_queue`, and `peak_queue_at` serve only `/api/summary`. Both real runtime `publish()` calls supply queue count `0`, so normal monitoring never raises the recorded peak queue above zero. Removing this bookkeeping requires deciding whether to retain the summary API. |
| Alert-history GET endpoint | `src/survai/server.py:92` | The current dashboard receives history and new alerts through `/events`; it does not GET `/api/alerts`. Tests do call this endpoint, and external clients could too. The DELETE endpoint at `/api/alerts/{alert_id}` is actively used and must stay. |
| Older doorway activity response | `src/survai/state.py:49`, `:67`; `src/survai/server.py:83` | `door_activity` is still stored and serialized, but the current dashboard renders `activity_log`. A test explicitly verifies the older response. The `DoorActivity` type and doorway publishing function are still needed to build the current activity log; only the redundant deque/response is a cleanup candidate. |
| Profile last-seen metadata | `src/survai/profiles.py:12`, `:45`; `src/survai/server.py:104` | This `Profile.last_seen` value is updated and returned by `/api/visits`, but no included frontend consumes it. Other trackers' separate `last_seen` fields control tracking and must remain. |
| Profile match-confidence metadata | `src/survai/profiles.py:17`; `src/survai/multi_camera.py:441`; `src/survai/server.py:110` | Calculated and returned by `/api/visits`, but no included frontend consumes `match_confidence`. Appearance matching itself remains active; do not remove the identity manager. |
| Older floor-distance guard in appearance matching | `src/survai/multi_identity.py:64`, `:76`, `:111`, `:165`, `:179`; `src/survai/config.py:154`; `src/survai/multi_camera.py:332`, `:347` | Every repository call to `GlobalIdentityManager.update()` passes an empty floor-coordinate dictionary. Its `local.floor`/`guest.floor` distance guard and `IdentityConfig.max_floor_distance` therefore never affect a current run. Calibrated camera runs use the separate active `FloorTracker` at `multi_camera.py:353`. These floor fields/parameter/guard can be simplified together if the older interface is no longer wanted. |

**Generated clutter and stale text**

- `.DS_Store` is an 8,196-byte macOS Finder metadata file and has no application role.
- `.pytest_cache/`, `.ruff_cache/`, and `__pycache__/` contain regenerable test/lint/Python caches. They are not source code.
- `src/survai.egg-info/` is generated installation metadata for the editable Python installation. It can be regenerated by reinstalling; leave it alone during a source cleanup unless rebuilding the installation. Its `SOURCES.txt` lists only some of the current tests and is not an authoritative project inventory.
- `dist/index.html` is generated from the current dashboard plus `scripts/hosted-demo.js`. It is used by the configured static build; rebuild it rather than editing it directly or classifying it as abandoned source.
- `Open Surveillance.command:5` points users to a `README.md` that does not exist in this project.
- `main.py:41`, `doctor.py:61`, `setup.html:72`, and the default camera names in `web.py:383` retain MacBook-specific wording from the earlier setup. This is stale/platform-specific wording, not dead executable code.

**Still used or intentionally optional**

- `scripts/hosted-demo.js`, `scripts/build-dashboard.mjs`, and `vercel.json` are linked by the static build. They are not loaded by the local live-camera server, but have a separate working entry point. The demo's `simulatedPhoto()`/`earlierPhoto` is still used for simulated walk-out alert evidence; it is not a real guest thumbnail.
- `Open Surveillance.command` remains the macOS launcher; the `.bat` files are the Windows launchers. Platform-specific files are not inherently unused.
- `doctor.py` is independently executable as `python -m survai.doctor`. CLI calibration, floor tools, synthetic feeds, `multi_test.py`, and replay support also have explicit entry points in `main.py`.
- WhatsApp delivery and `.env.example` are connected to `--whatsapp`. Browser alert sound and native Windows/macOS sound branches remain active in their respective modes.
- `torchvision` has no direct application import but is a required dependency of installed `ultralytics`. Keep it. `pytest` and `ruff` are development tools; they can be split into development requirements rather than deleted as unused.
- `employee-marker.png`, employee apron detection, floor calibration/fusion, person identity tracking, guest payment controls, activity logging, and walk-out evidence/face extraction all have application references.
- Internal `pay_zone` keys, old `dining`/`pay` zone aliases, and legacy pixel-coordinate handling are compatibility paths. The visible label is Counter; changing the internal key or removing compatibility handling requires a configuration migration.
- `tests/test_profiles.py` checks that guest photo fields/endpoints are absent. That remains a useful regression check for the current no-real-guest-thumbnail behavior, not an obsolete photo implementation.
- `.venv/`, `yolov8n.pt`, saved alert evidence/activity, recordings, and `walkout.multi.yaml` are environment/model/runtime/user data. This review does not classify them as deletable leftovers.

Validation: repository-wide Python AST reference/import tracing, searches across source/tests/scripts, frontend declaration and selector checks, dependency metadata inspection, and `python -m ruff check src tests scripts` (passed). No obvious unused dashboard/setup JavaScript declarations or unreferenced CSS class/ID selectors were found by the static checks. Tests were not rerun because application behavior was not changed. Static checks cannot establish every dynamic or external use; the disconnected API/compatibility items above are deliberately separate from confirmed unused application paths.
