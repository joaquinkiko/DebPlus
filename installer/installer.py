# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import glob
import math
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import termios
import time

from . import utils
from .config import Config
from .utils import (MNT, PROFILE, BIOS_BOOT_END_MIB, ESP_END_MIB,
                    get_partitions, keyboard_config)

BOLD = "\033[1m"
RED = "\033[0;31m"
GREEN = "\033[0;32m"
CYAN = "\033[0;36m"
NC = "\033[0m"

SUITE = PROFILE["SUITE"]
BACKPORTS = f"{SUITE}-backports"
KEYRING_PATH = "/usr/share/keyrings/debian-archive-keyring.gpg"
KEYRING_LINE = f"Signed-By: {KEYRING_PATH}"
SECURITY_MIRROR = "http://security.debian.org/debian-security"
EFIVARS_HOST = "/sys/firmware/efi/efivars"
BOOT_UEFI = "uefi"
BOOT_BIOS = "bios"

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
ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")

LUKS_ROOT = "cryptroot"
LUKS_SWAP = "cryptswap"
SWAP_KEY_LIVE = "/run/debplus/swap.key"

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
REBOOT_DELAY_SECONDS = 30


class Progress:
	"""Steps bar on the terminal's last row. Filled by finished steps, labelled with the current one."""

	def __init__(self):
		self.total = 0
		self.done = 0
		self.label = ""
		self.active = False

	def start(self, total):
		self.total = total
		self.active = True
		rows = shutil.get_terminal_size().lines
		sys.stdout.write(f"\x1b[1;{rows - 1}r")  # scrolling stops above the bar row
		sys.stdout.flush()
		self._draw()

	def stop(self):
		if not self.active:
			return
		self.active = False
		rows = shutil.get_terminal_size().lines
		sys.stdout.write(f"\x1b7\x1b[{rows};1H\x1b[2K\x1b[r\x1b8")
		sys.stdout.flush()

	def start_stage(self, label):
		self.label = label
		self._draw()

	def finish_stage(self):
		self.done += 1
		self._draw()

	def _draw(self):
		if not self.active:
			return
		filled = self.done * PROGRESS_BAR_WIDTH // self.total
		bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
		step = min(self.done + 1, self.total)
		rows = shutil.get_terminal_size().lines
		sys.stdout.write(f"\x1b7\x1b[{rows};1H\x1b[2K  [{bar}]  {self.label} {step}/{self.total}\x1b8")
		sys.stdout.flush()


PROGRESS = Progress()


# ── Output and command helpers ─────────────────────────────────────────

def _emit(text, stream=sys.stdout):
	print(text, file=stream)
	os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
	with open(LOG_PATH, "a") as log_file:
		log_file.write(ANSI_PATTERN.sub("", text) + "\n")


def msg(text):
	_emit(f"\n  {CYAN}{BOLD}::{NC} {BOLD}{text}{NC}")


def ok(text):
	_emit(f"  {GREEN}{text}{NC}")


class StageError(Exception):
	"""Raised inside an install stage. The runner decides retry, shell, reboot, or abort."""


def die(text):
	raise StageError(text)


def _run(argv, quiet=False):
	"""Run argv, raising StageError on failure."""
	result = subprocess.run(argv, capture_output=quiet, text=True)
	if result.returncode != 0:
		detail = f"\n{result.stderr}" if quiet else ""
		die(f"Command failed: {shlex.join(argv)}{detail}")
	return result


def _try(argv):
	"""Run argv and ignore failure."""
	subprocess.run(argv, capture_output=True)


def _chroot(argv, quiet=False):
	return _run(["chroot", MNT, *argv], quiet)


def _chroot_ok(argv):
	"""Run in the chroot with output shown, returning success instead of raising."""
	return subprocess.run(["chroot", MNT, *argv]).returncode == 0


def _apt(arguments, quiet=False):
	return _chroot([*APT_COMMAND, *arguments], quiet)


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
	"""Report an optional step's outcome. Failure is recorded by the caller, not raised."""
	if succeeded:
		ok(f"{label} installed")
		return True
	print(f"  {RED}{label} failed. {hint}{NC}")
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
	_run(["wipefs", "-af", config.disk], quiet=True)
	_run(["parted", "-s", config.disk, *parted_args], quiet=True)
	partitions = _wait_for_partitions(config.disk, len(roles))
	config.layout = {role: {"device": device, "format": role != "bios_grub"}
	                 for role, device in zip(roles, partitions)}


def _partition_auto(config: Config):
	disk_mib = utils.disk_bytes(config.disk) // 2**20
	disk_gib = disk_mib // 1024
	ram_gib = math.ceil(utils.ram_mib() / 1024)
	swap_gib = config.swap_gib(ram_gib, disk_gib) if config.has_swap_partition() else 0
	swap_start = f"{disk_mib - swap_gib * 1024}MiB"
	boot_roles, boot_args, boot_end_mib = _boot_partition(config.boot_mode == BOOT_UEFI)

	parted_args = ["mklabel", "gpt", *boot_args, "mkpart", "root",
	               PARTED_FS_HINT[config.filesystem], f"{boot_end_mib}MiB",
	               swap_start if swap_gib else "100%"]
	roles = [*boot_roles, "root"]
	if swap_gib:
		roles.append("swap")
		parted_args += ["mkpart", "swap", "linux-swap", swap_start, "100%"]
	_apply_partitions(config, roles, parted_args)


def _custom_segments(config: Config, first_mib, last_mib, disk_mib):
	"""(role, parted type, start, end) for custom sizes filling first_mib to last_mib. Swap sits last."""
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
	return segments


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
	_run(["parted", "-s", config.disk, *_mkpart_args(label, segments)], quiet=True)
	created = _wait_for_new_partitions(config.disk, existing, len(segments))
	config.layout = {role: {"device": device, "format": True}
	                 for (role, *_rest), device in zip(segments, created)}
	if esp:
		config.layout["esp"] = {"device": esp, "format": False}


def _format_layout(config: Config):
	msg("Formatting...")
	for role, part in config.layout.items():
		if not part["format"] or (role == "swap" and config.luks):
			continue
		device = _device(config, role)
		if role == "esp":
			_run(["mkfs.fat", "-F32", device])
		elif role == "swap":
			_run(["mkswap", device])
		elif role in ("root", "home"):
			_make_filesystem(device, config.filesystem)


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
	"""Device to format or mount for a role. Encrypted roles use their open mapper."""
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
	_try(["swapoff", f"/dev/mapper/{LUKS_SWAP}"])
	_try(["cryptsetup", "close", LUKS_SWAP])
	_try(["cryptsetup", "close", LUKS_ROOT])


def _encrypt_layout(config: Config):
	msg("Encrypting...")
	root = config.layout["root"]["device"]
	_cryptsetup(["luksFormat", "--type", "luks2", "--batch-mode", "--key-file=-", root],
	            config.luks_pass)
	_cryptsetup(["open", "--key-file=-", root, LUKS_ROOT], config.luks_pass)
	if "swap" in config.layout:
		swap = config.layout["swap"]["device"]
		os.makedirs(os.path.dirname(SWAP_KEY_LIVE), exist_ok=True)
		with open(SWAP_KEY_LIVE, "wb") as key_file:
			key_file.write(os.urandom(64))
		os.chmod(SWAP_KEY_LIVE, 0o600)
		_cryptsetup(["luksFormat", "--type", "luks2", "--batch-mode",
		             f"--key-file={SWAP_KEY_LIVE}", swap])
		_cryptsetup(["open", f"--key-file={SWAP_KEY_LIVE}", swap, LUKS_SWAP])
		_run(["mkswap", f"/dev/mapper/{LUKS_SWAP}"])


def partition_disk(config: Config):
	_unmount(MNT, lazy=True, recursive=True)
	_close_mappers()
	if config.manual:
		msg("Using selected partitions...")
	else:
		msg(f"Partitioning {config.disk}...")
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
	for sub in ("sys/firmware/efi/efivars", "sys", "proc", "dev/pts", "dev", "boot/efi", "home"):
		_unmount(f"{MNT}/{sub}", lazy=True)


def reset_identity():
	"""The image is shared by every install, so give this one its own identity."""
	msg("Resetting machine identity...")
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
	msg("Installing selected packages from the install medium...")
	mount_point = f"{MNT}{LOCAL_REPO_MOUNT}"
	os.makedirs(mount_point, exist_ok=True)
	_run(["mount", "--bind", repo, mount_point])
	try:
		_write_target("/etc/apt/debplus-local.list", f"deb [trusted=yes] file:{LOCAL_REPO_MOUNT} ./\n")
		_apt([*LOCAL_APT_OPTIONS, "update"], quiet=True)
		_apt([*LOCAL_APT_OPTIONS, "install", "-y", "--no-install-recommends", *packages], quiet=True)
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

	msg("Updating packages...")
	_apt(["update"])
	if packages:
		_apt(["install", "-y", "--no-install-recommends", *packages])
	_apt(["upgrade", "-y"])
	if config.backports_kernel:
		msg("Installing backports kernel...")
		_apt(["install", "-y", "-t", BACKPORTS, "linux-image-amd64", "linux-headers-amd64"])
	if config.backports_mesa:
		msg("Installing backports Mesa...")
		_apt(["install", "-y", "-t", BACKPORTS, *utils.mesa_packages()])
	_chroot(["apt-get", "clean"])


def install_base(config: Config):
	image = utils.target_image()
	if not image:
		die("Base system image not found on the install medium.")
	_unmount_all()
	msg("Extracting base system (this will take a while)...")
	_run(["unsquashfs", "-f", "-n", "-d", MNT, image])
	_remove(f"{MNT}/etc/resolv.conf")
	shutil.copy2("/etc/resolv.conf", f"{MNT}/etc/resolv.conf")
	mount_secondary(config)
	write_apt_sources(config)
	mount_chroot_fs()
	reset_identity()
	if config.multiarch_i386:
		_chroot(["dpkg", "--add-architecture", "i386"])
	install_packages(config)
	ok("Base system installed")


# ── System configuration ──────────────────────────────────────────────

def configure_encryption(config: Config):
	msg("Configuring encryption...")
	lines = [f"{LUKS_ROOT} UUID={_blkid(config.layout['root']['device'], 'UUID')} none luks"]
	if "swap" in config.layout:
		os.makedirs(f"{MNT}/etc/luks", exist_ok=True)
		shutil.copy2(SWAP_KEY_LIVE, f"{MNT}/etc/luks/swap.key")
		os.chmod(f"{MNT}/etc/luks/swap.key", 0o600)
		lines.append(f"{LUKS_SWAP} UUID={_blkid(config.layout['swap']['device'], 'UUID')} "
		             f"/etc/luks/swap.key luks")
	_write_target("/etc/crypttab", "\n".join(lines) + "\n")
	with open(f"{MNT}/etc/default/grub") as grub_file:
		if "GRUB_ENABLE_CRYPTODISK=y" not in grub_file.read():
			_write_target("/etc/default/grub", "GRUB_ENABLE_CRYPTODISK=y\n", append=True)
	ok("Encryption configured")


def configure_snapper(config: Config):
	if not config.snapper:
		return
	msg("Configuring snapper...")
	if not os.path.isfile(f"{MNT}/etc/snapper/configs/root"):
		_chroot(["snapper", "-c", "root", "create-config", "/"])
	if not os.path.isdir(f"{MNT}/.snapshots/1"):
		_chroot(["snapper", "-c", "root", "create", "--description", "post-install"])
	ok("Snapper configured")


def configure_auto_updates():
	msg("Enabling security updates...")
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


def configure_system(config: Config):
	msg("Configuring system...")
	write_fstab(config)
	_write_target("/etc/hostname", config.hostname + "\n")
	_write_target("/etc/hosts",
	              "127.0.0.1   localhost\n"
	              f"127.0.1.1   {config.hostname}\n"
	              "\n"
	              "::1         localhost ip6-localhost ip6-loopback\n"
	              "ff02::1     ip6-allnodes\n"
	              "ff02::2     ip6-allrouters\n")

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
	if config.auto_updates:
		configure_auto_updates()

	msg("Rebuilding initramfs...")  # fstab and crypttab exist now
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
	msg("Creating users...")
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
			print(f"  {RED}Cannot copy {repo}: {error}{NC}")
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
	msg(f"Installing {label}...")

	clone_path = f"/tmp/wm-{wm_id}"
	try:
		if not _fetch_wm_source(repo, ref, clone_path):
			print(f"  {RED}{label} source unavailable.{NC}")
			return False
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
		msg(f"Installing {label}...")
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
		msg(f"Installing {label}...")
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


def install_bootloader(config: Config):
	msg(f"Installing bootloader ({config.boot_mode})...")
	if config.boot_mode == BOOT_UEFI:
		_chroot(["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
		         "--bootloader-id=debian", "--recheck"])
		if not os.path.isfile(f"{MNT}/boot/efi/EFI/debian/shimx64.efi"):
			die("Signed shim was not installed to the EFI partition.")
		if not config.shared_esp():
			_install_removable_fallback()
	else:
		_chroot(["grub-install", "--target=i386-pc", "--recheck", config.disk])
	if config.dual_boot:
		_write_target("/etc/default/grub", "GRUB_DISABLE_OS_PROBER=false\n", append=True)
	_chroot(["update-grub"])
	ok("Bootloader installed")


def configure_secure_boot(config: Config):
	if not config.needs_mok():
		return
	msg("Setting up Secure Boot signing...")
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
			_chroot(["dkms", "autoinstall", "-k", version])

	result = subprocess.run(["mokutil", "--import", f"{key_dir}/mok.der"],
	                        input=f"{config.mok_pass}\n{config.mok_pass}\n",
	                        text=True, capture_output=True)
	if result.returncode != 0:
		die(f"mokutil import failed: {result.stderr.strip()}")
	ok("Key queued. On first boot choose Enroll MOK and enter the password.")


# ── Cleanup and orchestration ─────────────────────────────────────────

def cleanup():
	PROGRESS.stop()
	msg("Unmounting...")
	if os.path.isdir(f"{MNT}/var/log"):
		try:
			shutil.copy2(LOG_PATH, f"{MNT}/var/log/debplus-install.log")
		except OSError:
			pass
	_unmount(MNT, lazy=True, recursive=True)
	_remove(SWAP_KEY_LIVE)
	ok("Done")


def _reboot():
	subprocess.run(["reboot"])


def _open_shell():
	if os.path.isdir(f"{MNT}/etc"):
		subprocess.run(["chroot", MNT, "/bin/bash"])
	else:
		subprocess.run(["/bin/bash"])


def _choose(question, keys):
	"""Prompt until the reply's first letter is in keys. Returns the last key on EOF."""
	while True:
		try:
			reply = input(f"\n  {BOLD}{question}{NC} ").strip().lower()[:1]
		except (EOFError, KeyboardInterrupt):
			return keys[-1]
		if reply in keys:
			return reply
		print("  Enter one of the letters shown.")


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


def _clear_screen():
	sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
	sys.stdout.flush()


def _flush_input():
	"""Drop keys pressed during the install so they don't trigger the reboot prompt."""
	try:
		termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
	except (termios.error, OSError, ValueError):
		pass


def _run_stage(index, total, label, action):
	msg(f"[{index}/{total}] {label}")
	PROGRESS.start_stage(label)
	while True:
		try:
			action()
			_mark_done(label)
			PROGRESS.finish_stage()
			return
		except Exception as error:
			_emit(f"\n  {RED}{BOLD}Error:{NC} {label} failed: {error}", sys.stderr)
			choice = _choose_with_shell("(R)etry, (S)hell, Re(b)oot, or (A)bort?", "rsba")
			if choice == "r":
				continue
			cleanup()
			if choice == "b":
				_reboot()
			sys.exit(1)


def run_install(config: Config):
	_clear_screen()
	_try(["hostname", config.hostname])
	failures = []

	def desktop_stage():
		if not install_window_manager(config):
			failures.append("Window manager")

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
		("Secure Boot", lambda: configure_secure_boot(config)),
		("Bootloader", lambda: install_bootloader(config)),
		("Snapshots", lambda: configure_snapper(config)),
	]
	PROGRESS.start(len(stages))
	_emit(f"\n  {BOLD}{CYAN}Installing {PROFILE['PROFILE_NAME']}{NC}\n")
	try:
		for index, (label, action) in enumerate(stages, 1):
			_run_stage(index, len(stages), label, action)
	except KeyboardInterrupt:
		cleanup()
		raise

	PROGRESS.stop()
	_clear_screen()
	if failures:
		_emit(f"\n  {RED}{BOLD}Installed with errors:{NC}")
		for failure in failures:
			_emit(f"  {RED}- {failure}{NC}")
		choice = _choose_with_shell("(R)eboot, (S)hell, or (A)bort?", "rsa")
		cleanup()
		if choice == "r":
			_reboot()
		return

	_emit(f"\n  {GREEN}{BOLD}Installation complete!{NC}")
	_emit("\n  Remove the installation media if the firmware boots it first.")
	_emit("  Press Enter to reboot now, or Ctrl+C to stay in the live system.")
	_emit(f"  Rebooting automatically in {REBOOT_DELAY_SECONDS} seconds.")
	_flush_input()
	try:
		ready, _, _ = select.select([sys.stdin], [], [], REBOOT_DELAY_SECONDS)
	except KeyboardInterrupt:
		_emit("\n  Reboot cancelled. Target is still mounted at " + MNT + ".")
		return
	if ready:
		sys.stdin.readline()
	cleanup()
	_reboot()
