#!/usr/bin/env python3
"""

author: Enoc Martínez
modify: Daniel M. Toma 07/10/2024
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
from datetime import datetime, timedelta

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


# Update the paths to point to the FTP directory
FTP_BASE_PATH = os.path.expanduser('~/FTP')  # Absolute path to the FTP directory
SOURCE_FOLDER = os.path.join(FTP_BASE_PATH, 'PopUpBuoy')

@app.route('/upload/<popup_id>', methods=['GET'])
def upload_files(popup_id: str):
    """
    Copies files from 'PopUpBuoy' folder in FTP directory to 'PopUpBuoy_<popup_id>' in the FTP directory,
    and deletes the files from 'PopUpBuoy' after successful copy.
    """
    # Define source and destination paths
    source_folder = SOURCE_FOLDER
    destination_folder = os.path.join(FTP_BASE_PATH, f'PopUpBuoy_{popup_id}')

    # Log the upload request
    client_ip = request.remote_addr
    log.info(f"Received upload request from {client_ip} for popup_id={popup_id}")

    # Check if the source folder exists
    if not os.path.exists(source_folder):
        log.error(RED + f"Source folder '{source_folder}' does not exist!" + RST)
        return Response(json.dumps({"success": False, "message": "Source folder does not exist"}), 
                        status=500, 
                        mimetype="application/json")

    # Create the destination folder if it doesn't exist
    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
        log.info(GRN + f"Created destination folder '{destination_folder}'" + RST)

    # Copy files from the source to the destination folder
    try:
        shutil.copytree(source_folder, destination_folder, dirs_exist_ok=True)
        log.info(GRN + f"Successfully copied files from '{source_folder}' to '{destination_folder}'" + RST)

        # Now delete the contents of the source folder after successful copy
        for filename in os.listdir(source_folder):
            file_path = os.path.join(source_folder, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.remove(file_path)  # Delete the file
                    log.info(f"Deleted file: {file_path}")
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)  # Delete the directory and its contents
                    log.info(f"Deleted directory: {file_path}")
            except Exception as e:
                log.error(RED + f"Failed to delete {file_path}. Reason: {str(e)}" + RST)

        return Response(json.dumps({"success": True, "message": "Files successfully uploaded and source folder cleared"}), 
                        status=200, 
                        mimetype="application/json")
    except Exception as e:
        log.error(RED + f"Error while copying files: {str(e)}" + RST)
        return Response(json.dumps({"success": False, "message": f"Error during file upload: {str(e)}"}), 
                        status=500, 
                        mimetype="application/json")

# Function to load the status JSON file
def load_status():
    try:
        with open(popup_status_file) as f:
            status = json.load(f)
        return status
    except Exception as e:
        log.error(f"Failed to load status file: {str(e)}")
        return None

# Flask route to get permission details of a specific popup_id
@app.route('/permission/<popup_id>', methods=['GET'])
def get_permission_status(popup_id: str):
    # Load the status file
    status = load_status()
    if status is None:
        return Response(json.dumps({"success": False, "message": "Error loading status file"}), 
                        status=500, 
                        mimetype="application/json")
        # Check if the permission key exists
    if "permission" not in status:
        return Response(json.dumps({"success": False, "message": "'permission' section not found in the status file"}), 
                        status=500, 
                        mimetype="application/json")

    # Check if the popup_id exists in permission section
    if popup_id in status["permission"]:
        permission_details = status["permission"][popup_id]
        return Response(json.dumps({"success": True, "popup_id": popup_id, "permission": permission_details}), 
                        status=200, 
                        mimetype="application/json")
    else:
        return Response(json.dumps({"success": False, "message": f"popup_id {popup_id} not found"}), 
                        status=404, 
                        mimetype="application/json")
# Function to get the current time
def get_current_time_details():
    now = datetime.now()
    current_time = {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second
    }
    return current_time

# Flask route to get the current time
@app.route('/gettime', methods=['GET'])
def get_time_status():
    try:
        # Get the current time details
        current_time = get_current_time_details()

        # Return a successful response with the current time
        return Response(json.dumps({
            "success": True,
            "current_time": current_time
        }), status=200, mimetype="application/json")
    
    except Exception as e:
        # Handle errors and return an error response
        return Response(json.dumps({
            "success": False,
            "message": f"Error getting current time: {str(e)}"
        }), status=500, mimetype="application/json")
      
# Shutdown execution method
def shutdown_system():
    time.sleep(5)
    os.system("sudo poweroff")


@app.route('/control/shutdown', methods=['GET'])
def shutdown_callback():

    log.info(f"Received shutdown request, power off the system!")
    t = Thread(target=shutdown_system, args=())
    t.start()
    return Response(json.dumps({"success": True, "message": "success"}), status=200,
                    mimetype="application/json")

def copy_and_delete_files(popup_id):
    """Copies files from 'PopUpBuoy' folder in FTP directory to 'PopUpBuoy_<popup_id>' in the FTP directory,
    and deletes the files from 'PopUpBuoy' after successful copy."""
    dest_dir = os.path.join(FTP_BASE_PATH , f'PopUpBuoy_{popup_id}')

    # Ensure destination directory exists
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    # Copy files
    for filename in os.listdir(SOURCE_FOLDER):
        src_file = os.path.join(SOURCE_FOLDER, filename)
        dest_file = os.path.join(dest_dir, filename)
        shutil.copy(src_file, dest_file)

    # Delete files from the source directory after copying
    for filename in os.listdir(SOURCE_FOLDER):
        src_file = os.path.join(SOURCE_FOLDER, filename)
        os.remove(src_file)

def process_release(popup_status_file, popups_release, popups_release_mode):
    """Reads the popup_status_file JSON, compares releaseFlags, and updates if necessary."""
    # Load the JSON file
    with open(popup_status_file, 'r') as f:
        popup_status = json.load(f)

    # Loop over each popup in the release status
    for popup_id, release_value in popups_release.items():
        release_flag = popup_status["permission"][popup_id]["releaseFlag"]
        popup_status["permission"][popup_id]["releaseMode"] = popups_release_mode[popup_id]
        popup_status["permission"][popup_id]["sleeptime_h"] = str(sampling_time_hours)
        # If both release_flag and release_value are 1, do nothing
        if release_flag == 1 and release_value == 1:
            continue

        # If releaseFlag is 0 but release_value is 1, proceed with file copy
        if release_flag == 0 and release_value == 1:
            log.info(f"Release condition met for popup_id {popup_id}, copying files...")
            
            # Copy files and then delete them from the source
            copy_and_delete_files(popup_id)

            # Update releaseFlag to 1 in the JSON
            popup_status["permission"][popup_id]["releaseFlag"] = 1
            log.info(f"Updated releaseFlag to 1 for popup_id {popup_id}")

    # Save the updated JSON back to the file
    with open(popup_status_file, 'w') as f:
        json.dump(popup_status, f, indent=2)
    log.info(f"Updated popup-buoys.json file saved.")



if __name__ == "__main__":
    log = setup_log("popup-server")
    log.info("Loading config.yaml")
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    popups_pins = {}
    popups_timestamps = {}
    popups_release = {}
    popups_release_mode = {}
    sampling_time_hours = config["sampling_time_hours"]
    release_time = config["release_time_secs"]
    max_release_time = config["max_release_time_secs"]
    popup_status_file = config["pop_status_file"]
    # Read offset_seconds from the config
    offset_seconds = config.get("offset_seconds", 60)  # Default to 60 seconds (1 minute) if not found
    log.info(f"    sampling_time_hours: {sampling_time_hours} release_time: {release_time} max_release_time: {max_release_time}")

    # Get the current time
    current_time = datetime.now()
    log.info(f"    current_time: {current_time}")
    # Parse the new structure of popup_parameters
    for popup in config["popup_parameters"]:
        popup_id = str(popup["id"])  # Get the ID as a string
        gpio = str(popup["gpio"])     # Get the GPIO number as a string
        timestamp = popup["date"]      # Get the date string
        relsease_mode =  popup["releaseMode"]
        # Store the GPIO pin for the popup
        popups_pins[popup_id] = gpio
        popups_release_mode[popup_id] = relsease_mode
        popups_timestamps[popup_id] = datetime.strptime(timestamp, '%Y/%m/%d %H:%M:%S')
        log.info(f"    popup_id: {popup_id} popups_pins: {popups_pins[popup_id]} popups_timestamps: {popups_timestamps[popup_id]}")
            # Check if the timestamp is less than current time plus the offset
        if popups_timestamps[popup_id] < (current_time + timedelta(seconds=offset_seconds)):
            popups_release[popup_id] = 1  # Mark as release
        else:
            popups_release[popup_id] = 0  # Mark as not release

        log.info(f"    popup_id: {popup_id} popups_pins: {popups_pins[popup_id]} popups_timestamps: {popups_timestamps[popup_id]} popups_release: {popups_release[popup_id]}")

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
        process_release(popup_status_file, popups_release, popups_release_mode)

    log.info("Setting all GIPOs to low")
    for pin in popups_pins.values():
        p = int(pin)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(p, GPIO.OUT)
        GPIO.output(p, GPIO.LOW)

    app.run(host="0.0.0.0", debug=True)
