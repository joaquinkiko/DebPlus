# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import functools
import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

MNT = "/mnt/debplus"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIONS_DIR = os.path.join(ROOT_DIR, "options")
PACKAGES_DIR = os.path.join(ROOT_DIR, "packages")
WM_DIR = os.path.join(OPTIONS_DIR, "wm")
CUSTOM_DIR = os.path.join(OPTIONS_DIR, "custom")
WMS_PATH = os.path.join(OPTIONS_DIR, "wms.list")
EXTRAS_PATH = os.path.join(OPTIONS_DIR, "extras.list")
COMPONENTS_PATH = os.path.join(OPTIONS_DIR, "components.list")
CUSTOM_PATH = os.path.join(OPTIONS_DIR, "custom.list")
OPTIONAL_PATH = os.path.join(PACKAGES_DIR, "optional.list")
MESA_PATH = os.path.join(PACKAGES_DIR, "mesa.list")
HEADERS_PATH = os.path.join(PACKAGES_DIR, "headers.list")
DKMS_PATH = os.path.join(PACKAGES_DIR, "dkms.list")
BOOT_PATH = os.path.join(PACKAGES_DIR, "boot.list")
CONSOLE_ROW = ("console", "Console only", "-", "No desktop, text console only")

# Outside live/ on purpose: live-boot layers every *.squashfs it finds in live/.
TARGET_IMAGE = "/run/live/medium/install/target.squashfs"
TARGET_REPO = "/run/live/medium/install/repo"  # flat apt repo of the optional packages
REPO_SCHEMES = ("https", "http", "git")

LANGUAGE_NAMES_PATH = "/usr/share/iso-codes/json/iso_639-2.json"
SUPPORTED_LOCALES_PATH = "/usr/share/i18n/SUPPORTED"
ZONEINFO_DIR = Path("/usr/share/zoneinfo")
ZONE_REGIONS = ("Africa", "America", "Antarctica", "Asia", "Atlantic",
                "Australia", "Europe", "Indian", "Pacific")

MIN_RAM_MIB = 1024
WARN_RAM_MIB = 2048
MIN_DISK_GIB = 10
MIN_HOME_GIB = 5
WARN_DISK_GIB = 20
REQUIRED_TOOLS = ("unsquashfs", "parted", "wipefs", "partprobe", "blkid",
                  "lsblk", "chroot", "mkfs.fat", "mkfs.ext4", "mkfs.btrfs",
                  "mkfs.xfs", "mokutil", "timedatectl", "cryptsetup")
ESP_RESERVE_GIB = 1
ESP_END_MIB = 513
BIOS_BOOT_END_MIB = 2

TIMEZONE_LOOKUPS = (
	("https://ipinfo.io/timezone", ()),
	("https://ipapi.co/timezone", ()),
	("https://ipwho.is/", ("timezone", "id")),
	("https://freeipapi.com/api/json", ("timeZone",)),
	("http://ip-api.com/json/?fields=timezone", ("timezone",)),
)

COUNTRY_LOCALES = {
	"US": "en_US.UTF-8", "GB": "en_GB.UTF-8", "IE": "en_IE.UTF-8",
	"DE": "de_DE.UTF-8", "AT": "de_AT.UTF-8", "CH": "de_CH.UTF-8",
	"FR": "fr_FR.UTF-8", "BE": "fr_BE.UTF-8", "ES": "es_ES.UTF-8",
	"MX": "es_MX.UTF-8", "AR": "es_AR.UTF-8", "IT": "it_IT.UTF-8",
	"PT": "pt_PT.UTF-8", "BR": "pt_BR.UTF-8", "NL": "nl_NL.UTF-8",
	"SE": "sv_SE.UTF-8", "NO": "nb_NO.UTF-8", "DK": "da_DK.UTF-8",
	"FI": "fi_FI.UTF-8", "PL": "pl_PL.UTF-8", "RU": "ru_RU.UTF-8",
	"JP": "ja_JP.UTF-8", "CN": "zh_CN.UTF-8", "KR": "ko_KR.UTF-8",
	"CZ": "cs_CZ.UTF-8", "HU": "hu_HU.UTF-8", "GR": "el_GR.UTF-8",
	"TR": "tr_TR.UTF-8", "UA": "uk_UA.UTF-8", "CA": "en_CA.UTF-8",
	"AU": "en_AU.UTF-8", "NZ": "en_NZ.UTF-8",
}


@dataclass
class Check:
	level: str  # "block", "warn", or "ok"
	title: str
	message: str
	fix: str = None  # "network", "disk", "clock", or None


# ── Locale ─────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def language_names():
	"""ISO 639 code -> English name, or empty dict if iso-codes is absent."""
	try:
		with open(LANGUAGE_NAMES_PATH) as names_file:
			entries = json.load(names_file)["639-2"]
	except (OSError, KeyError, ValueError):
		return {}
	return {entry["alpha_2"]: entry["name"] for entry in entries if "alpha_2" in entry}


def locale_country(locale_name):
	"""Territory code from a locale name, e.g. de_AT.UTF-8 -> AT, or empty string."""
	base = locale_name.split(".")[0].split("@")[0]
	parts = base.split("_")
	return parts[1] if len(parts) > 1 else ""


def locale_language(locale_name):
	"""Language name for a locale code, or empty string if unknown."""
	return language_names().get(locale_name.split("_")[0], "")


@functools.lru_cache(maxsize=1)
def get_supported_locales():
	"""Names from locales' SUPPORTED list, or an empty set if absent."""
	try:
		with open(SUPPORTED_LOCALES_PATH) as supported_file:
			return frozenset(line.split()[0] for line in supported_file if line.strip())
	except OSError:
		return frozenset()


@functools.lru_cache(maxsize=1)
def _zone_countries():
	"""Zone name -> country code. The first zone.tab entry wins."""
	countries = {}
	try:
		with open(ZONEINFO_DIR / "zone.tab") as zone_file:
			for line in zone_file:
				fields = line.split()
				if len(fields) >= 3:
					countries.setdefault(fields[2], fields[0].split(",")[0])
	except OSError:
		pass
	return countries


def locale_for_timezone(zone):
	"""Installed UTF-8 locale for the zone's country, or None."""
	locale_name = COUNTRY_LOCALES.get(_zone_countries().get(zone, ""))
	supported_locales = get_supported_locales()
	if locale_name and (not supported_locales or locale_name in supported_locales):
		return locale_name
	return None


# ── Timezone ───────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def get_timezones():
	"""Sorted zone names such as Europe/Oslo. Symlinks are skipped, as find -type f did."""
	zones = []
	for region in ZONE_REGIONS:
		region_dir = ZONEINFO_DIR / region
		if region_dir.is_dir():
			zones += [str(path.relative_to(ZONEINFO_DIR))
			          for path in region_dir.rglob("*")
			          if path.is_file() and not path.is_symlink()]
	return tuple(sorted(zones))


def _extract_zone(body, path):
	if not path:
		return body.strip()
	value = json.loads(body)
	for key in path:
		value = value.get(key, {}) if isinstance(value, dict) else ""
	return value if isinstance(value, str) else ""


def detect_timezone():
	"""(zone, reason): zone from the public IP, or None with the reason it failed."""
	valid_zones = get_timezones()
	reason = "no lookup responded"
	for url, path in TIMEZONE_LOOKUPS:
		result = run(["curl", "-sf", "--max-time", "5", "-A", "DebPlus", url])
		if result.returncode != 0:
			reason = f"{url} unreachable (curl exit {result.returncode})"
			continue
		try:
			zone = _extract_zone(result.stdout, path)
		except ValueError:
			zone = ""
		if zone in valid_zones:
			return zone, ""
		reason = f"{url} returned no valid zone"
	return None, reason


def enable_ntp():
	run(["timedatectl", "set-ntp", "true"])


# ── Disk and memory ────────────────────────────────────────────────────

def disk_bytes(device):
	return int(_query(["lsblk", "-bdno", "SIZE", device]) or 0)


def ram_kibibytes():
	with open("/proc/meminfo") as meminfo_file:
		return next(int(line.split()[1]) for line in meminfo_file
		            if line.startswith("MemTotal:"))


def ram_mib():
	return ram_kibibytes() // 1024


def _lsblk_entries(disk):
	"""(device, type, size, filesystem) per device on disk. Missing columns are blank."""
	entries = []
	for line in run(["lsblk", "-lnpo", "NAME,TYPE,SIZE,FSTYPE", disk]).stdout.splitlines():
		device, kind, size, filesystem = (line.split() + [""] * 4)[:4]
		entries.append((device, kind, size, filesystem))
	return entries


def get_partitions(disk):
	"""Partition device paths in order, ignoring device-mapper children."""
	return [device for device, kind, _size, _filesystem in _lsblk_entries(disk)
	        if kind == "part"]


def partition_rows(disk):
	"""(device, size, filesystem) per partition on disk."""
	return [(device, size, filesystem or "unformatted")
	        for device, kind, size, filesystem in _lsblk_entries(disk) if kind == "part"]


def partition_table_type(disk):
	"""'gpt', 'dos', or empty if unknown."""
	return run(["blkid", "-s", "PTTYPE", "-o", "value", disk]).stdout.strip()


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


# ── Boot and secure boot ───────────────────────────────────────────────

def largest_free_region(disk):
	"""(start_mib, end_mib) of the largest unallocated gap on disk, or None."""
	gaps = []
	for line in run(["parted", "-sm", disk, "unit", "MiB", "print", "free"]).stdout.splitlines():
		fields = line.rstrip(";").split(":")
		if len(fields) >= 5 and fields[4] == "free":
			start = math.ceil(float(fields[1].removesuffix("MiB")))
			end = math.floor(float(fields[2].removesuffix("MiB")))
			gaps.append((end - start, start, end))
	size, start, end = max(gaps, default=(0, 0, 0))
	return (start, end) if size > 0 else None


def find_esp(disk):
	"""Device of the first FAT partition, the EFI system partition on a UEFI disk, or None."""
	return next((device for device, _size, filesystem in partition_rows(disk)
	             if filesystem == "vfat"), None)


def has_bios_grub(disk):
	return "bios_grub" in run(["parted", "-sm", disk, "print"]).stdout


def detect_boot_mode():
	return "uefi" if os.path.isdir("/sys/firmware/efi") else "bios"


def secure_boot_state():
	return _query(["mokutil", "--sb-state"]).lower()


def secure_boot_enabled():
	return "enabled" in secure_boot_state()


def boot_packages(mode):
	"""Bootloader packages for a boot mode ("uefi" or "bios")."""
	return next((packages.split() for row_mode, packages in _rows(BOOT_PATH)
	             if row_mode == mode), [])


def dkms_packages():
	"""Packages that build modules with DKMS. Signed when Secure Boot is on."""
	return set(read_lines(DKMS_PATH))


def header_packages():
	"""Kernel header packages every install needs for DKMS."""
	return read_lines(HEADERS_PATH)


def mesa_packages():
	"""Mesa packages installed from trixie-backports."""
	return read_lines(MESA_PATH)


def target_image():
	"""Path of the base system squashfs on the live medium, or None."""
	return TARGET_IMAGE if os.path.isfile(TARGET_IMAGE) else None


def target_repo():
	"""Path of the local package repo on the live medium, or None."""
	return TARGET_REPO if os.path.isfile(os.path.join(TARGET_REPO, "Packages.gz")) else None


# ── Preflight ──────────────────────────────────────────────────────────

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
	return Check("warn", "Internet",
	             "No connection. You can still install a console-only system from this "
	             "image. Desktops, extra apps and updates need a connection.",
	             "network")


def _check_memory():
	kibibytes = ram_kibibytes()
	gibibytes = kibibytes / 2**20
	if kibibytes < MIN_RAM_MIB * 1024:
		return Check("block", "Memory",
		             f"This computer has {gibibytes:.1f} GB. Debian needs at least "
		             f"{MIN_RAM_MIB // 1024} GB to install.")
	if kibibytes < WARN_RAM_MIB * 1024:
		return Check("warn", "Memory",
		             f"This computer has {gibibytes:.1f} GB. Install works, but the desktop "
		             f"may feel slow. {WARN_RAM_MIB // 1024} GB or more is recommended.")
	return Check("ok", "Memory", f"{gibibytes:.1f} GB")


def _check_disk(device):
	gibibytes = disk_bytes(device) / 2**30
	if gibibytes < MIN_DISK_GIB:
		return Check("block", "Disk",
		             f"The chosen disk is {gibibytes:.0f} GB. Debian needs at least "
		             f"{MIN_DISK_GIB} GB.", "disk")
	if gibibytes < WARN_DISK_GIB:
		return Check("warn", "Disk",
		             f"The chosen disk is {gibibytes:.0f} GB. Install works, but space will "
		             f"be tight. {WARN_DISK_GIB} GB or more is recommended.", "disk")
	return Check("ok", "Disk", f"{gibibytes:.0f} GB")


def _check_secure_boot():
	if detect_boot_mode() != "uefi":
		return Check("ok", "Secure Boot", "not applicable")
	state = secure_boot_state()
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


def _check_image():
	if target_image() and target_repo():
		return Check("ok", "Install image", "found")
	return Check("block", "Install image",
	             "The base system image or package repo is missing from the install "
	             "medium. Download the ISO again.")


def preflight(config, online):
	"""List of Check objects. Only level "block" stops Install."""
	checks = [_check_network(online), _check_memory(), _check_secure_boot()]
	if online:  # an unsynced clock only matters for downloads
		checks.append(_check_clock())
	if not config.fake_mode:
		checks.append(_check_image())
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


# ── Package lists ──────────────────────────────────────────────────────

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


@functools.lru_cache(maxsize=None)
def read_lines(path):
	"""Non-blank, non-comment lines of a list file, as a tuple."""
	with open(path) as list_file:
		return tuple(line.strip() for line in list_file
		             if line.strip() and not line.startswith("#"))


def _rows(path):
	"""Each list line split on '|' into stripped fields."""
	return [tuple(value.strip() for value in line.split("|"))
	        for line in read_lines(path)]


def optional_rows():
	"""(package, config flag) per optional package."""
	return _rows(OPTIONAL_PATH)


def custom_rows():
	"""(id, menu, section, label, script, requires) per script-installed package."""
	return _rows(CUSTOM_PATH)


def wm_rows():
	"""(id, label, repo, ref, description) per window manager, with local repos resolved."""
	rows = []
	for wm_id, label, repo, ref, description in _rows(WMS_PATH):
		if repo.startswith("/"):
			repo = os.path.join(WM_DIR, repo.lstrip("/"))
		rows.append((wm_id, label, repo, ref, description))
	return rows


def extras_rows():
	"""(menu, section, package, label, requires) per optional package."""
	return [row[:5] for row in _rows(EXTRAS_PATH)]


@functools.lru_cache(maxsize=None)
def baked_packages():
	"""Package names shipped in the ISO's local repo: extras.list rows flagged 'bake'."""
	return {word for row in _rows(EXTRAS_PATH)
	        if len(row) > 5 and row[5] == "bake" for word in row[2].split()}


def needs_network(package):
	"""True unless every package in this entry is already in the install image."""
	baked = baked_packages()
	return not all(word in baked for word in package.split())


def components_rows():
	"""(id, label, description) per apt component."""
	return _rows(COMPONENTS_PATH)


def package_requirements():
	"""Package id -> required archive id, or '-' when none."""
	requirements = {package: requires
	                for _menu, _section, package, _label, requires in extras_rows()}
	requirements.update({custom_id: requires
	                     for custom_id, _menu, _section, _label, _script, requires in custom_rows()})
	return requirements


PROFILE = load_profile(os.path.join(ROOT_DIR, "profile.env"))
MIRROR = PROFILE["MIRROR"]


# ── Keyboard ───────────────────────────────────────────────────────────

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


# ── Network ────────────────────────────────────────────────────────────

def is_online():
	if run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"]).stdout.strip() == "full":
		return True
	return run("ping -c 1 -W 1 1.1.1.1").returncode == 0


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


def wifi_connect(ssid, password, security=""):
	"""(success, message). Password is None for open networks."""
	name = re.sub(r"[^A-Za-z0-9_-]", "_", ssid)
	path = os.path.join("/etc/NetworkManager/system-connections", f"{name}.nmconnection")
	lines = [
		"[connection]", f"id={ssid}", "type=wifi", "",
		"[wifi]", f"ssid={ssid}", "mode=infrastructure", "",
	]
	if password:
		key_lines = ["key-mgmt=sae", "pmf=3"] if "WPA3" in security.upper() else ["key-mgmt=wpa-psk"]
		lines += ["[wifi-security]", *key_lines, f"psk={password}", ""]

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
