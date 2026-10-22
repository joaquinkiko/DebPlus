# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os
import re
import subprocess
import time

MNT = "/mnt/debplus"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIONS_DIR = os.path.join(ROOT_DIR, "options")
DESKTOPS_PATH = os.path.join(OPTIONS_DIR, "desktops.list")
WMS_PATH = os.path.join(OPTIONS_DIR, "wms.list")
EXTRAS_PATH = os.path.join(OPTIONS_DIR, "extras.list")
COMPONENTS_PATH = os.path.join(OPTIONS_DIR, "components.list")
TARGET_PACKAGES_PATH = os.path.join(ROOT_DIR, "packages", "target.list")
FIRMWARE_PATH = os.path.join(ROOT_DIR, "packages", "firmware.list")


def run(command):
	"""Run a shell string or argv list, capturing output."""
	return subprocess.run(command, shell=isinstance(command, str),
	                      capture_output=True, text=True)


def load_profile(path):
	"""Parse KEY="value" lines."""
	profile = {}
	with open(path) as profile_file:
		for line in profile_file:
			line = line.strip()
			if line and not line.startswith("#") and "=" in line:
				key, value = line.split("=", 1)
				profile[key] = value.strip('"')
	return profile


def read_lines(path):
	"""Non-blank, non-comment lines of a list file."""
	with open(path) as list_file:
		return [line.strip() for line in list_file
		        if line.strip() and not line.startswith("#")]


def _rows(path):
	"""Each list line split on '|' into stripped fields."""
	return [tuple(value.strip() for value in line.split("|"))
	        for line in read_lines(path)]


def desktop_rows():
	"""(id, label, package) per Debian desktop option."""
	return _rows(DESKTOPS_PATH)


def wm_rows():
	"""(id, label, repo, ref) per window manager repo."""
	return _rows(WMS_PATH)


def extras_rows():
	"""(menu, section, package, label) per optional package."""
	return _rows(EXTRAS_PATH)


def components_rows():
	"""(id, label, description) per apt component."""
	return _rows(COMPONENTS_PATH)


PROFILE = load_profile(os.path.join(ROOT_DIR, "profile.env"))
MIRROR = PROFILE["MIRROR"]


def keyboard_config(layout):
	"""Contents of /etc/default/keyboard for an XKB layout."""
	return (
		'XKBMODEL="pc105"\n'
		f'XKBLAYOUT="{layout}"\n'
		'XKBVARIANT=""\n'
		'XKBOPTIONS=""\n'
		'BACKSPACE="guess"\n'
	)


def apply_keymap(layout):
	"""Switch the live console to an XKB layout. Returns an error string or None."""
	if not layout or not layout.replace("_", "").replace("-", "").isalnum():
		return f"Invalid layout name '{layout}'"
	if run(["ckbcomp", "-layout", layout]).returncode != 0:
		return f"Unknown layout '{layout}'"
	with open("/etc/default/keyboard", "w") as keyboard_file:
		keyboard_file.write(keyboard_config(layout))
	result = run(["setupcon", "--force", "-k"])
	if result.returncode != 0:
		return (result.stderr.strip().splitlines() or ["setupcon failed"])[-1]
	return None


def get_supported_locales():
	"""Names from locales' SUPPORTED list, or an empty set if absent."""
	try:
		with open("/usr/share/i18n/SUPPORTED") as supported_file:
			return {line.split()[0] for line in supported_file if line.strip()}
	except OSError:
		return set()


def detect_boot_mode():
	return "uefi" if os.path.isdir("/sys/firmware/efi") else "bios"


def is_online():
	if run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"]).stdout.strip() == "full":
		return True
	return run("ping -c 1 -W 1 1.1.1.1").returncode == 0


def get_disks():
	"""(device, size, model) for whole disks, excluding floppy, RAM, loop, and optical."""
	disks = []
	for line in run("lsblk -dnpo NAME,SIZE,MODEL -e 1,2,7,11").stdout.splitlines():
		parts = line.split(None, 2)
		if len(parts) < 2 or "zram" in parts[0]:
			continue
		disks.append((parts[0], parts[1], parts[2].strip() if len(parts) > 2 else ""))
	return disks


def get_timezones():
	result = run(
		"find /usr/share/zoneinfo/Africa /usr/share/zoneinfo/America "
		"/usr/share/zoneinfo/Antarctica /usr/share/zoneinfo/Asia "
		"/usr/share/zoneinfo/Atlantic /usr/share/zoneinfo/Australia "
		"/usr/share/zoneinfo/Europe /usr/share/zoneinfo/Indian "
		"/usr/share/zoneinfo/Pacific -type f 2>/dev/null | "
		"sed 's|/usr/share/zoneinfo/||' | sort"
	)
	return result.stdout.strip().splitlines()


def get_partitions(disk):
	"""Partition device paths in order, ignoring device-mapper children."""
	partitions = []
	for line in run(f"lsblk -lnpo NAME,TYPE {disk}").stdout.splitlines():
		fields = line.split()
		if len(fields) >= 2 and fields[1] == "part":
			partitions.append(fields[0])
	return partitions


def wifi_scan():
	"""(ssid, signal, security) per visible network, strongest first."""
	run(["nmcli", "device", "wifi", "rescan"])
	time.sleep(2)
	networks = {}
	listing = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
	for line in listing.stdout.splitlines():
		# Terse output escapes colons inside SSIDs as \:
		fields = [part.replace("\\:", ":") for part in re.split(r"(?<!\\):", line)]
		if len(fields) < 3 or not fields[0]:
			continue
		ssid, signal, security = fields[0], int(fields[1] or 0), fields[2]
		if ssid not in networks or signal > networks[ssid][1]:
			networks[ssid] = (ssid, signal, security)
	return sorted(networks.values(), key=lambda entry: entry[1], reverse=True)


def wifi_connect(ssid, password):
	"""(success, message). Password is None for open networks."""
	command = ["nmcli", "device", "wifi", "connect", ssid]
	if password:
		command += ["password", password]
	result = run(command)
	if result.returncode == 0:
		return True, f"Connected to {ssid}"
	return False, result.stderr.strip() or f"Failed to connect to {ssid}"


def wifi_test():
	"""(passed, message): link is up and the Debian mirror answers over HTTP(S)."""
	state = run(["nmcli", "-t", "-f", "STATE", "general"]).stdout.strip()
	if not state.startswith("connected"):
		return False, f"Not connected (NetworkManager state: {state or 'unknown'})"
	code = run(["curl", "-s", "-I", "--max-time", "10", "-o", "/dev/null",
	            "-w", "%{http_code}", MIRROR]).stdout.strip()
	if code.startswith(("2", "3")):
		return True, f"Connected, {MIRROR} reachable (HTTP {code})"
	return False, f"Connected, but {MIRROR} is unreachable (HTTP {code or 'none'})"
