#!/usr/bin/env python3
"""
author: Enoc Martínez
modify: Daniel M. Toma 04/11/2024
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 14/11/23
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from threading import Thread

import yaml
from flask import Flask, request, Response

app = Flask(__name__)

# Module-level logger; configured with file handler in __main__.
# When run via `flask run` the basicConfig below is enough for stdout.
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)-7s: %(message)s')
log = logging.getLogger("popup-server")

# Color codes
GRN = "\x1B[32m"
RST = "\033[0m"
YEL = "\x1B[33m"
RED = "\x1B[31m"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    config_path = os.environ.get("POPUP_SERVER_CONFIG", "config.yaml")
    logger = logging.getLogger()
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    valid_roles = ["popup-server", "blueboat"]
    if config.get("whoami") not in valid_roles:
        raise ValueError(f"config 'whoami' must be one of {valid_roles}, got: {config.get('whoami')!r}")
    return config


def save_config(config):
    config_path = os.environ.get("POPUP_SERVER_CONFIG", "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_log(name, path="log", log_level="debug"):
    if not os.path.exists(path):
        os.makedirs(path)
    filename = os.path.join(path, f"{name}.log")
    root = logging.getLogger()
    root.handlers.clear()  # remove handlers added by module-level basicConfig
    root.setLevel(getattr(logging, log_level.upper()))
    fmt = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)-7s: %(message)s', datefmt='%Y/%m/%d %H:%M:%S')
    handler = TimedRotatingFileHandler(filename, when="midnight", interval=1, backupCount=7)
    handler.setFormatter(fmt)
    root.addHandler(handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)
    root.info(f"===== {name} initialized =====")
    return root


# ---------------------------------------------------------------------------
# Status tracking
# ---------------------------------------------------------------------------

def init_buoy_status_file(config: dict, status_file="log/status.tab"):
    import pandas as pd
    if os.path.exists(status_file):
        log.info(f"Buoy status log already exists: {status_file}")
        return
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for buoy in config.get("popup_parameters", []):
        lines.append({"id": int(buoy["id"]), "time": now, "status": "I"})
    df = pd.DataFrame(lines)
    df.to_csv(status_file, header=False, index=False, sep="\t")


def update_buoy_status_file(buoy_id: int, status: str, status_file="log/status.tab"):
    import pandas as pd
    buoy_id = int(buoy_id)
    log.info(f"Updating status '{status}' for buoy {buoy_id}")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df = pd.read_csv(status_file, names=["id", "time", "status"], sep="\t")
    df["id"] = df["id"].astype(int)
    df.loc[df["id"] == buoy_id, "time"] = now
    df.loc[df["id"] == buoy_id, "status"] = status
    df.to_csv(status_file, header=False, index=False, sep="\t")


# ---------------------------------------------------------------------------
# GPIO (lander only)
# ---------------------------------------------------------------------------

_GPIO = None

def _init_gpio(config):
    global _GPIO
    try:
        import RPi.GPIO as GPIO
        _GPIO = GPIO
        GPIO.setmode(GPIO.BOARD)
        for popup in config.get("popup_parameters", []):
            pin = int(popup["gpio"])
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
        log.info("GPIO initialized")
    except (ImportError, RuntimeError):
        log.warning("RPi.GPIO not available — running without GPIO (blueboat or dev mode)")
        _GPIO = None


def _gpio_output(pin, value):
    if _GPIO is not None:
        _GPIO.output(int(pin), value)


# ---------------------------------------------------------------------------
# Release (lander only)
# ---------------------------------------------------------------------------

popups_pins = {}
release_time = 1
max_release_time = 20


def ping_host(host):
    try:
        result = subprocess.run(['ping', '-c', '4', host], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def release_thread(popup_id: str, rel_time: float, max_rel_time: float, client_ip: str):
    pin = popups_pins.get(popup_id)
    if pin is None:
        log.error(RED + f"No GPIO pin for popup_id={popup_id}" + RST)
        return
    log.info(f"Activating pin {pin} to release popup={popup_id}")
    update_buoy_status_file(popup_id, "A")
    _gpio_output(pin, True)
    time.sleep(rel_time)
    init = time.time()
    timeout = False
    while ping_host(client_ip):
        log.warning(YEL + f"Buoy id={popup_id} ip={client_ip} still connected" + RST)
        time.sleep(rel_time)
        if (time.time() - init) > max_rel_time:
            timeout = True
            break
    _gpio_output(pin, False)
    if timeout:
        log.error(RED + f"Release cycle failed! id={popup_id} ip={client_ip}" + RST)
    else:
        config = load_config()
        entry = next((p for p in config.get("popup_parameters", []) if str(p["id"]) == popup_id), None)
        if entry:
            entry["released"] = True
            save_config(config)
        log.info(GRN + f"Release finished id={popup_id}" + RST)
        update_buoy_status_file(popup_id, "R")


def release_popup(popup_id: str, client_ip):
    popup_id = str(popup_id)
    if popup_id not in popups_pins:
        log.error(RED + f"popup_id {popup_id} not registered" + RST)
        return False
    t = Thread(target=release_thread, args=(popup_id, release_time, max_release_time, client_ip), daemon=True)
    t.start()
    return True


# ---------------------------------------------------------------------------
# File-transfer helpers (blueboat)
# ---------------------------------------------------------------------------

POPUP_DATA_BASE = os.path.expanduser("~/popup_data")


def receive_dir_for(popup_id: str) -> str:
    return os.path.join(POPUP_DATA_BASE, str(popup_id))


def files_already_received(popup_id: str) -> dict:
    """Returns {filename: size} for files already in the receive directory."""
    d = receive_dir_for(popup_id)
    if not os.path.isdir(d):
        return {}
    result = {}
    for name in os.listdir(d):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            result[name] = os.path.getsize(path)
    return result


# ---------------------------------------------------------------------------
# FTP server (blueboat only)
# ---------------------------------------------------------------------------

def start_ftp_server(data_dir: str, port: int = 2121):
    try:
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer as PyFTPServer

        os.makedirs(data_dir, exist_ok=True)
        authorizer = DummyAuthorizer()
        # Full permissions: list, retrieve, store, delete, mkdir, etc.
        authorizer.add_user("pop", "plome2023", data_dir, perm="elradfmwMT")
        handler = FTPHandler
        handler.authorizer = authorizer
        handler.passive_ports = range(60000, 60100)
        handler.banner = "popup-server FTP"
        server = PyFTPServer(("0.0.0.0", port), handler)
        log.info(GRN + f"FTP server listening on port {port}, root={data_dir}" + RST)
        server.serve_forever()
    except ImportError:
        log.error(RED + "pyftpdlib not installed — FTP server not started" + RST)
    except Exception as e:
        log.error(RED + f"FTP server error: {e}" + RST)


# ---------------------------------------------------------------------------
# Navigation / BlueBoat state interface
# ---------------------------------------------------------------------------

def get_navigation_params():
    nav_file = os.environ.get("POPUP_NAV_FILE", "navigation.yaml")
    try:
        with open(nav_file) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        log.warning(f"navigation.yaml not found at {nav_file}, defaulting allow=False")
        return {"allow": False}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FTP_BASE_PATH = os.path.expanduser('~/FTP')
SOURCE_FOLDER = os.path.join(FTP_BASE_PATH, 'PopUpBuoy')


def get_current_time_details():
    now = datetime.now(timezone.utc)
    return {"year": now.year, "month": now.month, "day": now.day,
            "hour": now.hour, "minute": now.minute, "second": now.second}


def _json(payload: dict, status: int = 200) -> Response:
    return Response(json.dumps(payload), status=status, mimetype="application/json")


def shutdown_system():
    time.sleep(5)
    os.system("sudo poweroff")


# ---------------------------------------------------------------------------
# Routes — shared
# ---------------------------------------------------------------------------

@app.route('/whoami', methods=['GET'])
def whoami():
    config = load_config()
    return _json({"id": config["whoami"]})


@app.route('/gettime', methods=['GET'])
def get_time_status():
    try:
        return _json({"success": True, "current_time": get_current_time_details()})
    except Exception as e:
        return _json({"success": False, "message": str(e)}, 500)


@app.route("/getsynctime", methods=["GET"])
def get_sync_time():
    config = load_config()
    return _json({"sync_time": config.get("sync_time", 9)})


@app.route("/getsynctime/<popup_id>", methods=["GET"])
def get_sync_time_with_id(popup_id):
    update_buoy_status_file(popup_id, "S")
    return get_sync_time()


@app.route('/control/reboot', methods=['GET', 'POST'])
def reboot_system():
    try:
        subprocess.run(["sudo", "reboot"], check=True)
        return _json({"success": True, "message": "Reboot issued."})
    except subprocess.CalledProcessError as e:
        return _json({"success": False, "message": str(e)}, 500)


@app.route('/control/shutdown', methods=['GET'])
def shutdown_callback():
    log.info("Received shutdown request")
    Thread(target=shutdown_system, daemon=True).start()
    return _json({"success": True, "message": "success"})


# ---------------------------------------------------------------------------
# Routes — lander only
# ---------------------------------------------------------------------------

@app.route('/permission/<popup_id>', methods=['GET'])
def get_permission_status(popup_id: str):
    config = load_config()
    popup_entry = next((p for p in config.get("popup_parameters", []) if str(p["id"]) == popup_id), None)
    if not popup_entry:
        return _json({"success": False, "message": f"popup_id {popup_id} not found"}, 404)

    release_date = datetime.strptime(popup_entry["date"], '%Y/%m/%d %H:%M:%S')
    update_buoy_status_file(popup_id, "P")

    release_flag = 1 if datetime.now() >= release_date else 0
    permission = {
        "releaseFlag": release_flag,
        "releaseMode": popup_entry["releaseMode"],
        "sleeptime_h": popup_entry["sleeptime_h"],
        "sleeptime_m": popup_entry["sleeptime_m"],
    }
    return _json({"success": True, "popup_id": popup_id, "permission": permission})


@app.route('/release/<popup_id>', methods=['GET'])
def release_callback(popup_id: str):
    client_ip = request.remote_addr
    log.info(f"Release request from {client_ip} for popup_id={popup_id}")
    ret = release_popup(popup_id, client_ip)
    return _json({"success": ret, "message": "success" if ret else "release failed"}, 200 if ret else 500)


@app.route('/upload/<popup_id>', methods=['GET'])
def upload_files(popup_id: str):
    """Moves lander instrument files from inbox into per-buoy FTP folder."""
    destination_folder = os.path.join(FTP_BASE_PATH, f'PopUpBuoy_{popup_id}')
    client_ip = request.remote_addr
    log.info(f"Upload request from {client_ip} for popup_id={popup_id}")

    if not os.path.exists(SOURCE_FOLDER):
        log.error(RED + f"Source folder '{SOURCE_FOLDER}' does not exist" + RST)
        return _json({"success": False, "message": "Source folder does not exist"}, 500)

    os.makedirs(destination_folder, exist_ok=True)
    try:
        shutil.copytree(SOURCE_FOLDER, destination_folder, dirs_exist_ok=True)
        log.info(GRN + f"Copied '{SOURCE_FOLDER}' → '{destination_folder}'" + RST)
        for filename in os.listdir(SOURCE_FOLDER):
            os.remove(os.path.join(SOURCE_FOLDER, filename))
        return _json({"success": True, "message": "Files uploaded and inbox cleared"})
    except Exception as e:
        log.error(RED + f"Error copying files: {e}" + RST)
        return _json({"success": False, "message": str(e)}, 500)


# ---------------------------------------------------------------------------
# Routes — blueboat only
# ---------------------------------------------------------------------------

@app.route('/uploadpermission/<popup_id>', methods=['GET'])
def upload_permission(popup_id: str):
    navigation = get_navigation_params()
    allow = navigation.get("allow", False)
    log.info(f"Buoy {popup_id} requested upload permission → {allow}")
    return _json({"allow": allow})


@app.route('/filelist/<popup_id>', methods=['PUT'])
def file_list(popup_id: str):
    """
    Buoy PUTs JSON: {"files": [{"name": "GPS_track.csv", "size": 1234}, ...]}
    Server responds with the subset it doesn't already have:
    {"tobesent": ["GPS_track.csv"]}
    """
    log.info(f"File manifest received from buoy {popup_id}")
    try:
        payload = request.get_json(force=True, silent=True) or {}
        offered = {f["name"]: f.get("size", -1) for f in payload.get("files", [])}
    except Exception as e:
        log.warning(f"Could not parse manifest body: {e}")
        offered = {}

    already_have = files_already_received(popup_id)
    to_send = []
    for name, size in offered.items():
        existing_size = already_have.get(name, -1)
        if existing_size != size:
            to_send.append(name)
        else:
            log.info(f"  Already have {name} ({size} B) — skipping")

    log.info(f"  Requesting {len(to_send)} of {len(offered)} offered files")
    os.makedirs(receive_dir_for(popup_id), exist_ok=True)
    return _json({"tobesent": to_send})


@app.route('/transfercomplete/<popup_id>', methods=['POST'])
def transfer_complete(popup_id: str):
    """
    Buoy POSTs {"files_sent": N} after FTP upload.
    Server verifies files landed and returns success.
    """
    try:
        payload = request.get_json(force=True, silent=True) or {}
        files_sent = payload.get("files_sent", 0)
    except Exception:
        files_sent = 0

    received = files_already_received(popup_id)
    log.info(GRN + f"Transfer complete from buoy {popup_id}: sent={files_sent}, on_disk={len(received)}" + RST)

    if len(received) == 0 and files_sent > 0:
        log.warning(YEL + "files_sent > 0 but nothing found on disk — FTP may have failed" + RST)
        return _json({"success": False, "message": "No files found on server after transfer"}, 500)

    return _json({"success": True, "files_received": len(received), "files_sent": files_sent})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log = setup_log("popup-server")
    log.info("Loading config")
    config = load_config()

    whoami_val = config["whoami"]
    log.info(f"Running as: {whoami_val}")

    init_buoy_status_file(config)

    release_time = config.get("release_time_secs", 1)
    max_release_time = config.get("max_release_time_secs", 20)

    if whoami_val == "popup-server":
        for popup in config.get("popup_parameters", []):
            popups_pins[str(popup["id"])] = str(popup["gpio"])
            log.info(f"  popup_id={popup['id']} pin={popup['gpio']}")
        _init_gpio(config)

    if whoami_val == "blueboat":
        ftp_port = config.get("ftp_upload_port", 2121)
        ftp_thread = Thread(target=start_ftp_server, args=(POPUP_DATA_BASE, ftp_port), daemon=True)
        ftp_thread.start()
        log.info(f"FTP upload server thread started on port {ftp_port}")

    debug = os.environ.get("POPUP_SERVER_DEBUG") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)
