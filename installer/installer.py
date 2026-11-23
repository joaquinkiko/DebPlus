# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import glob
import json
import math
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import termios
import textwrap
import threading
import time

from . import utils
from .app import LOGO
from .config import Config
from .utils import (MNT, PROFILE, BIOS_BOOT_END_MIB, BOOT_PARTITION_GIB, ESP_END_MIB,
                    get_partitions, keyboard_config)

BOLD = "\033[1m"
RED_BOLD = "\033[1;31m"
GREEN_BOLD = "\033[1;32m"
NC = "\033[0m"

SUITE = PROFILE["SUITE"]
BACKPORTS = f"{SUITE}-backports"
KEYRING_PATH = "/usr/share/keyrings/debian-archive-keyring.gpg"
KEYRING_LINE = f"Signed-By: {KEYRING_PATH}"
SECURITY_MIRROR = "http://security.debian.org/debian-security"
EFIVARS_HOST = "/sys/firmware/efi/efivars"
BOOT_UEFI = "uefi"
BOOT_BIOS = "bios"

GRUB_MODULES = ("part_gpt part_msdos ext2 btrfs xfs search search_fs_uuid normal")

PARTED_FS_HINT = {"btrfs": "btrfs", "ext4": "ext4", "xfs": "ext2"}
MKFS_COMMANDS = {
	"btrfs": ["mkfs.btrfs", "-f"],
	"xfs": ["mkfs.xfs", "-f"],
	"ext4": ["mkfs.ext4", "-qF"],
}
FSTAB_OPTIONS = {
	"btrfs": "defaults,compress=zstd,noatime",
	"ext4": "errors=remount-ro",
	"xfs": "defaults",
}
FIRST_PARTITION_START = "1MiB"

LOG_PATH = "/var/log/debplus/install.log"
STATE_PATH = "/run/debplus/state"

LUKS_ROOT = "cryptroot"
LUKS_SWAP = "cryptswap"

LOCAL_REPO_MOUNT = "/media/debplus-repo"  # inside the target, only during install
APT_COMMAND = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
# Offline apt reads only the local repo. Check-Date is off because an offline RTC is often wrong.
LOCAL_APT_OPTIONS = [
	"-o", "Dir::Etc::sourcelist=debplus-local.list",
	"-o", "Dir::Etc::sourceparts=-",
	"-o", "APT::Sandbox::User=root",
	"-o", "Acquire::Check-Date=false",
]

PROGRESS_BAR_WIDTH = 40
SPINNER_FRAMES = "|/-\\"
SPINNER_INTERVAL = 0.12
REBOOT_DELAY_SECONDS = 30


class Ui:
	"""Full-screen install view: centred logo, progress bar, current step with spinner, and messages."""

	def __init__(self):
		self._lock = threading.Lock()
		self._total = 0
		self._done = 0
		self._stage = ""
		self._sub = ""
		self._message = []
		self._spinning = False
		self._tick = 0
		self._spin_stop = threading.Event()
		self._spin_thread = None

	def _layout(self):
		size = shutil.get_terminal_size()
		cols, rows = size.columns, size.lines
		logo = [line.rstrip() for line in LOGO.splitlines()]
		if cols < max(len(line) for line in logo) + 2:
			logo = []
		logo_width = max((len(line) for line in logo), default=0)
		top = max(1, (rows - len(logo) - 12) // 2)
		return {
			"cols": cols,
			"logo": logo,
			"logo_col": max(1, (cols - logo_width) // 2 + 1),
			"top": top,
			"bar": top + len(logo) + 1,
			"stage": top + len(logo) + 3,
			"sub": top + len(logo) + 4,
			"message": top + len(logo) + 6,
		}

	def _put(self, layout, row, text, code=""):
		"""Write text centred on row. Returns the column just after it."""
		col = max(1, (layout["cols"] - len(text)) // 2 + 1)
		sys.stdout.write(f"\x1b[{row};1H\x1b[2K\x1b[{row};{col}H{code}{text}{NC}")
		return col + len(text)

	def _draw_bar_locked(self, layout):
		width = PROGRESS_BAR_WIDTH
		filled = self._done * width // self._total if self._total else 0
		step = min(self._done + 1, self._total)
		self._put(layout, layout["bar"], f"[{'#' * filled}{'-' * (width - filled)}]  {step}/{self._total}")

	def _draw_sub_locked(self):
		layout = self._layout()
		text = self._sub
		if self._spinning:
			text = f"{SPINNER_FRAMES[self._tick % len(SPINNER_FRAMES)]} {text}"
		self._put(layout, layout["sub"], text)
		sys.stdout.flush()

	def _draw_locked(self):
		layout = self._layout()
		sys.stdout.write("\x1b[2J\x1b[H")
		for index, line in enumerate(layout["logo"]):
			sys.stdout.write(f"\x1b[{layout['top'] + index};{layout['logo_col']}H{line}")
		self._draw_bar_locked(layout)
		self._put(layout, layout["stage"], self._stage, BOLD)
		self._put(layout, layout["sub"], self._sub)
		for index, entry in enumerate(self._message):
			code, text = entry if isinstance(entry, tuple) else ("", entry)
			self._put(layout, layout["message"] + index, text, code)
		sys.stdout.flush()

	def start(self, total):
		with self._lock:
			self._total = total
			self._done = 0
			self._draw_locked()

	def begin_stage(self, label):
		with self._lock:
			self._stage = label
			self._sub = ""
			self._draw_locked()

	def finish_stage(self):
		with self._lock:
			self._done += 1
			self._draw_bar_locked(self._layout())
			sys.stdout.flush()

	def set_sub(self, text):
		with self._lock:
			self._sub = text
			self._draw_sub_locked()

	def current_sub(self):
		with self._lock:
			return self._sub

	def show_message(self, lines):
		"""Centred lines under the progress view. Each entry is text or (ansi_code, text)."""
		with self._lock:
			self._message = lines
			self._draw_locked()

	def redraw(self):
		with self._lock:
			self._draw_locked()

	def prompt(self, question, hint=""):
		"""Centred question under the messages, with the cursor left after it for input()."""
		with self._lock:
			layout = self._layout()
			row = layout["message"] + len(self._message) + 1
			end_col = self._put(layout, row, question)
			if hint:
				self._put(layout, row + 1, hint)
			sys.stdout.write(f"\x1b[{row};{end_col}H")
			sys.stdout.flush()

	def start_spinner(self):
		if self._spin_thread:
			return
		self._spin_stop.clear()
		self._spinning = True
		self._spin_thread = threading.Thread(target=self._spin, daemon=True)
		self._spin_thread.start()

	def _spin(self):
		while not self._spin_stop.wait(SPINNER_INTERVAL):
			with self._lock:
				self._tick += 1
				self._draw_sub_locked()

	def stop_spinner(self):
		if not self._spin_thread:
			return
		self._spin_stop.set()
		self._spin_thread.join()
		self._spin_thread = None
		with self._lock:
			self._spinning = False
			self._draw_sub_locked()


ui = Ui()


# ── Output and command helpers ─────────────────────────────────────────

def _log(text):
	os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
	with open(LOG_PATH, "a") as log_file:
		log_file.write(text + "\n")


def msg(text):
	ui.set_sub(text)
	_log(f":: {text}")


def ok(text):
	_log(f"   {text}")


class StageError(Exception):
	"""Raised inside an install stage. The runner decides retry, shell, reboot, or abort."""


def die(text):
	raise StageError(text)


def _execute(argv):
	"""Run argv with its output going to the install log. Returns the exit status."""
	_log(f"$ {shlex.join(argv)}")
	with open(LOG_PATH, "a") as log_file:
		return subprocess.run(argv, stdout=log_file, stderr=subprocess.STDOUT).returncode


def _run(argv):
	"""Run argv, raising StageError on failure."""
	if _execute(argv) != 0:
		die(f"Command failed: {shlex.join(argv)}")


def _try(argv):
	"""Run argv and ignore failure."""
	subprocess.run(argv, capture_output=True)


def _chroot(argv):
	_run(["chroot", MNT, *argv])


def _chroot_ok(argv):
	"""Run in the chroot, returning success instead of raising."""
	return _execute(["chroot", MNT, *argv]) == 0


def _apt(arguments):
	_chroot([*APT_COMMAND, *arguments])


def _unmount(path, lazy=False, recursive=False):
	flags = (["-R"] if recursive else []) + (["-l"] if lazy else [])
	_try(["umount", *flags, path])


def _remove(path):
	"""Delete a file, ignoring it if it is missing."""
	try:
		os.remove(path)
	except OSError:
		pass


def _clear_directory(path):
	"""Delete everything inside a directory, keeping the directory itself."""
	for entry in glob.glob(os.path.join(path, "*")):
		if os.path.isdir(entry) and not os.path.islink(entry):
			shutil.rmtree(entry, ignore_errors=True)
		else:
			_remove(entry)


def _report(label, succeeded, hint):
	"""Record an optional step's outcome in the log. Failure is collected by the caller."""
	if succeeded:
		ok(f"{label} installed")
		return True
	_log(f"{label} failed. {hint}")
	return False


def _write_target(path, content, append=False, permissions=None):
	"""Write a file inside the target. path is absolute within the target."""
	with open(f"{MNT}{path}", "a" if append else "w") as target_file:
		target_file.write(content)
	if permissions is not None:
		os.chmod(f"{MNT}{path}", permissions)


def _blkid(device, field):
	return subprocess.run(["blkid", "-s", field, "-o", "value", device],
	                      capture_output=True, text=True).stdout.strip()


# ── Partitioning ───────────────────────────────────────────────────────

def _make_filesystem(device, filesystem):
	_run([*MKFS_COMMANDS[filesystem], device])


def _wait_for_partitions(disk, expected, attempts=10):
	for _ in range(attempts):
		_try(["partprobe", disk])
		parts = get_partitions(disk)
		if len(parts) >= expected:
			return parts
		time.sleep(0.5)
	die(f"Expected {expected} partitions on {disk}, found {len(parts)}")


def _boot_partition(uefi):
	"""Roles, parted args and end in MiB of the boot partition, which always starts the disk."""
	if uefi:
		return (["esp"],
		        ["mkpart", "ESP", "fat32", FIRST_PARTITION_START, f"{ESP_END_MIB}MiB",
		         "set", "1", "esp", "on"],
		        ESP_END_MIB)
	return (["bios_grub"],
	        ["mkpart", "bios_grub", FIRST_PARTITION_START, f"{BIOS_BOOT_END_MIB}MiB",
	         "set", "1", "bios_grub", "on"],
	        BIOS_BOOT_END_MIB)


def _apply_partitions(config: Config, roles, parted_args):
	_run(["wipefs", "-af", config.disk])
	_run(["parted", "-s", config.disk, *parted_args])
	partitions = _wait_for_partitions(config.disk, len(roles))
	config.layout = {role: {"device": device, "format": role != "bios_grub"}
	                 for role, device in zip(roles, partitions)}


def _partition_auto(config: Config):
	disk_mib = utils.disk_bytes(config.disk) // 2**20
	ram_gib = math.ceil(utils.ram_mib() / 1024)
	swap_start = f"{disk_mib - config.auto_swap_gib(ram_gib, config.swap_size) * 1024}MiB"
	boot_roles, boot_args, boot_end_mib = _boot_partition(config.boot_mode == BOOT_UEFI)

	roles = [*boot_roles]
	parted_args = ["mklabel", "gpt", *boot_args]
	if config.luks:
		split_mib = boot_end_mib + BOOT_PARTITION_GIB * 1024
		parted_args += ["mkpart", "boot", "ext4", f"{boot_end_mib}MiB", f"{split_mib}MiB"]
		roles.append("boot")
		boot_end_mib = split_mib
	parted_args += ["mkpart", "root", PARTED_FS_HINT[config.filesystem],
	                f"{boot_end_mib}MiB", swap_start]
	roles.append("root")
	roles.append("swap")
	parted_args += ["mkpart", "swap", "linux-swap", swap_start, "100%"]
	_apply_partitions(config, roles, parted_args)


def _custom_segments(config: Config, first_mib, last_mib, disk_mib):
	"""(role, parted type, start, end) for custom sizes filling first_mib to last_mib. Swap sits last."""
	boot_segments = []
	if config.luks:
		boot_end_mib = first_mib + BOOT_PARTITION_GIB * 1024
		boot_segments = [("boot", "ext4", f"{first_mib}MiB", f"{boot_end_mib}MiB")]
		first_mib = boot_end_mib
	swap_gib = config.custom_swap_gib
	root_gib = config.custom_root_gib
	home_gib = config.custom_home_gib
	avail_end_mib = last_mib - swap_gib * 1024

	def end_arg(end_mib):
		return "100%" if end_mib == last_mib == disk_mib else f"{end_mib}MiB"

	if root_gib:
		root_end_mib = first_mib + root_gib * 1024
	elif home_gib:
		root_end_mib = avail_end_mib - home_gib * 1024
	else:
		root_end_mib = avail_end_mib
	fs_hint = PARTED_FS_HINT[config.filesystem]
	segments = [("root", fs_hint, f"{first_mib}MiB", end_arg(root_end_mib))]
	if root_gib or home_gib:
		home_end_mib = root_end_mib + home_gib * 1024 if home_gib else avail_end_mib
		segments.append(("home", fs_hint, f"{root_end_mib}MiB", end_arg(home_end_mib)))
	if swap_gib:
		segments.append(("swap", "linux-swap", f"{avail_end_mib}MiB", end_arg(last_mib)))
	return boot_segments + segments


def _partition_custom(config: Config):
	boot_roles, boot_args, boot_end_mib = _boot_partition(config.boot_mode == BOOT_UEFI)
	disk_mib = utils.disk_bytes(config.disk) // 2**20
	segments = _custom_segments(config, boot_end_mib, disk_mib, disk_mib)
	roles = [*boot_roles, *(role for role, *_rest in segments)]
	_apply_partitions(config, roles, ["mklabel", "gpt", *boot_args, *_mkpart_args("gpt", segments)])


def _mkpart_args(label, segments):
	"""parted mkpart arguments for (role, type, start, end) segments. MBR has no names, so each is primary."""
	args = []
	for role, parted_type, start, end in segments:
		name = role if label == "gpt" else "primary"
		args += ["mkpart", name, parted_type, start, end]
	return args


def _wait_for_new_partitions(disk, existing, expected, attempts=10):
	for _ in range(attempts):
		_try(["partprobe", disk])
		new = [part for part in get_partitions(disk) if part not in existing]
		if len(new) >= expected:
			return new[:expected]
		time.sleep(0.5)
	die(f"Expected {expected} new partitions on {disk}")


def _partition_free_space(config: Config):
	region = utils.largest_free_region(config.disk)
	if region is None:
		die("No unallocated space on the disk.")
	start_mib, end_mib = region
	disk_mib = utils.disk_bytes(config.disk) // 2**20
	esp = utils.find_esp(config.disk) if config.boot_mode == BOOT_UEFI else None
	if config.boot_mode == BOOT_UEFI and not esp:
		die("No EFI system partition found on the disk.")
	label = utils.partition_table_type(config.disk)
	segments = _custom_segments(config, start_mib, end_mib, disk_mib)
	existing = get_partitions(config.disk)
	_run(["parted", "-s", config.disk, *_mkpart_args(label, segments)])
	created = _wait_for_new_partitions(config.disk, existing, len(segments))
	config.layout = {role: {"device": device, "format": True}
	                 for (role, *_rest), device in zip(segments, created)}
	if esp:
		config.layout["esp"] = {"device": esp, "format": False}


def _format_layout(config: Config):
	msg("Formatting...")
	for role, part in config.layout.items():
		if not part["format"]:
			continue
		device = _device(config, role)
		if role == "esp":
			_run(["mkfs.fat", "-F32", device])
		elif role == "boot":
			_run(["mkfs.ext4", "-qF", device])
		elif role == "swap":
			_run(["mkswap", device])
		elif role in ("root", "home"):
			_make_filesystem(device, config.filesystem)

def mount_boot(config: Config):
	"""Mount the unencrypted /boot before extraction so the kernel lands on it."""
	if "boot" not in config.layout:
		return
	os.makedirs(f"{MNT}/boot", exist_ok=True)
	_run(["mount", config.layout["boot"]["device"], f"{MNT}/boot"])


def _mount_layout(config: Config):
	root = _device(config, "root")
	btrfs = config.filesystem == "btrfs"
	os.makedirs(MNT, exist_ok=True)
	if btrfs:
		_run(["mount", root, MNT])
		_run(["btrfs", "subvolume", "create", f"{MNT}/@"])
		if "home" not in config.layout:
			_run(["btrfs", "subvolume", "create", f"{MNT}/@home"])
		_run(["umount", MNT])
	subvolume_option = ["-o", "subvol=@"] if btrfs else []
	_run(["mount", *subvolume_option, root, MNT])


def mount_secondary(config: Config):
	"""Mount /home and the ESP. Runs after extraction so the image never writes onto them."""
	layout = config.layout
	home_target = f"{MNT}/home"
	if not os.path.ismount(home_target):
		if "home" in layout:
			home_source = [layout["home"]["device"]]
		elif config.filesystem == "btrfs":
			home_source = ["-o", "subvol=@home", _device(config, "root")]
		else:
			home_source = None
		if home_source:
			os.makedirs(home_target, exist_ok=True)
			_run(["mount", *home_source, home_target])

	esp_target = f"{MNT}/boot/efi"
	if "esp" in layout and not os.path.ismount(esp_target):
		os.makedirs(esp_target, exist_ok=True)
		_run(["mount", layout["esp"]["device"], esp_target])


def _device(config: Config, role):
	"""Device to format or mount for a role. Encrypted root and swap use their open mapper."""
	if config.luks and role == "root":
		return f"/dev/mapper/{LUKS_ROOT}"
	if config.luks and role == "swap":
		return f"/dev/mapper/{LUKS_SWAP}"
	return config.layout[role]["device"]


def _cryptsetup(args, passphrase=None):
	result = subprocess.run(["cryptsetup", *args], input=passphrase,
	                        text=True, capture_output=True)
	if result.returncode != 0:
		die(f"cryptsetup {args[0]} failed: {result.stderr.strip()}")


def _close_mappers():
	"""Release LUKS mappers left open by an earlier attempt."""
	for mapper in (LUKS_ROOT, LUKS_SWAP):
		_try(["cryptsetup", "close", mapper])


def _encrypt_layout(config: Config):
	for role, mapper in (("root", LUKS_ROOT), ("swap", LUKS_SWAP)):
		if role not in config.layout:
			continue
		device = config.layout[role]["device"]
		msg(f"Encrypting {role} ({device})")
		_cryptsetup(["luksFormat", "--type", "luks2", "--pbkdf", "pbkdf2",
		             "--batch-mode", "--key-file=-", device], config.luks_pass)
		_cryptsetup(["open", "--key-file=-", device, mapper], config.luks_pass)


def partition_disk(config: Config):
	_unmount(MNT, lazy=True, recursive=True)
	_close_mappers()
	if config.manual:
		msg("Using selected partitions")
	else:
		msg(f"Partitioning {config.disk}")
		if config.free_space:
			_partition_free_space(config)
		elif config.custom:
			_partition_custom(config)
		else:
			_partition_auto(config)
	config.dual_boot = config.free_space or config.shared_esp()
	if config.luks:
		_encrypt_layout(config)
	_format_layout(config)
	msg("Mounting filesystems")
	_mount_layout(config)
	ok("Partitioned and mounted")


def _fstab_rows(config: Config):
	layout = config.layout
	root = _device(config, "root")
	btrfs = config.filesystem == "btrfs"
	rows = [(root, "/", config.filesystem,
	         FSTAB_OPTIONS[config.filesystem] + (",subvol=@" if btrfs else ""), 1)]
	if "home" in layout:
		home_device = layout["home"]["device"]
		home_type = _blkid(home_device, "TYPE")
		rows.append((home_device, "/home", home_type,
		             FSTAB_OPTIONS.get(home_type, "defaults"), 2))
	elif btrfs:
		rows.append((root, "/home", "btrfs",
		             FSTAB_OPTIONS["btrfs"] + ",subvol=@home", 2))
	if "boot" in layout:
		rows.append((layout["boot"]["device"], "/boot", "ext4", "errors=remount-ro", 2))
	if "esp" in layout:
		rows.append((layout["esp"]["device"], "/boot/efi", "vfat", "umask=0077", 2))
	if "swap" in layout:
		rows.append((_device(config, "swap"), "none", "swap", "sw", 0))
	return rows


def write_fstab(config: Config):
	lines = ["# <filesystem>  <mount>  <type>  <options>  <dump>  <pass>"]
	for device, mount_point, filesystem, options, pass_number in _fstab_rows(config):
		spec = device if device.startswith("/dev/mapper/") else f"UUID={_blkid(device, 'UUID')}"
		lines.append(f"{spec}  {mount_point}  {filesystem}  {options}  0  {pass_number}")
	_write_target("/etc/fstab", "\n".join(lines) + "\n")


# ── Chroot mounts ──────────────────────────────────────────────────────

def mount_chroot_fs():
	for argv in (
		["mount", "--bind", "/dev", f"{MNT}/dev"],
		["mount", "--bind", "/dev/pts", f"{MNT}/dev/pts"],
		["mount", "-t", "proc", "proc", f"{MNT}/proc"],
		["mount", "-t", "sysfs", "sys", f"{MNT}/sys"],
	):
		_run(argv)
	if os.path.isdir(EFIVARS_HOST):
		_try(["mount", "--bind", EFIVARS_HOST, f"{MNT}/sys/firmware/efi/efivars"])


# ── Apt sources ────────────────────────────────────────────────────────

def _apt_stanza(uri, suites, components):
	return f"Types: deb\nURIs: {uri}\nSuites: {suites}\nComponents: {components}\n{KEYRING_LINE}\n"


def _write_backports_pins(config: Config):
	"""Keep backports opt-in: pin it low, then raise only the packages the user ticked."""
	pin_blocks = [f"Package: *\nPin: release a={BACKPORTS}\nPin-Priority: 100\n"]
	if config.backports_kernel:
		pin_blocks.append(f"Package: linux-image-amd64 linux-headers-amd64\n"
		                  f"Pin: release a={BACKPORTS}\nPin-Priority: 990\n")
	if config.backports_mesa:
		# Glob catches the Mesa libraries and drivers. libgbm1 has no "mesa" in its name.
		pin_blocks.append(f"Package: *mesa* libgbm1\n"
		                  f"Pin: release a={BACKPORTS}\nPin-Priority: 990\n")
	os.makedirs(f"{MNT}/etc/apt/preferences.d", exist_ok=True)
	_write_target("/etc/apt/preferences.d/backports", "\n".join(pin_blocks))


def write_apt_sources(config: Config):
	components = " ".join(["main"] + config.components)
	stanzas = [
		_apt_stanza(config.mirror, f"{SUITE} {SUITE}-updates", components),
		_apt_stanza(SECURITY_MIRROR, f"{SUITE}-security", components),
	]
	if config.backports_repo or config.backports_kernel or config.backports_mesa:
		stanzas.append(_apt_stanza(config.mirror, BACKPORTS, components))
		_write_backports_pins(config)
	_write_target("/etc/apt/sources.list.d/debian.sources", "\n".join(stanzas))
	_write_target("/etc/apt/sources.list", "")


# ── Base system ────────────────────────────────────────────────────────

def packages_to_install(config: Config) -> list:
	"""Packages beyond the base image: bootloader, chosen options, shipped extras."""
	wanted = set(utils.boot_packages(config.boot_mode))
	wanted |= {package for package, flag in utils.optional_rows() if getattr(config, flag)}
	if not config.backports_kernel:  # backports path installs matching headers later
		wanted |= set(utils.header_packages())
	for entry in config.selected_packages():
		if not utils.needs_network(entry):  # shipped on the ISO, so install_extras skips it
			wanted |= set(entry.split())
	return sorted(wanted)


def _unmount_all():
	"""Release chroot and secondary mounts so a retried extraction starts clean."""
	for sub in ("sys/firmware/efi/efivars", "sys", "proc", "dev/pts", "dev", "boot/efi", "boot", "home"):
		_unmount(f"{MNT}/{sub}", lazy=True)


def reset_identity():
	"""The image is shared by every install, so give this one its own identity."""
	msg("Resetting machine identity")
	_write_target("/etc/machine-id", "")  # empty: systemd generates a new id on first boot
	_remove(f"{MNT}/var/lib/dbus/machine-id")
	try:
		os.symlink("/etc/machine-id", f"{MNT}/var/lib/dbus/machine-id")
	except OSError:
		pass
	for key in glob.glob(f"{MNT}/etc/ssh/ssh_host_*"):
		_remove(key)
	_try(["chroot", MNT, "sh", "-c",
	      "dpkg -s openssh-server >/dev/null 2>&1 && "
	      "dpkg-reconfigure -f noninteractive openssh-server"])


def _install_from_local_repo(packages):
	"""Offline install: apt reads only the repo bind-mounted from the install medium."""
	repo = utils.target_repo()
	if not repo:
		die("Package repo not found on the install medium.")
	msg("Installing packages from the install medium (apt)")
	mount_point = f"{MNT}{LOCAL_REPO_MOUNT}"
	os.makedirs(mount_point, exist_ok=True)
	_run(["mount", "--bind", repo, mount_point])
	try:
		_write_target("/etc/apt/debplus-local.list", f"deb [trusted=yes] file:{LOCAL_REPO_MOUNT} ./\n")
		_apt([*LOCAL_APT_OPTIONS, "update"])
		_apt([*LOCAL_APT_OPTIONS, "install", "-y", "--no-install-recommends", *packages])
	finally:
		_unmount(mount_point)
		_try(["rmdir", mount_point])
		_remove(f"{MNT}/etc/apt/debplus-local.list")
		_clear_directory(f"{MNT}/var/lib/apt/lists")


def install_packages(config: Config):
	"""Install the packages this install needs, then bring the system up to date if online."""
	packages = packages_to_install(config)
	if config.boot_mode == BOOT_BIOS:
		# Empty install_devices keeps grub-pc from prompting. The bootloader stage installs GRUB.
		_chroot(["sh", "-c", "echo 'grub-pc grub-pc/install_devices string' | debconf-set-selections"])
	if not config.online:
		if packages:
			_install_from_local_repo(packages)
		return

	msg("Getting package lists (apt update)")
	_apt(["update"])
	if packages:
		msg("Installing packages (apt)")
		_apt(["install", "-y", "--no-install-recommends", *packages])
	msg("Upgrading installed packages (apt)")
	_apt(["upgrade", "-y"])
	if config.backports_kernel:
		msg("Installing backports kernel (apt)")
		_apt(["install", "-y", "-t", BACKPORTS, "linux-image-amd64", "linux-headers-amd64"])
	if config.backports_mesa:
		msg("Installing backports Mesa (apt)")
		_apt(["install", "-y", "-t", BACKPORTS, *utils.mesa_packages(config.multiarch_i386)])
	_chroot(["apt-get", "clean"])


def install_base(config: Config):
	image = utils.target_image()
	if not image:
		die("Base system image not found on the install medium.")
	_unmount_all()
	mount_boot(config)
	msg("Extracting base system")
	_run(["unsquashfs", "-f", "-n", "-d", MNT, image])
	_remove(f"{MNT}/etc/resolv.conf")
	shutil.copy2("/etc/resolv.conf", f"{MNT}/etc/resolv.conf")
	mount_secondary(config)
	msg("Configuring apt sources")
	write_apt_sources(config)
	mount_chroot_fs()
	reset_identity()
	if config.multiarch_i386:
		msg("Enabling 32-bit (i386) architecture")
		_chroot(["dpkg", "--add-architecture", "i386"])
	install_packages(config)
	ok("Base system installed")


# ── System configuration ──────────────────────────────────────────────

def configure_encryption(config: Config):
	msg("Configuring encryption...")
	root_uuid = _blkid(config.layout["root"]["device"], "UUID")
	lines = [f"{LUKS_ROOT} UUID={root_uuid} none luks,tries=5"]
	if "swap" in config.layout:
		# initramfs unlock: resume reads swap before root is mounted
		swap_uuid = _blkid(config.layout["swap"]["device"], "UUID")
		lines.append(f"{LUKS_SWAP} UUID={swap_uuid} none luks,tries=5,initramfs")
	_write_target("/etc/crypttab", "\n".join(lines) + "\n")
	ok("Encryption configured")


TIMESHIFT_EXCLUDES = ["/home/**", "/root/**", "/var/log/**", "/var/tmp/**", "/tmp/**",
                      "/var/cache/apt/archives/**", "/var/lib/apt/lists/**"]


def configure_timeshift(config: Config):
	msg("Configuring Timeshift")
	settings = {
		"backup_device_uuid": _blkid(_device(config, "root"), "UUID"),
		"parent_device_uuid": _blkid(config.layout["root"]["device"], "UUID") if config.luks else "",
		"do_first_run": False,
		"btrfs_mode": config.timeshift_btrfs,
		"include_btrfs_home_for_backup": False,
		"include_btrfs_home_for_restore": False,
		"stop_cron_emails": True,
		"schedule_monthly": False,
		"schedule_weekly": config.timeshift_auto,
		"schedule_daily": False,
		"schedule_hourly": False,
		"schedule_boot": False,
		"count_daily": 3,
		"count_weekly": 3,
		"count_monthly": 3,
		"exclude": TIMESHIFT_EXCLUDES,
		"exclude-apps": [],
	}
	os.makedirs(f"{MNT}/etc/timeshift", exist_ok=True)
	_write_target("/etc/timeshift/timeshift.json", json.dumps(settings, indent="\t") + "\n")
	if config.timeshift_auto:
		_write_target("/etc/systemd/system/timeshift-autosnap.service",
		              "[Unit]\nDescription=Timeshift scheduled snapshot check\n\n"
		              "[Service]\nType=oneshot\nExecStart=/usr/bin/timeshift --check\n")
		_write_target("/etc/systemd/system/timeshift-autosnap.timer",
		              "[Unit]\nDescription=Hourly Timeshift snapshot check\n\n"
		              "[Timer]\nOnCalendar=hourly\nPersistent=true\n\n"
		              "[Install]\nWantedBy=timers.target\n")
		_chroot(["systemctl", "enable", "timeshift-autosnap.timer"])
	ok("Timeshift configured")


def take_first_snapshot() -> bool:
	msg("Creating first Timeshift snapshot")
	if _chroot_ok(["timeshift", "--create", "--scripted",
	               "--comments", "Fresh install", "--tags", "O"]):
		ok("First Timeshift snapshot created")
		return True
	_log("First Timeshift snapshot failed. Run: sudo timeshift --create")
	return False


def configure_auto_updates():
	msg("Enabling security updates")
	_write_target("/etc/apt/apt.conf.d/20auto-upgrades",
	              'APT::Periodic::Update-Package-Lists "1";\n'
	              'APT::Periodic::Unattended-Upgrade "1";\n')
	ok("Security updates enabled")


def enable_locale(locale_name):
	"""Uncomment the chosen locale in the target's locale.gen."""
	path = f"{MNT}/etc/locale.gen"
	with open(path) as locale_file:
		text = locale_file.read()
	text = re.sub(rf"^# *{re.escape(locale_name)}", locale_name, text, flags=re.MULTILINE)
	with open(path, "w") as locale_file:
		locale_file.write(text)


def _swap_uuid(config: Config):
	"""UUID of the swap filesystem, read from the open mapper when LUKS is on."""
	return _blkid(_device(config, "swap"), "UUID")


def configure_resume(config: Config):
	if "swap" not in config.layout:
		return
	os.makedirs(f"{MNT}/etc/initramfs-tools/conf.d", exist_ok=True)
	_write_target("/etc/initramfs-tools/conf.d/resume", f"RESUME=UUID={_swap_uuid(config)}\n")
	ok("Hibernation resume target set")


def configure_system(config: Config):
	msg("Writing system configuration")
	write_fstab(config)
	_write_target("/etc/hostname", config.hostname + "\n")
	_write_target("/etc/hosts",
	              "127.0.0.1   localhost\n"
	              f"127.0.1.1   {config.hostname}\n"
	              "\n"
	              "::1         localhost ip6-localhost ip6-loopback\n"
	              "ff02::1     ip6-allnodes\n"
	              "ff02::2     ip6-allrouters\n")

	msg("Generating locale")
	enable_locale(config.locale)
	_chroot(["locale-gen"])
	_write_target("/etc/default/locale", f"LANG={config.locale}\n")

	_chroot(["ln", "-sf", f"/usr/share/zoneinfo/{config.timezone}", "/etc/localtime"])
	_write_target("/etc/timezone", config.timezone + "\n")
	_write_target("/etc/default/keyboard", keyboard_config(config.keymap))
	_write_target("/etc/sysctl.d/99-swappiness.conf", f"vm.swappiness={config.swappiness}\n")

	_chroot(["systemctl", "enable", "NetworkManager", "systemd-timesyncd"])
	copy_wifi_profiles()

	if config.luks:
		configure_encryption(config)
	configure_resume(config)
	if config.auto_updates:
		configure_auto_updates()

	msg("Rebuilding initramfs")  # fstab and crypttab exist now
	_chroot(["update-initramfs", "-u", "-k", "all"])
	ok("System configured")


def copy_wifi_profiles():
	"""Carry live Wi-Fi profiles into the target so the first boot connects."""
	source_dir = "/etc/NetworkManager/system-connections"
	if not os.path.isdir(source_dir):
		return
	target_dir = f"{MNT}{source_dir}"
	os.makedirs(target_dir, exist_ok=True)
	for name in os.listdir(source_dir):
		source_path = os.path.join(source_dir, name)
		try:
			with open(source_path) as profile_file:
				content = profile_file.read()
		except OSError:
			continue
		if "type=wifi" not in content and "type=802-11-wireless" not in content:
			continue
		target_path = os.path.join(target_dir, name)
		shutil.copy2(source_path, target_path)
		os.chmod(target_path, 0o600)


def _chpasswd(line):
	subprocess.run(["chroot", MNT, "chpasswd"], input=f"{line}\n", text=True, check=True)


def create_users(config: Config):
	msg("Creating users")
	_chroot(["groupadd", "-f", "lpadmin"])
	user_known = subprocess.run(["chroot", MNT, "id", config.username],
	                            capture_output=True).returncode == 0
	if not user_known:
		_chroot(["useradd", "-m", "-G", "sudo,audio,video,netdev,lpadmin",
		         "-s", "/bin/bash", config.username])
	_chpasswd(f"{config.username}:{config.user_pass}")
	if config.root_pass:
		_chpasswd(f"root:{config.root_pass}")
	else:
		_chroot(["passwd", "-l", "root"])
	ok("Users created")


# ── Desktop, window manager, optional packages ────────────────────────

def _fetch_wm_source(repo, ref, clone_path):
	"""Place window manager source at clone_path inside the target. Returns success."""
	if repo.startswith("/"):
		try:
			shutil.copytree(repo, f"{MNT}{clone_path}", dirs_exist_ok=True)
		except OSError as error:
			_log(f"Cannot copy {repo}: {error}")
			return False
		return True
	branch = [] if ref == "-" else ["--branch", ref]
	return _chroot_ok(["git", "clone", "--depth", "1", *branch, "--", repo, clone_path])


def install_window_manager(config: Config) -> bool:
	if config.desktop == "custom":
		entry = ("custom", "Custom repo", config.custom_repo, "-", "")
	else:
		entry = next((row for row in utils.wm_rows() if row[0] == config.desktop), None)
	if entry is None:
		return True
	wm_id, label, repo, ref, _description = entry
	msg(f"Fetching {label}")

	clone_path = f"/tmp/wm-{wm_id}"
	try:
		if not _fetch_wm_source(repo, ref, clone_path):
			_log(f"{label} source unavailable.")
			return False
		msg(f"Installing {label}")
		return _report(label, _chroot_ok(["bash", f"{clone_path}/install.sh"]),
		               "Fix it from Shell, or rerun its install.sh.")
	finally:
		_try(["rm", "-rf", f"{MNT}{clone_path}"])


def install_extras(config: Config) -> list:
	custom_ids = {custom_id for custom_id, *_rest in utils.custom_rows()}
	labels = {package: label for _menu, _section, package, label, _requires in utils.extras_rows()}
	failed = []
	for package in config.selected_packages():
		if package in custom_ids or not utils.needs_network(package):
			continue  # custom scripts run separately; local-repo packages are already installed
		label = labels.get(package, package)
		msg(f"Installing {label} (apt)")
		if not _report(label, _chroot_ok([*APT_COMMAND, "install", "-y", package]),
		               f"Fix it from Shell, or rerun: apt-get install {package}"):
			failed.append(label)
	return failed


def install_custom_software(config: Config) -> list:
	selected = set(config.selected_packages())
	failed = []
	for custom_id, _menu, _section, label, script, _requirement in utils.custom_rows():
		if custom_id not in selected:
			continue
		msg(f"Installing {label}")
		staged_path = f"{MNT}/tmp/{script}"
		try:
			shutil.copy2(os.path.join(utils.CUSTOM_DIR, script), staged_path)
			if not _report(label, _chroot_ok(["sh", f"/tmp/{script}"]),
			               "Fix it from Shell, or rerun the script."):
				failed.append(label)
		finally:
			_remove(staged_path)
	return failed


# ── Bootloader ─────────────────────────────────────────────────────────

def _install_removable_fallback():
	"""Copy shim and GRUB to EFI/BOOT so the disk boots if firmware drops its NVRAM entry."""
	source_dir = f"{MNT}/boot/efi/EFI/debian"
	target_dir = f"{MNT}/boot/efi/EFI/BOOT"
	os.makedirs(target_dir, exist_ok=True)
	shutil.copy2(f"{source_dir}/shimx64.efi", f"{target_dir}/BOOTX64.EFI")
	for name in ("grubx64.efi", "mmx64.efi"):
		shutil.copy2(f"{source_dir}/{name}", f"{target_dir}/{name}")


def ensure_grub_modules(config: Config):
	"""Install the GRUB module package if the target lacks modinfo.sh."""
	platform = "x86_64-efi" if config.boot_mode == BOOT_UEFI else "i386-pc"
	if os.path.isfile(f"{MNT}/usr/lib/grub/{platform}/modinfo.sh"):
		return
	packages = utils.boot_packages(config.boot_mode)
	msg("Installing GRUB modules...")
	if config.online:
		_apt(["install", "-y", *packages])
	else:
		_install_from_local_repo(packages)


def configure_plymouth(config: Config):
	if not config.luks:
		return
	msg("Setting boot splash theme")
	theme = "bgrt" if config.boot_mode == BOOT_UEFI else "spinner"
	_chroot(["plymouth-set-default-theme", "-R", theme])
	ok("Boot splash configured")


def write_grub_defaults(config: Config):
	msg("Writing GRUB defaults")
	splash = " splash" if config.luks else ""
	resume = f" resume=UUID={_swap_uuid(config)}" if "swap" in config.layout else ""
	hidden = not config.dual_boot
	lines = [
		f'GRUB_DISTRIBUTOR="{PROFILE["PROFILE_NAME"]}"',
		"GRUB_DEFAULT=0",
		f"GRUB_TIMEOUT={0 if hidden else 3}",
		f"GRUB_TIMEOUT_STYLE={'hidden' if hidden else 'menu'}",
		"GRUB_GFXMODE=auto",
		"GRUB_GFXPAYLOAD_LINUX=keep",
		f'GRUB_CMDLINE_LINUX_DEFAULT="quiet{splash}{resume}"',
		'GRUB_CMDLINE_LINUX=""',
	]
	if config.dual_boot:
		lines.append("GRUB_DISABLE_OS_PROBER=false")
	_write_target("/etc/default/grub", "\n".join(lines) + "\n")


def install_bootloader(config: Config):
	msg(f"Installing bootloader ({config.boot_mode})")
	ensure_grub_modules(config)
	if config.boot_mode == BOOT_UEFI:
		_chroot(["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
		         "--bootloader-id=debian", "--recheck", f"--modules={GRUB_MODULES}"])
		if not os.path.isfile(f"{MNT}/boot/efi/EFI/debian/shimx64.efi"):
			die("Signed shim was not installed to the EFI partition.")
		if not config.shared_esp():
			_install_removable_fallback()
	else:
		_chroot(["grub-install", "--target=i386-pc", "--recheck",
		         f"--modules={GRUB_MODULES}", config.disk])
	write_grub_defaults(config)
	msg("Updating GRUB configuration")
	_chroot(["update-grub"])
	ok("Bootloader installed")


def configure_secure_boot(config: Config):
	if not config.needs_mok():
		return
	msg("Setting up Secure Boot signing")
	key_dir = f"{MNT}/var/lib/dkms"
	os.makedirs(key_dir, exist_ok=True)
	_chroot(["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
	         "-days", "36500", "-subj", "/CN=DebPlus Secure Boot/",
	         "-keyout", "/var/lib/dkms/mok.key", "-out", "/var/lib/dkms/mok.pub"])
	os.chmod(f"{key_dir}/mok.key", 0o600)
	_chroot(["openssl", "x509", "-outform", "DER",
	         "-in", "/var/lib/dkms/mok.pub", "-out", "/var/lib/dkms/mok.der"])

	os.makedirs(f"{MNT}/etc/dkms", exist_ok=True)  # may be absent if DKMS is not yet installed
	_write_target("/etc/dkms/framework.conf",
	              "mok_signing_key=/var/lib/dkms/mok.key\n"
	              "mok_certificate=/var/lib/dkms/mok.pub\n", append=True)

	modules_dir = f"{MNT}/lib/modules"
	kernel_versions = sorted(os.listdir(modules_dir)) if os.path.isdir(modules_dir) else []
	for version in kernel_versions:
		if os.path.isdir(f"{MNT}/usr/src/linux-headers-{version}"):
			msg(f"Signing DKMS modules for {version}")
			_chroot(["dkms", "autoinstall", "-k", version])

	result = subprocess.run(["mokutil", "--import", f"{key_dir}/mok.der"],
	                        input=f"{config.mok_pass}\n{config.mok_pass}\n",
	                        text=True, capture_output=True)
	if result.returncode != 0:
		die(f"mokutil import failed: {result.stderr.strip()}")
	ok("Key queued. On first boot choose Enroll MOK and enter the password.")


def configure_firewall(config: Config):
	if not config.ufw:
		return
	msg("Configuring firewall")
	_chroot(["ufw", "default", config.ufw_incoming, "incoming"])
	_chroot(["ufw", "default", config.ufw_outgoing, "outgoing"])
	rules = {rule_id: rule for rule_id, _label, rule in utils.firewall_rows()}
	for rule_id in config.firewall_rules:
		_chroot(["ufw", *rules[rule_id].split()])
	_chroot(["sed", "-i", "s/^ENABLED=.*/ENABLED=yes/", "/etc/ufw/ufw.conf"])
	_chroot(["systemctl", "enable", "ufw"])
	ok("Firewall configured")


def configure_git(config: Config):
	msg("Configuring git")
	settings = [
		("init.defaultBranch", config.git_default_branch),
		("core.editor", config.git_editor),
		("pull.rebase", config.git_pull_rebase),
		("user.name", config.git_name),
		("user.email", config.git_email),
	]
	for key, value in settings:
		if value:
			_chroot(["runuser", "-u", config.username, "--", "git", "config", "--global", key, value])
	ok("Git configured")


# ── Cleanup and orchestration ─────────────────────────────────────────

def cleanup():
	ui.stop_spinner()
	msg("Unmounting")
	if os.path.isdir(f"{MNT}/var/log"):
		try:
			shutil.copy2(LOG_PATH, f"{MNT}/var/log/debplus-install.log")
		except OSError:
			pass
	_unmount(MNT, lazy=True, recursive=True)
	ok("Done")


def _reboot():
	subprocess.run(["reboot"])


def _open_shell():
	sys.stdout.write("\x1b[2J\x1b[H")
	sys.stdout.flush()
	if os.path.isdir(f"{MNT}/etc"):
		subprocess.run(["chroot", MNT, "/bin/bash"])
	else:
		subprocess.run(["/bin/bash"])
	ui.redraw()


def _choose(question, keys):
	"""Prompt until the reply's first letter is in keys. Returns the last key on EOF."""
	ui.prompt(question)
	while True:
		try:
			reply = input().strip().lower()[:1]
		except (EOFError, KeyboardInterrupt):
			return keys[-1]
		if reply in keys:
			return reply
		ui.prompt(question, "Enter one of the letters shown.")


def _choose_with_shell(question, keys):
	"""Like _choose, but the 's' key opens a shell and asks again."""
	while True:
		choice = _choose(question, keys)
		if choice != "s":
			return choice
		_open_shell()


def _mark_done(label):
	os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
	with open(STATE_PATH, "a") as state_file:
		state_file.write(label + "\n")


def _flush_input():
	"""Drop keys pressed during the install so they don't trigger the reboot prompt."""
	try:
		termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
	except (termios.error, OSError, ValueError):
		pass


def _failure_lines(label, failed_at, error):
	lines = [(RED_BOLD, f"{label} failed")]
	if failed_at:
		lines.append(f"at: {failed_at}")
	lines += textwrap.wrap(str(error), 70)
	lines += ["", f"Log: {LOG_PATH}"]
	return lines


def _run_stage(label, action):
	ui.begin_stage(label)
	while True:
		ui.start_spinner()
		try:
			action()
		except Exception as error:
			ui.stop_spinner()
			failed_at = ui.current_sub()
			_log(f"Error: {label} failed at '{failed_at}': {error}")
			ui.show_message(_failure_lines(label, failed_at, error))
			choice = _choose_with_shell("(R)etry, (S)hell, Re(b)oot, or (A)bort?", "rsba")
			ui.show_message([])
			if choice == "r":
				ui.begin_stage(label)
				continue
			cleanup()
			if choice == "b":
				_reboot()
			sys.exit(1)
		ui.stop_spinner()
		_mark_done(label)
		ui.finish_stage()
		return


def run_install(config: Config):
	_try(["hostname", config.hostname])
	failures = []

	def desktop_stage():
		if not install_window_manager(config):
			failures.append("Window manager")

	def snapshots_stage():
		if not config.timeshift:
			return
		configure_timeshift(config)
		if not take_first_snapshot():
			failures.append("First Timeshift snapshot")

	def extras_stage():
		failures.extend(install_extras(config))
		failures.extend(install_custom_software(config))

	stages = [
		("Partitioning", lambda: partition_disk(config)),
		("Base system", lambda: install_base(config)),
		("Configuration", lambda: configure_system(config)),
		("Users", lambda: create_users(config)),
		("Desktop", desktop_stage),
		("Extras", extras_stage),
		("Firewall", lambda: configure_firewall(config)),
		("Git", lambda: configure_git(config)),
		("Secure Boot", lambda: configure_secure_boot(config)),
		("Boot splash", lambda: configure_plymouth(config)),
		("Bootloader", lambda: install_bootloader(config)),
		("Snapshots", snapshots_stage),
	]
	ui.start(len(stages))
	try:
		for label, action in stages:
			_run_stage(label, action)
	except KeyboardInterrupt:
		cleanup()
		raise

	ui.stop_spinner()
	if failures:
		lines = [(RED_BOLD, "Installed with errors")] + [f"- {failure}" for failure in failures]
		ui.show_message(lines)
		choice = _choose_with_shell("(R)eboot, (S)hell, or (A)bort?", "rsa")
		cleanup()
		if choice == "r":
			_reboot()
		return

	ui.set_sub("")
	ui.show_message([
		(GREEN_BOLD, f"{PROFILE['PROFILE_NAME']} installation complete!"),
		"",
		"Remove the installation media if the firmware boots it first.",
		"Press Enter to reboot now, or Ctrl+C to stay in the live system.",
		f"Rebooting automatically in {REBOOT_DELAY_SECONDS} seconds.",
	])
	_flush_input()
	try:
		ready, _, _ = select.select([sys.stdin], [], [], REBOOT_DELAY_SECONDS)
	except KeyboardInterrupt:
		ui.show_message(["Reboot cancelled.", f"Target is still mounted at {MNT}."])
		return
	if ready:
		sys.stdin.readline()
	cleanup()
	_reboot()
