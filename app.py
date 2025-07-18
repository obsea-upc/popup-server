#!/usr/bin/env python3
"""
author: Enoc Martínez
modify: Daniel M. Toma 04/11/2024
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 14/11/23
"""

from flask import Flask, request, Response
import json
import os
import logging
from logging.handlers import TimedRotatingFileHandler
import RPi.GPIO as GPIO
import yaml
from threading import Thread
import time
import subprocess
import shutil
from datetime import datetime, timedelta, timezone
import pandas as pd

app = Flask(__name__)

# Color codes
GRN = "\x1B[32m"
RST = "\033[0m"
YEL = "\x1B[33m"
RED = "\x1B[31m"


def init_buoy_status_file(config: dict, status_file="log/status.tab"):
    """
    Initializes buoy status file
    :param config:
    :param status_file:
    :return:
    """
    if os.path.exists(status_file):
        log.info(f"Buoy status log already exists! {status_file}")
        return

    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"Creating buoy status file: {status_file}")
    for buoy in config["popup_parameters"]:
        lines.append({
            "id": int(buoy["id"]),
            "time": now,
            "status": "I"  # init
        })

    df = pd.DataFrame(lines)
    df.to_csv(status_file, header=False, index=False, sep="\t")


def update_buoy_status_file(buoy_id: int, status: str, status_file="log/status.tab"):
    buoy_id = int(buoy_id)
    log.info(f"Updating status '{status}' for buoy {buoy_id}")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df = pd.read_csv(status_file, names=["id", "time", "status"], sep="\t")
    df["id"] = df["id"].astype(int)
    # update the row!
    df.loc[df["id"] == buoy_id, "time"] = now
    df.loc[df["id"] == buoy_id, "status"] = status
    df.to_csv(status_file, header=False, index=False, sep="\t")


def setup_log(name, path="log", log_level="debug"):
    """
    Sets up the logging module.
    """
    if not os.path.exists(path):
        os.makedirs(path)
    filename = os.path.join(path, f"{name}.log")
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))
    handler = TimedRotatingFileHandler(filename, when="midnight", interval=1, backupCount=7)
    handler.setFormatter(
        logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)-7s: %(message)s', datefmt='%Y/%m/%d %H:%M:%S'))
    logger.addHandler(handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(handler.formatter)
    logger.addHandler(console_handler)
    logger.info(f"===== {name} initialized =====")
    return logger


def load_config():
    """Loads the YAML configuration file."""
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def save_config(config):
    """Saves the modified YAML configuration back to the file."""
    with open("config.yaml", "w") as f:
        yaml.dump(config, f)


def ping_host(host):
    try:
        result = subprocess.run(['ping', '-c', '4', host], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def release_thread(popup_id: str, release_time: float, max_release_time: float, client_ip: str):
    """
    Handles the release process for the specified popup_id
    """
    pin = int(popups_pins[popup_id])
    log.info(f"Activating pin {pin} to release popup={popup_id}")
    update_buoy_status_file(popup_id, "A")
    GPIO.output(pin, GPIO.HIGH)
    time.sleep(release_time)
    init = time.time()
    timeout = False
    while ping_host(client_ip):
        log.warning(YEL + f"Pop-up with id={popup_id} buoy with ip={client_ip} is still connected!" + RST)
        time.sleep(release_time)
        if (time.time() - init) > max_release_time:
            timeout = True
            break
    GPIO.output(pin, GPIO.LOW)
    if timeout:
        log.error(RED + f"Release cycle failed!!  pop-up id={popup_id} ip={client_ip}" + RST)
    else:
        log.info(GRN + f"Released finished id={popup_id}" + RST)

        config = load_config()
        popup_entry = next((popup for popup in config["popup_parameters"] if str(popup["id"]) == popup_id), None)
        if popup_entry:
            popup_entry["released"] = True
            save_config(config)
            log.info(f"Updated released status for popup_id {popup_id} to True")
            update_buoy_status_file(popup_id, "R")


def release_popup(popup_id: str, client_ip):
    popup_id = str(popup_id)
    if popup_id not in popups_pins.keys():
        log.error(RED + f"pop-up with id: {popup_id} not registered!" + RST)
        return False
    else:
        # Start a thread to handle the release process
        t = Thread(target=release_thread, args=(popup_id, release_time, max_release_time, client_ip), daemon=True)
        t.start()
        return True


@app.route('/release/<popup_id>', methods=['GET'])
def release_callback(popup_id: str):
    client_ip = request.remote_addr
    log.info(f"Received release request from {client_ip} with popup_id={popup_id}")
    ret = release_popup(popup_id, client_ip)
    return Response(json.dumps({"success": ret, "message": "success" if ret else "pop-up release failed"}),
                    status=200 if ret else 500, mimetype="application/json")


# FTP directories
FTP_BASE_PATH = os.path.expanduser('~/FTP')
SOURCE_FOLDER = os.path.join(FTP_BASE_PATH, 'PopUpBuoy')


@app.route('/upload/<popup_id>', methods=['GET'])
def upload_files(popup_id: str):
    source_folder = SOURCE_FOLDER
    destination_folder = os.path.join(FTP_BASE_PATH, f'PopUpBuoy_{popup_id}')
    client_ip = request.remote_addr
    log.info(f"Received upload request from {client_ip} for popup_id={popup_id}")

    if not os.path.exists(source_folder):
        log.error(RED + f"Source folder '{source_folder}' does not exist!" + RST)
        return Response(json.dumps({"success": False, "message": "Source folder does not exist"}), status=500,
                        mimetype="application/json")

    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
        log.info(GRN + f"Created destination folder '{destination_folder}'" + RST)

    try:
        shutil.copytree(source_folder, destination_folder, dirs_exist_ok=True)
        log.info(GRN + f"Copied files from '{source_folder}' to '{destination_folder}'" + RST)
        for filename in os.listdir(source_folder):
            file_path = os.path.join(source_folder, filename)
            os.remove(file_path)
        return Response(json.dumps({"success": True, "message": "Files uploaded and source folder cleared"}),
                        status=200, mimetype="application/json")
    except Exception as e:
        log.error(RED + f"Error while copying files: {str(e)}" + RST)
        return Response(json.dumps({"success": False, "message": f"Error during file upload: {str(e)}"}), status=500,
                        mimetype="application/json")


# Function to get the current time
def get_current_time_details():
    now = datetime.now(timezone.utc)
    return {"year": now.year, "month": now.month, "day": now.day, "hour": now.hour, "minute": now.minute,
            "second": now.second}


@app.route('/gettime', methods=['GET'])
def get_time_status():
    try:
        return Response(json.dumps({"success": True, "current_time": get_current_time_details()}), status=200,
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"success": False, "message": f"Error getting current time: {str(e)}"}), status=500,
                        mimetype="application/json")


@app.route('/control/reboot', methods=['GET', 'POST'])
def reboot_system():
    try:
        subprocess.run(["sudo", "reboot"], check=True)
        return Response(json.dumps({"success": True, "message": "Reboot command issued. System is rebooting."}),
                        status=200, mimetype="application/json")
    except subprocess.CalledProcessError as e:
        return Response(json.dumps({"success": False, "message": f"Failed to issue reboot command: {str(e)}"}),
                        status=500, mimetype="application/json")


def shutdown_system():
    time.sleep(5)
    os.system("sudo poweroff")


# Update the paths to point to the FTP directory
FTP_BASE_PATH = os.path.expanduser('~/FTP')
SOURCE_FOLDER = os.path.join(FTP_BASE_PATH, 'PopUpBuoy')


def copy_and_delete_files(popup_id):
    """Copies files from 'PopUpBuoy' to 'PopUpBuoy_<popup_id>' and deletes the original files."""
    dest_dir = os.path.join(FTP_BASE_PATH, f'PopUpBuoy_{popup_id}')
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    for filename in os.listdir(SOURCE_FOLDER):
        src_file = os.path.join(SOURCE_FOLDER, filename)
        dest_file = os.path.join(dest_dir, filename)
        shutil.copy(src_file, dest_file)

    for filename in os.listdir(SOURCE_FOLDER):
        os.remove(os.path.join(SOURCE_FOLDER, filename))


@app.route('/control/shutdown', methods=['GET'])
def shutdown_callback():
    log.info(f"Received shutdown request, powering off!")
    Thread(target=shutdown_system, daemon=True).start()
    return Response(json.dumps({"success": True, "message": "success"}), status=200, mimetype="application/json")


@app.route("/getsynctime", methods=["GET"])
def get_sync_time():
    log.info(f"Getting sync time from config")
    config = load_config()
    resp = {"sync_time": config["sync_time"]}
    return Response(json.dumps(resp), status=200, mimetype="application/json")


@app.route("/getsynctime/<popup_id>", methods=["GET"])
def get_sync_time_with_id(popup_id):
    update_buoy_status_file(popup_id, "S")
    return get_sync_time()


@app.route('/permission/<popup_id>', methods=['GET'])
def get_permission_status(popup_id: str):
    config = load_config()
    popup_entry = next((popup for popup in config["popup_parameters"] if str(popup["id"]) == popup_id), None)

    if not popup_entry:
        return Response(json.dumps({"success": False, "message": f"popup_id {popup_id} not found"}),
                        status=404, mimetype="application/json")

    release_date = datetime.strptime(popup_entry["date"], '%Y/%m/%d %H:%M:%S')
    current_time = datetime.now()
    update_buoy_status_file(popup_id, "P")

    if current_time >= release_date:
        permission = {
            "releaseFlag": 1,
            "releaseMode": popup_entry["releaseMode"],
            "sleeptime_h": popup_entry["sleeptime_h"],
            "sleeptime_m": popup_entry["sleeptime_m"],
        }
        return Response(json.dumps({
            "success": True,
            "popup_id": popup_id,
            "permission": permission,
        }), status=200, mimetype="application/json")
    else:
        nopermission = {
            "releaseFlag": 0,
            "releaseMode": popup_entry["releaseMode"],
            "sleeptime_h": popup_entry["sleeptime_h"],
            "sleeptime_m": popup_entry["sleeptime_m"],
        }
        return Response(json.dumps({
            "success": True,
            "popup_id": popup_id,
            "permission": nopermission,
        }), status=200, mimetype="application/json")


if __name__ == "__main__":
    log = setup_log("popup-server")
    log.info("Loading config.yaml")
    config = load_config()
    init_buoy_status_file(config)

    popups_pins = {}
    release_time = config["release_time_secs"]
    max_release_time = config["max_release_time_secs"]

    for popup in config["popup_parameters"]:
        popup_id = str(popup["id"])
        gpio = str(popup["gpio"])
        popups_pins[popup_id] = gpio
        log.info(f"    popup_id: {popup_id} popups_pins: {popups_pins[popup_id]}")

    for pin in popups_pins.values():
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(int(pin), GPIO.OUT)
        GPIO.output(int(pin), GPIO.LOW)

    app.run(host="0.0.0.0", debug=True)
