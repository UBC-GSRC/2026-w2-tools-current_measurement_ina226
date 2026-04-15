#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Serial COM Logger for Windows
- Connects to a COM device
- Parses measurement blocks like:
    Current: 810.97 mA
    Bus Voltage: 13.13 V
    Shunt Voltage: 12.16 mV
    Power: 10637.45 mW
    Energy: 268.19 J
    Charge: 20.57 C
    Temperature: 21.65 *C
- Appends rows to a CSV with a timestamp per reading
- Interactive prompts in terminal
- Press SPACE to stop recording a session, then choose to record again
"""

import os
import re
import csv
import sys
import time
import threading
from datetime import datetime

try:
    import msvcrt  # Windows-only for non-blocking keypress
except ImportError:
    print("This script is intended for Windows (msvcrt not available).")
    sys.exit(1)

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Missing dependency 'pyserial'. Install with: pip install pyserial")
    sys.exit(1)


# ----------- Parsing Logic -----------

FIELD_PATTERNS = {
    "current_mA": re.compile(r"^\s*Current\s*:\s*([\-]?\d+(?:\.\d+)?)\s*mA\s*$", re.IGNORECASE),
    "bus_V": re.compile(r"^\s*Bus\s+Voltage\s*:\s*([\-]?\d+(?:\.\d+)?)\s*V\s*$", re.IGNORECASE),
    "shunt_mV": re.compile(r"^\s*Shunt\s+Voltage\s*:\s*([\-]?\d+(?:\.\d+)?)\s*mV\s*$", re.IGNORECASE),
    "power_mW": re.compile(r"^\s*Power\s*:\s*([\-]?\d+(?:\.\d+)?)\s*mW\s*$", re.IGNORECASE),
    "energy_J": re.compile(r"^\s*Energy\s*:\s*([\-]?\d+(?:\.\d+)?)\s*J\s*$", re.IGNORECASE),
    "charge_C": re.compile(r"^\s*Charge\s*:\s*([\-]?\d+(?:\.\d+)?)\s*C\s*$", re.IGNORECASE),
    "temperature_C": re.compile(r"^\s*Temperature\s*:\s*([\-]?\d+(?:\.\d+)?)\s*\*C\s*$", re.IGNORECASE),
}

CSV_HEADERS = [
    "timestamp_local",
    "current_mA",
    "bus_V",
    "shunt_mV",
    "power_mW",
    "energy_J",
    "charge_C",
    "temperature_C",
]


def parse_block(lines):
    """
    Parse a block of lines into a dict with required fields.
    Returns None if the block is incomplete.
    """
    data = {
        "current_mA": None,
        "bus_V": None,
        "shunt_mV": None,
        "power_mW": None,
        "energy_J": None,
        "charge_C": None,
        "temperature_C": None,
    }

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        for key, pat in FIELD_PATTERNS.items():
            m = pat.match(line)
            if m:
                data[key] = float(m.group(1))
                break

    # Only accept the block if all numeric fields are present
    if all(data[k] is not None for k in ["current_mA", "bus_V", "shunt_mV", "power_mW", "energy_J", "charge_C", "temperature_C"]):
        return data
    else:
        return None


# ----------- Serial Utilities -----------

def list_available_ports():
    ports = list_ports.comports()
    return [p.device for p in ports]


def open_serial_port(port, baudrate=115200, timeout=0.5):
    """
    Open and return a serial.Serial instance with a short timeout
    so we can check for stop events frequently.
    """
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baudrate
    ser.timeout = timeout  # seconds
    ser.write_timeout = 1
    ser.open()
    return ser


# ----------- Recording Logic -----------

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def generate_filename(output_dir):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = f"recording_{ts}.csv"
    return os.path.join(output_dir, name)


def write_header_if_new(filepath):
    file_exists = os.path.exists(filepath)
    if not file_exists or os.path.getsize(filepath) == 0:
        with open(filepath, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def keypress_listener(stop_event):
    """
    Wait for spacebar press in a non-blocking loop.
    """
    try:
        while not stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                # Space bar is ' ' -> b' '
                if ch == b' ':
                    stop_event.set()
                    break
                # Ctrl+C is handled by main thread normally, but just in case:
                if ch == b'\x03':  # Ctrl+C
                    stop_event.set()
                    break
            time.sleep(0.05)
    except Exception:
        # Fail-safe: stop if listener fails
        stop_event.set()


def record_once(ser, output_dir="recordings"):
    """
    Perform a single recording session until SPACE is pressed.
    Creates a new CSV file and appends rows for each parsed block.
    """
    ensure_dir(output_dir)
    filepath = generate_filename(output_dir)
    write_header_if_new(filepath)

    print(f"\nRecording started.")
    print(f"Writing to: {os.path.abspath(filepath)}")
    print("Press SPACE to stop...\n")

    stop_event = threading.Event()
    listener_thread = threading.Thread(target=keypress_listener, args=(stop_event,), daemon=True)
    listener_thread.start()

    block_lines = []

    # Keep reading until spacebar stops
    try:
        while not stop_event.is_set():
            try:
                raw = ser.readline()  # returns bytes (may be b'' on timeout)
            except serial.SerialException as e:
                print(f"[ERROR] Serial exception: {e}")
                break

            if not raw:
                # Timeout occurred; loop again to check stop_event
                continue

            # Decode line defensively
            try:
                line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
            except Exception:
                # Fallback
                line = str(raw, errors="ignore")

            # Detect block boundaries: blank line ends a block
            if line.strip() == "":
                if block_lines:
                    data = parse_block(block_lines)
                    if data:
                        timestamp = datetime.now().isoformat(timespec="seconds")
                        row = [
                            timestamp,
                            data["current_mA"],
                            data["bus_V"],
                            data["shunt_mV"],
                            data["power_mW"],
                            data["energy_J"],
                            data["charge_C"],
                            data["temperature_C"],
                        ]
                        with open(filepath, mode="a", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerow(row)
                        # Optional: print a short confirmation
                        print(f"[{timestamp}] V={data['bus_V']}V I={data['current_mA']}mA P={data['power_mW']}mW T={data['temperature_C']}C")
                    else:
                        # Incomplete or malformed block
                        print("[WARN] Skipped an incomplete block.")
                    block_lines = []
                # If buffer empty, just continue
            else:
                block_lines.append(line)

        if block_lines:
            data = parse_block(block_lines)
            if data:
                timestamp = datetime.now().isoformat(timespec="seconds")
                row = [
                    timestamp,
                    data["current_mA"],
                    data["bus_V"],
                    data["shunt_mV"],
                    data["power_mW"],
                    data["energy_J"],
                    data["charge_C"],
                    data["temperature_C"],
                ]
                with open(filepath, mode="a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(row)
                print(f"[{timestamp}] (final block) V={data['bus_V']}V I={data['current_mA']}mA P={data['power_mW']}mW T={data['temperature_C']}C")

    except KeyboardInterrupt:
        print("\n[INFO] KeyboardInterrupt received. Stopping recording.")

    finally:
        stop_event.set()
        listener_thread.join(timeout=1.0)
        print("Recording stopped.\n")

    return filepath


# ----------- CLI Interaction -----------

def prompt_yes_no(message, default="y"):
    default = default.lower()
    prompt = " [Y/n] " if default == "y" else " [y/N] "
    while True:
        resp = input(message + prompt).strip().lower()
        if resp == "" and default in ("y", "n"):
            return default == "y"
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print("Please enter y or n.")


def prompt_baud(default=115200):
    while True:
        s = input(f"Enter baud rate (default {default}): ").strip()
        if not s:
            return default
        try:
            val = int(s)
            if val > 0:
                return val
        except ValueError:
            pass
        print("Please enter a valid positive integer baud rate.")


def choose_com_port():
    ports = list_available_ports()
    if ports:
        print("Available COM ports:")
        for i, p in enumerate(ports, 1):
            print(f"  {i}. {p}")
    else:
        print("No COM ports detected. You can still enter a port (e.g., COM3).")

    while True:
        port = input("Enter COM port (e.g., COM3): ").strip()
        if port:
            return port
        print("COM port cannot be empty.")


def main():
    print("=== Windows Serial COM Logger ===\n")

    # Ask once for COM settings
    ready = prompt_yes_no("Ready to configure and record?", default="y")
    if not ready:
        print("Exiting.")
        return

    port = choose_com_port()
    baud = prompt_baud(default=115200)

    # Open serial port
    try:
        ser = open_serial_port(port, baudrate=baud, timeout=0.5)
    except Exception as e:
        print(f"[ERROR] Could not open {port} at {baud} baud: {e}")
        return

    print(f"\nConnected to {ser.port} @ {ser.baudrate} baud.")

    try:
        while True:
            if not prompt_yes_no("Start a new recording session now?", default="y"):
                break

            filepath = record_once(ser, output_dir="recordings")

            if not prompt_yes_no("Record another session (new file)?", default="n"):
                break

    finally:
        try:
            if ser and ser.is_open:
                ser.close()
                print(f"Closed serial port {port}.")
        except Exception:
            pass

    print("Done. Goodbye!")


if __name__ == "__main__":
    main()
