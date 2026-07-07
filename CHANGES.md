# State 6 / BlueBoat Upload — Changes

Branch: `feature/state6-transfer`

## What changed and why

### Bug fixes (prevented server from starting)

| Bug | Fix |
|-----|-----|
| `/whoareyou` — firmware called `/whoami`, 404 every time | Renamed route to `/whoami` |
| `upload_permission` defined twice — Flask raised `AssertionError` at startup | Renamed `/filelist` handler to `file_list` |
| `whoami: "popup-serdver"` in config.yaml — failed assertion | Fixed typo to `"popup-server"` |
| `sync_time` key missing — `/getsynctime` raised `KeyError` / 500 | Added `sync_time: 9` to config.yaml |
| `log` not set when run via `flask run` — all routes crashed | Moved logger to module level with `basicConfig` fallback |

### New endpoints

#### `GET /whoami`
Returns `{"id": "popup-server"}` on the lander or `{"id": "blueboat"}` on the boat. The buoy uses this to decide which upload protocol to run.

#### `GET /uploadpermission/<popup_id>`
Reads `navigation.yaml` → `allow: true/false`. On the boat, a ROS2 node or operator writes this file. Returns `{"allow": true}` when the boat is ready to receive.

#### `PUT /filelist/<popup_id>`
Buoy sends: `{"files": [{"name": "GPS_track.csv", "size": 1234}, ...]}`
Server checks `~/popup_data/<popup_id>/` for files already received and responds with the missing subset: `{"tobesent": ["GPS_track.csv"]}`. Enables free retries — re-running after a dropped WiFi connection only re-uploads what didn't land.

#### `POST /transfercomplete/<popup_id>`
Buoy sends `{"files_sent": N}` after FTP upload. Server verifies files exist on disk and returns `{"success": true, "files_received": M}`. This is the signal that causes the buoy to return 0 from `tryUploadDataToUSV()` and exit state 6.

### Conditional GPIO
`import RPi.GPIO` is now guarded by a `try/except ImportError`. The server starts on non-Raspberry Pi hardware (Jetson, dev laptop) without crashing — GPIO pins are simply no-ops.

### Embedded FTP server (blueboat mode)
When `whoami: blueboat`, `pyftpdlib` is started as a background thread on port `2121` (non-privileged, no root needed). Accepts user `pop` / `plome2023`, root at `~/popup_data/`. Files land in `~/popup_data/<popup_id>/` after the buoy issues `MakeDir`+`ChangeWorkDir`.

### Dual-role config
| File | Used on |
|------|---------|
| `config.yaml` | Lander (Raspberry Pi) — GPIO pins, release timing |
| `blueboat-config.yaml` | BlueBoat (Jetson) — no GPIO, FTP upload port |

Set `POPUP_SERVER_CONFIG=blueboat-config.yaml` env var to switch.

### navigation.yaml
File-based interface between Flask and the autonomy stack. Flask reads it on every `/uploadpermission` call — no code coupling to ROS2. A brain_node or operator script writes `allow: true` when the boat is on station.

---

## Running on the BlueBoat (Jetson)

```bash
# One-time: sync code
rsync -av popup-server/ jetson1tc:~/popup-server/

# Start server (blueboat mode)
cd ~/popup-server
POPUP_SERVER_CONFIG=blueboat-config.yaml POPUP_NAV_FILE=navigation.yaml python3 app.py
```

Files received from buoys appear in `~/popup_data/<popup_id>/`.
