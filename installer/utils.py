# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os
import re
import subprocess
import time
import shutil
from urllib.parse import urlparse
from dataclasses import dataclass

MNT = "/mnt/debplus"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIONS_DIR = os.path.join(ROOT_DIR, "options")
WM_DIR = os.path.join(OPTIONS_DIR, "wm")
CONSOLE_ROW = ("console", "Console only", "-", "No desktop, text console only")
WMS_PATH = os.path.join(OPTIONS_DIR, "wms.list")
EXTRAS_PATH = os.path.join(OPTIONS_DIR, "extras.list")
COMPONENTS_PATH = os.path.join(OPTIONS_DIR, "components.list")
TARGET_PACKAGES_PATH = os.path.join(ROOT_DIR, "packages", "target.list")
FIRMWARE_PATH = os.path.join(ROOT_DIR, "packages", "firmware.list")
CUSTOM_PATH = os.path.join(OPTIONS_DIR, "custom.list")
CUSTOM_DIR = os.path.join(OPTIONS_DIR, "custom")
REPO_SCHEMES = ("https", "http", "git")

MIN_RAM_MIB = 1024
WARN_RAM_MIB = 2048
MIN_DISK_GIB = 10
WARN_DISK_GIB = 20
REQUIRED_TOOLS = ("debootstrap", "parted", "wipefs", "partprobe", "blkid",
                  "lsblk", "chroot", "mkfs.fat", "mkfs.ext4", "mkfs.btrfs",
                  "mkfs.xfs", "mokutil", "timedatectl")
ESP_RESERVE_GIB = 1
                  
def disk_bytes(device):
	return int(_query(["lsblk", "-bdno", "SIZE", device]) or 0)


	def swap_gib(self, ram_gib: int, disk_gib: int) -> int:
		"""Swap equal to RAM, limited so root keeps its minimum. 0 means no room."""
		reserve = MIN_DISK_GIB + (ESP_RESERVE_GIB if self.boot_mode == "uefi" else 0)
		return max(0, min(ram_gib, disk_gib - reserve))


@dataclass
class Check:
	level: str  # "block", "warn", or "ok"
	title: str
	message: str
	fix: str = None  # "network", "disk", "clock", or None


def missing_tools():
	"""Tools the installer image must ship. Empty when the image is complete."""
	return [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]


def _query(argv):
	"""Stdout of a tool, or empty string if it is not installed."""
	if not shutil.which(argv[0]):
		return ""
	return run(argv).stdout.strip()


def _check_network(online):
	if online:
		return Check("ok", "Internet", "connected")
	return Check("block", "Internet",
	             "Debian is downloaded during install. Connect to Wi-Fi or Ethernet first.",
	             "network")


def _check_memory():
	with open("/proc/meminfo") as meminfo_file:
		kib = next(int(line.split()[1]) for line in meminfo_file
		           if line.startswith("MemTotal:"))
	gib = kib / 2**20
	if kib < MIN_RAM_MIB * 1024:
		return Check("block", "Memory",
		             f"This computer has {gib:.1f} GB. Debian needs at least "
		             f"{MIN_RAM_MIB // 1024} GB to install.")
	if kib < WARN_RAM_MIB * 1024:
		return Check("warn", "Memory",
		             f"This computer has {gib:.1f} GB. Install works, but the desktop "
		             f"may feel slow. {WARN_RAM_MIB // 1024} GB or more is recommended.")
	return Check("ok", "Memory", f"{gib:.1f} GB")


def _check_disk(device):
	gib = int(_query(["lsblk", "-bdno", "SIZE", device]) or 0) / 2**30
	if gib < MIN_DISK_GIB:
		return Check("block", "Disk",
		             f"The chosen disk is {gib:.0f} GB. Debian needs at least "
		             f"{MIN_DISK_GIB} GB.", "disk")
	if gib < WARN_DISK_GIB:
		return Check("warn", "Disk",
		             f"The chosen disk is {gib:.0f} GB. Install works, but space will "
		             f"be tight. {WARN_DISK_GIB} GB or more is recommended.", "disk")
	return Check("ok", "Disk", f"{gib:.0f} GB")


def _check_secure_boot():
	if detect_boot_mode() != "uefi":
		return Check("ok", "Secure Boot", "not applicable")
	state = _query(["mokutil", "--sb-state"]).lower()
	if "enabled" in state:
		return Check("ok", "Secure Boot",
		             "on. Debian supports it. Some drivers, such as NVIDIA, need a one-time key enrollment after install.")
	if "disabled" in state:
		return Check("ok", "Secure Boot", "off")
	return None  # unknown state is an image problem, not a user problem


def _check_clock():
	if _query(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) == "yes":
		return Check("ok", "Clock", "set automatically")
	return Check("warn", "Clock",
	             "The clock is not synced over the internet. A wrong time can "
	             "cause package download errors.", "clock")


def enable_ntp():
	run(["timedatectl", "set-ntp", "true"])


def preflight(config, online):
	"""List of Check objects. Only level "block" stops Install."""
	checks = [_check_network(online), _check_memory(),
	          _check_secure_boot(), _check_clock()]
	if config.disk:
		checks.append(_check_disk(config.disk))
	return [check for check in checks if check]


def check_repo(url):
	"""(passed, message): URL is well-formed and git ls-remote can reach it."""
	url = url.strip()
	if not url or url.startswith("-"):
		return False, "Invalid repository address"
	is_scp_style = re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:", url)
	if not is_scp_style and urlparse(url).scheme not in REPO_SCHEMES:
		return False, "Use https://, http://, git:// or user@host:path"
	if not shutil.which("git"):
		return False, "git is not installed on the live system"
	environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
	               "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
	try:
		result = subprocess.run(["git", "ls-remote", "--exit-code", "--", url],
		                        capture_output=True, text=True, timeout=20,
		                        env=environment)
	except subprocess.TimeoutExpired:
		return False, "Timed out reaching the repository"
	if result.returncode == 0:
		return True, "Repository reachable"
	detail = (result.stderr.strip().splitlines() or ["no refs found"])[-1]
	return False, f"Cannot reach repository: {detail}"


def custom_rows():
	"""(id, menu, section, label, script) per script-installed package."""
	return _rows(CUSTOM_PATH)


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
	"""(id, label, package, description) per desktop. Console is always first."""
	"""Old code"""
	return [CONSOLE_ROW]


def wm_rows():
	"""(id, label, repo, ref, description) per window manager, with local repos resolved."""
	rows = []
	for wm_id, label, repo, ref, description in _rows(WMS_PATH):
		if repo.startswith("/"):
			repo = os.path.join(WM_DIR, repo.lstrip("/"))
		rows.append((wm_id, label, repo, ref, description))
	return rows


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


def boot_medium_disk():
	"""Whole-disk path the live medium was booted from, or None outside live mode."""
	source = run(["findmnt", "-no", "SOURCE", "/run/live/medium"]).stdout.strip()
	if not source:
		return None
	parent = run(["lsblk", "-no", "PKNAME", source]).stdout.strip().splitlines()
	return f"/dev/{parent[0]}" if parent and parent[0] else source


def get_disks():
	"""(device, size, model) for whole disks, excluding floppy, RAM, loop, optical, and the boot medium."""
	boot_disk = boot_medium_disk()
	disks = []
	for line in run("lsblk -dnpo NAME,SIZE,MODEL -e 1,2,7,11").stdout.splitlines():
		parts = line.split(None, 2)
		if len(parts) < 2 or "zram" in parts[0] or parts[0] == boot_disk:
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
	name = re.sub(r"[^A-Za-z0-9_-]", "_", ssid)
	path = os.path.join("/etc/NetworkManager/system-connections", f"{name}.nmconnection")
	lines = [
		"[connection]", f"id={ssid}", "type=wifi", "",
		"[wifi]", f"ssid={ssid}", "mode=infrastructure", "",
	]
	if password:
		lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={password}", ""]

	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(fd, "w") as profile_file:
		profile_file.write("\n".join(lines) + "\n")

	run(["nmcli", "connection", "reload"])
	result = run(["nmcli", "connection", "up", "id", ssid])
	if result.returncode == 0:
		return True, f"Connected to {ssid}"
	os.remove(path)
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


def package_requirements():
	"""Package id -> required archive id, or '-' when none."""
	requirements = {package: requires
	                for _menu, _section, package, _label, requires in extras_rows()}
	requirements.update({custom_id: requires
	                     for custom_id, _menu, _section, _label, _script, requires in custom_rows()})
	return requirements


def partition_rows(disk):
	"""(device, size, filesystem) per partition on disk."""
	rows = []
	for line in run(["lsblk", "-lnpo", "NAME,TYPE,SIZE,FSTYPE", disk]).stdout.splitlines():
		fields = line.split() + ["", ""]
		if fields[1] == "part":
			rows.append((fields[0], fields[2], fields[3] or "unformatted"))
	return rows


def partition_table_type(disk):
	"""'gpt', 'dos', or empty if unknown."""
	return run(["blkid", "-s", "PTTYPE", "-o", "value", disk]).stdout.strip()

def ram_mib():
	with open("/proc/meminfo") as meminfo_file:
		kib = next(int(line.split()[1]) for line in meminfo_file
		           if line.startswith("MemTotal:"))
	return kib // 1024