#!/usr/bin/env python3
"""

author: Enoc Martínez
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

app = Flask(__name__)

# Color codes
GRN = "\x1B[32m"
RST = "\033[0m"
BLU = "\x1B[34m"
YEL = "\x1B[33m"
RED = "\x1B[31m"
MAG = "\x1B[35m"
CYN = "\x1B[36m"
WHT = "\x1B[37m"
NRM = "\x1B[0m"
PRL = "\033[95m"
RST = "\033[0m"


def setup_log(name, path="log", log_level="debug"):
    """
    Setups the logging module
    :param name: log name (.log will be appended)
    :param path: where the logs will be stored
    :param log_level: log level as string, it can be "debug, "info", "warning" and "error"
    """

    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Check arguments
    if len(name) < 1 or len(path) < 1:
        raise ValueError("name \"%s\" not valid", name)
    elif len(path) < 1:
        raise ValueError("name \"%s\" not valid", name)

    # Convert to logging level
    if log_level == 'debug':
        level = logging.DEBUG
    elif log_level == 'info':
        level = logging.INFO
    elif log_level == 'warning':
        level = logging.WARNING
    elif log_level == 'error':
        level = logging.ERROR
    else:
        raise ValueError("log level \"%s\" not valid" % log_level)

    if not os.path.exists(path):
        os.makedirs(path)

    filename = os.path.join(path, name)
    if not filename.endswith(".log"):
        filename += ".log"
    print("Creating log", filename)
    print("name", name)

    logger = logging.getLogger()
    logger.setLevel(level)
    log_formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)-7s: %(message)s',
                                      datefmt='%Y/%m/%d %H:%M:%S')
    handler = TimedRotatingFileHandler(filename, when="midnight", interval=1, backupCount=7)
    handler.setFormatter(log_formatter)
    logger.addHandler(handler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(log_formatter)
    logger.addHandler(consoleHandler)

    logger.info("")
    logger.info(f"===== {name} =====")

    return logger

def ping_host(host):
    try:
        # Use subprocess to run the ping command
        result = subprocess.run(['ping', '-c', '4', host], capture_output=True, text=True, timeout=5)
        # Check the return code to see if the ping was successful
        if result.returncode == 0:
            return True
        else:
            return False

    except subprocess.TimeoutExpired:
        return False

def release_thread(popup_id: int, release_time: float, max_release_time: float, client_ip: str, popup_status_file: str):
    """
    This will release the
    :param popup_id:
    :param release_time:
    :return:
    """
    pin = int(popups_pins[popup_id])

    log.info(f"Activating pin {pin} to release popup={popup_id}")
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

        log.info(f"Updating status file {popup_status_file}")


        with open(popup_status_file) as f:
            status = json.load(f)
        popup_status[str(popup_id)] = True
        status["releasedBuoys"][str(popup_id)] = True

        with open(popup_status_file, "w") as f:
            f.write(json.dumps(status, indent=2))




def release_popup(popup_id: str, client_ip):
    popup_id = str(popup_id)
    if popup_id not in popups_pins.keys():
        log.error(RED + f"pop-up with id: {popup_id} not registered!" + RST)
        return False
    else:
        t = Thread(target=release_thread, args=(popup_id, release_time, max_release_time, client_ip,
                                                popup_status_file), daemon=True)
        t.start()
        return True


@app.route('/release/<popup_id>', methods=['GET'])
def release_callback(popup_id: str):
    popup_id = str(popup_id)
    client_ip = request.remote_addr
    log.info(f"Received release request from {client_ip} with popup_id={popup_id}")
    ret = release_popup(popup_id, client_ip)
    if ret:
        return Response(json.dumps({"success": True, "message": "success"}), status=200,
                        mimetype="application/json")
    else:
        return Response(json.dumps({"success": False, "message": "pop-up release failed"}), status=500, mimetype="application/json")


def shutdown_system():
    time.sleep(5)
    os.system("sudo shutdown -h")


@app.route('/control/shutdown', methods=['GET'])
def shutdown_callback():

    log.info(f"Received shutdown request, power off the system!")
    t = Thread(target=shutdown_system, args=())
    t.start()
    return Response(json.dumps({"success": True, "message": "success"}), status=200,
                    mimetype="application/json")



if __name__ == "__main__":
    log = setup_log("popup-server")
    log.info("Loading config.yaml")
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    popups_pins = {}
    release_time = config["release_time_secs"]
    max_release_time = config["max_release_time_secs"]

    for line in config["popup-gpios"]:
        key, value = line.split(":")
        popups_pins[key] = value


    popup_status_file = config["pop_status_file"]

    if not os.path.exists(popup_status_file):
        log.info("Generating status file")
        status = {"releasedBuoys": {}}
        for popup_id in popups_pins.keys():
            status["releasedBuoys"][str(popup_id)] = False
        with open(popup_status_file, "w") as f:
            f.write(json.dumps(status, indent=2))
    else:
        with open(popup_status_file) as f:
            popup_status =  json.load(f)
        log.info(f"popup status already exists released status:")
        for key, value in popup_status["releasedBuoys"].items():
            log.info(f"    popup buoy '{key}' released: {value}")

    log.info("Setting all GIPOs to low")
    for pin in popups_pins.values():
        p = int(pin)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(p, GPIO.OUT)
        GPIO.output(p, GPIO.LOW)

    app.run(host="0.0.0.0", debug=True)