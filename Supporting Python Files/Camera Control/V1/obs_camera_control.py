import os
import sys
import json
import time
import atexit
import argparse
import threading
import socketserver
import http.server
import subprocess
import importlib
import traceback
from urllib.parse import urlparse

obsws = None
duvc = None

# Config Section
OBS_PASSWORD = ""  
OBS_PORT = 4455
FALLBACK_CAMERA_NAME = "HD Camera"
DEFAULT_DOCK_PORT = 8787

DEFAULT_PRESET_FILE = os.path.join(os.path.expanduser("~"), "Documents", "obs_stage_presets.json")

# Maps the importable module name -> the PyPI package name to install if it's missing.
REQUIRED_PACKAGES = {
    "obsws_python": "obsws-python",
    "duvc_ctl": "duvc-ctl",
}

SEED_PRESETS = {
    "_config": {"camera_name": FALLBACK_CAMERA_NAME},
    "stage_center": {"p": -96, "t": -2, "z": 5194},
    "stage_left": {"p": -105, "t": -3, "z": 4124},
    "stage_right": {"p": -87, "t": -5, "z": 4315},
    "stage_off_left": {"p": -100, "t": -3, "z": 4160},
}

# Filled in by argparse in main()
PRESET_FILE = DEFAULT_PRESET_FILE
PID_FILE = None
DOCK_PORT = DEFAULT_DOCK_PORT
SETUP_LOG_FILE = None

# Fallback log location used for crashes that happen before argparse has run
# (e.g. the script can't even find its own folder), so nothing is ever lost.
CRASH_LOG_FALLBACK = os.path.join(os.path.expanduser("~"), "Documents", "obs_camera_control_crash.log")


# Crash logging
def _log_exception(exc_type, exc_value, exc_tb):
    target = SETUP_LOG_FILE or CRASH_LOG_FALLBACK
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Uncaught exception:\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except Exception:
        pass  # logging must never itself crash the process


def _thread_excepthook(args):
    _log_exception(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = _log_exception
threading.excepthook = _thread_excepthook


def log_setup(message):
    print(message)
    if SETUP_LOG_FILE:
        try:
            os.makedirs(os.path.dirname(SETUP_LOG_FILE), exist_ok=True)
            with open(SETUP_LOG_FILE, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass  # logging is best-effort; never let it block startup


# Install dependencies
def pip_install(pip_name):
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    base_cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pip_name]

    try:
        subprocess.check_call(base_cmd, creationflags=creationflags)
        return
    except subprocess.CalledProcessError:
        pass  # fall through and retry as a per-user install below

    subprocess.check_call(base_cmd + ["--user"], creationflags=creationflags)


def ensure_dependencies():
    global obsws, duvc

    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            log_setup(f"[Setup] '{module_name}' not found. Installing '{pip_name}' via pip...")
            try:
                pip_install(pip_name)
            except subprocess.CalledProcessError as e:
                log_setup(f"[Setup] pip install failed for '{pip_name}': {e}")
                raise RuntimeError(
                    f"Could not install required package '{pip_name}'. "
                    f"Try running: {sys.executable} -m pip install {pip_name}"
                ) from e

            try:
                module = importlib.import_module(module_name)
            except ImportError as e:
                log_setup(f"[Setup] '{module_name}' still not importable after install: {e}")
                raise
            else:
                log_setup(f"[Setup] '{pip_name}' installed successfully.")

        if module_name == "obsws_python":
            obsws = module
        elif module_name == "duvc_ctl":
            duvc = module


# Presets
def write_seed_presets():
    os.makedirs(os.path.dirname(PRESET_FILE), exist_ok=True)
    with open(PRESET_FILE, "w") as f:
        json.dump(SEED_PRESETS, f, indent=4)
    print(f"[System] Resetting/Initializing stage presets file at: {PRESET_FILE}")


def load_presets():
    if not os.path.exists(PRESET_FILE) or os.path.getsize(PRESET_FILE) == 0:
        write_seed_presets()

    try:
        with open(PRESET_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[Warning] Presets file was corrupted. Forcing a preset reset...")
        write_seed_presets()
        return dict(SEED_PRESETS)


def save_presets(presets):
    os.makedirs(os.path.dirname(PRESET_FILE), exist_ok=True)
    with open(PRESET_FILE, "w") as f:
        json.dump(presets, f, indent=4)


def get_configured_camera_name(presets):
    return presets.get("_config", {}).get("camera_name", FALLBACK_CAMERA_NAME)


# Use a PID so we can stop the python file on close
def write_pid_file():
    try:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"[Warning] Could not write PID file: {e}")


def remove_pid_file():
    try:
        if PID_FILE and os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


# Camera controls
def move_camera_to_coordinates(camera_name, pan, tilt, zoom):
    print(f"Sending hardware instructions -> CAMERA: {camera_name} | PAN: {pan} | TILT: {tilt} | ZOOM: {zoom}")
    try:
        cam = duvc.find_camera(camera_name)
        if cam:
            cam.pan = pan
            cam.tilt = tilt
            cam.zoom = zoom
            cam.close()
        else:
            print(f"Error: Could not find hardware device matching name '{camera_name}'")
    except Exception as e:
        print(f"Hardware injection failed: {e}")


def list_camera_names():
    try:
        return list(duvc.list_cameras())
    except Exception as e:
        print(f"[Camera Detect] Failed: {e}")
        return []


def list_scene_names():
    try:
        req_client = obsws.ReqClient(host='127.0.0.1', port=OBS_PORT, password=OBS_PASSWORD)
        resp = req_client.get_scene_list()
        return [s.get("sceneName") for s in resp.scenes]
    except Exception as e:
        print(f"[Scene List] Could not reach OBS: {e}")
        return []


def on_current_program_scene_changed(data):
    scene_name = data.scene_name
    presets = load_presets()
    coords = presets.get(scene_name)
    if coords is None:
        return

    print(f"\n[OBS Event] Switched Scene to: '{scene_name}'")
    camera_name = get_configured_camera_name(presets)
    move_camera_to_coordinates(camera_name, coords["p"], coords["t"], coords["z"])


# OBS Dock
DOCK_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PTZ Camera Control</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, Segoe UI, sans-serif; background: #1e1e1e; color: #e6e6e6;
         margin: 0; padding: 14px; font-size: 13px; }
  h2 { font-size: 14px; margin: 0 0 12px 0; color: #fff; }
  label { display: block; margin: 10px 0 4px; color: #ababab; }
  .row { display: flex; gap: 6px; align-items: center; }
  select, input[type=number] { flex: 1; background: #2d2d2d; color: #fff; border: 1px solid #3f3f3f;
       border-radius: 4px; padding: 6px; font-size: 13px; }
  button { background: #3a3a3a; color: #fff; border: 1px solid #4a4a4a; border-radius: 4px;
       padding: 6px 10px; cursor: pointer; font-size: 12px; }
  button:hover { background: #474747; }
  button.primary { background: #2a6fd6; border-color: #2a6fd6; }
  button.primary:hover { background: #3a7fe6; }
  .ptz-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
  .actions { display: flex; gap: 6px; margin-top: 14px; flex-wrap: wrap; }
  #log { margin-top: 14px; background: #141414; border: 1px solid #2e2e2e; border-radius: 4px;
       padding: 8px; height: 90px; overflow-y: auto; font-family: monospace; font-size: 11px; color: #8fbf7f; }
</style>
</head>
<body>
  <h2>PTZ Camera Control</h2>

  <label for="camera">Camera</label>
  <div class="row">
    <select id="camera"></select>
    <button onclick="loadCameras()">Refresh</button>
  </div>

  <label for="scene">Scene</label>
  <div class="row">
    <select id="scene" onchange="loadPresetForScene()"></select>
    <button onclick="loadScenes()">Refresh</button>
  </div>

  <label>Pan / Tilt / Zoom</label>
  <div class="ptz-grid">
    <input type="number" id="pan" value="0">
    <input type="number" id="tilt" value="0">
    <input type="number" id="zoom" value="0">
  </div>

  <div class="actions">
    <button onclick="loadPresetForScene()">Load From Scene</button>
    <button class="primary" onclick="savePreset()">Save Preset</button>
    <button onclick="moveNow()">Move Camera Now</button>
  </div>

  <div id="log"></div>

<script>
function log(msg) {
  const el = document.getElementById('log');
  const line = document.createElement('div');
  line.textContent = new Date().toLocaleTimeString() + '  ' + msg;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

async function loadCameras() {
  try {
    const r = await fetch('/api/cameras');
    const j = await r.json();
    const sel = document.getElementById('camera');
    const current = sel.value;
    sel.innerHTML = '';
    j.cameras.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    });
    if (j.cameras.includes(current)) sel.value = current;
    log('Cameras refreshed (' + j.cameras.length + ' found)');
  } catch (e) { log('Failed to load cameras: ' + e); }
}

async function loadScenes() {
  try {
    const r = await fetch('/api/scenes');
    const j = await r.json();
    const sel = document.getElementById('scene');
    const current = sel.value;
    sel.innerHTML = '';
    j.scenes.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    });
    if (j.scenes.includes(current)) sel.value = current;
    log('Scenes refreshed (' + j.scenes.length + ' found)');
    loadPresetForScene();
  } catch (e) { log('Failed to load scenes: ' + e); }
}

async function loadPresetForScene() {
  const scene = document.getElementById('scene').value;
  if (!scene) return;
  try {
    const r = await fetch('/api/presets');
    const presets = await r.json();
    if (presets._config && presets._config.camera_name) {
      document.getElementById('camera').value = presets._config.camera_name;
    }
    const p = presets[scene];
    if (p) {
      document.getElementById('pan').value = p.p;
      document.getElementById('tilt').value = p.t;
      document.getElementById('zoom').value = p.z;
      log('Loaded preset for "' + scene + '"');
    } else {
      log('No saved preset yet for "' + scene + '"');
    }
  } catch (e) { log('Failed to load preset: ' + e); }
}

async function savePreset() {
  const scene = document.getElementById('scene').value;
  if (!scene) { log('Pick a scene first'); return; }
  const body = {
    scene: scene,
    camera_name: document.getElementById('camera').value,
    p: parseInt(document.getElementById('pan').value, 10) || 0,
    t: parseInt(document.getElementById('tilt').value, 10) || 0,
    z: parseInt(document.getElementById('zoom').value, 10) || 0
  };
  try {
    const r = await fetch('/api/presets', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const j = await r.json();
    log(j.ok ? ('Saved preset for "' + scene + '"') : ('Save failed: ' + (j.error || 'unknown error')));
  } catch (e) { log('Failed to save preset: ' + e); }
}

async function moveNow() {
  const body = {
    camera: document.getElementById('camera').value,
    p: parseInt(document.getElementById('pan').value, 10) || 0,
    t: parseInt(document.getElementById('tilt').value, 10) || 0,
    z: parseInt(document.getElementById('zoom').value, 10) || 0
  };
  try {
    const r = await fetch('/api/move', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const j = await r.json();
    log(j.ok ? 'Move command sent' : ('Move failed: ' + (j.error || 'unknown error')));
  } catch (e) { log('Failed to move camera: ' + e); }
}

loadCameras();
loadScenes();
</script>
</body>
</html>
"""


class DockRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep the console output clean; errors are still printed explicitly below

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(DOCK_HTML)
        elif path == "/api/presets":
            self._send_json(load_presets())
        elif path == "/api/cameras":
            self._send_json({"cameras": list_camera_names()})
        elif path == "/api/scenes":
            self._send_json({"scenes": list_scene_names()})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}

        if path == "/api/presets":
            scene = payload.get("scene")
            if not scene:
                self._send_json({"error": "scene is required"}, 400)
                return
            presets = load_presets()
            presets[scene] = {
                "p": int(payload.get("p", 0)),
                "t": int(payload.get("t", 0)),
                "z": int(payload.get("z", 0)),
            }
            if payload.get("camera_name"):
                presets.setdefault("_config", {})["camera_name"] = payload["camera_name"]
            save_presets(presets)
            self._send_json({"ok": True})

        elif path == "/api/move":
            camera_name = payload.get("camera") or get_configured_camera_name(load_presets())
            move_camera_to_coordinates(
                camera_name, int(payload.get("p", 0)), int(payload.get("t", 0)), int(payload.get("z", 0))
            )
            self._send_json({"ok": True})

        else:
            self._send_json({"error": "not found"}, 404)


def start_dock_server(port):
    try:
        server = socketserver.ThreadingTCPServer(("127.0.0.1", port), DockRequestHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[Dock] Control panel available at http://127.0.0.1:{port}/")
        print("[Dock] In OBS: Docks -> Custom Browser Docks... -> add that URL to dock it in the main window.")
        return server
    except Exception as e:
        print(f"[Dock] Could not start local control panel server on port {port}: {e}")
        return None


def cmd_list_cameras():
    print(json.dumps({"cameras": [{"name": n} for n in list_camera_names()]}))


def cmd_move(camera_name, pan, tilt, zoom):
    move_camera_to_coordinates(camera_name, pan, tilt, zoom)
    print("Move command sent.")


# Main listener
def run_listener():
    write_pid_file()
    atexit.register(remove_pid_file)

    print("Connecting to OBS WebSockets...")
    try:
        req_client = obsws.ReqClient(host='127.0.0.1', port=OBS_PORT, password=OBS_PASSWORD)

        vcam_status = req_client.get_virtual_cam_status()
        if not vcam_status.output_active:
            print("Virtual Camera is offline. Activating it now...")
            req_client.start_virtual_cam()
            print("Virtual Camera Activated Successfully.")
        else:
            print("Virtual Camera is already active.")

        presets = load_presets()
        monitored_scenes = [k for k in presets.keys() if k != "_config"]
        print("\nMonitoring Stage Scene Changes... Press CTRL+C to stop.")
        print(f"Active Monitored Scenes: {monitored_scenes}")
        print(f"PID: {os.getpid()} (written to {PID_FILE})")

        start_dock_server(DOCK_PORT)

        event_client = obsws.EventClient(host='127.0.0.1', port=OBS_PORT, password=OBS_PASSWORD)
        event_client.callback.register(on_current_program_scene_changed)

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nScript terminated safely by user.")
    except Exception as e:
        log_setup(f"An error occurred (check the OBS WebSocket port/password): {e}")
    finally:
        remove_pid_file()


def main():
    global PRESET_FILE, PID_FILE, DOCK_PORT, SETUP_LOG_FILE, OBS_PORT, OBS_PASSWORD

    parser = argparse.ArgumentParser(description="OBS PTZ camera controller")
    parser.add_argument("--preset-file", dest="preset_file", default=None)
    parser.add_argument("--port", dest="port", type=int, default=DEFAULT_DOCK_PORT,
                         help="Port for the local control-panel web server")
    parser.add_argument("--obs-port", dest="obs_port", type=int, default=OBS_PORT,
                         help="OBS WebSocket server port (normally supplied automatically by the Lua launcher)")
    parser.add_argument("--obs-password", dest="obs_password", default="",
                         help="OBS WebSocket server password (normally supplied automatically by the Lua launcher)")
    parser.add_argument("--list-cameras", action="store_true")
    parser.add_argument("--move", nargs=4, metavar=("CAMERA", "PAN", "TILT", "ZOOM"))
    parser.add_argument("--skip-dependency-check", action="store_true",
                         help="Skip auto-installing obsws-python/duvc-ctl (assume they're already installed)")
    args = parser.parse_args()

    if args.preset_file:
        PRESET_FILE = args.preset_file
    PID_FILE = os.path.join(os.path.dirname(PRESET_FILE), "obs_camera_control.pid")
    SETUP_LOG_FILE = os.path.join(os.path.dirname(PRESET_FILE), "obs_camera_control_setup.log")
    DOCK_PORT = args.port
    OBS_PORT = args.obs_port
    OBS_PASSWORD = args.obs_password

    if not OBS_PASSWORD:
        log_setup("[Warning] No OBS WebSocket password was supplied. This only works if OBS WebSocket "
                   "authentication is disabled; otherwise the connection below will fail.")

    if not args.skip_dependency_check:
        ensure_dependencies()
    else:
        global obsws, duvc
        import obsws_python as obsws
        import duvc_ctl as duvc

    if args.list_cameras:
        cmd_list_cameras()
        return

    if args.move:
        camera_name, pan, tilt, zoom = args.move
        cmd_move(camera_name, int(pan), int(tilt), int(zoom))
        return

    run_listener()


if __name__ == "__main__":
    main()
