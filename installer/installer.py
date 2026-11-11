# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os
import re
import shutil
import subprocess
import sys
import time
import shlex

from . import utils
from .config import Config
from .utils import (FIRMWARE_PATH, MNT, PROFILE, TARGET_PACKAGES_PATH,
                    desktop_rows, get_partitions, keyboard_config, read_lines)

BOLD = "\033[1m"
RED = "\033[0;31m"
GREEN = "\033[0;32m"
CYAN = "\033[0;36m"
NC = "\033[0m"

SUITE = PROFILE["SUITE"]
MIRROR = PROFILE["MIRROR"]
PARTED_FS_HINT = {"btrfs": "btrfs", "ext4": "ext4", "xfs": "ext2"}
BOOT_PACKAGES = {"uefi": ["grub-efi-amd64", "efibootmgr"], "bios": ["grub-pc"]}
FSTAB_OPTIONS = {
	"btrfs": "defaults,compress=zstd,noatime",
	"ext4": "errors=remount-ro",
	"xfs": "defaults",
}
MESA_PACKAGES = ["libgl1-mesa-dri", "libglx-mesa0", "libegl-mesa0", "libgbm1",
                 "mesa-vulkan-drivers", "mesa-va-drivers"]


LOG_PATH = "/var/log/debplus/install.log"
STATE_PATH = "/run/debplus/state"
ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")


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


def _run(command, quiet=False):
	result = subprocess.run(command, shell=True, capture_output=quiet, text=True)
	if result.returncode != 0:
		detail = f"\n{result.stderr}" if quiet else ""
		die(f"Command failed: {command}{detail}")
	return result


def _try(command):
	subprocess.run(command, shell=True, capture_output=True)


# ── Partitioning ───────────────────────────────────────────────────────

def _make_filesystem(device, filesystem):
	commands = {
		"btrfs": f"mkfs.btrfs -f {device}",
		"xfs": f"mkfs.xfs -f {device}",
		"ext4": f"mkfs.ext4 -qF {device}",
	}
	_run(commands[filesystem])


def _wait_for_partitions(disk, expected, attempts=10):
	for _ in range(attempts):
		_try(f"partprobe {disk}")
		parts = get_partitions(disk)
		if len(parts) >= expected:
			return parts
		time.sleep(0.5)
	die(f"Expected {expected} partitions on {disk}, found {len(parts)}")


def _partition_auto(config: Config):
	uefi = config.boot_mode == "uefi"
	filesystem_hint = PARTED_FS_HINT[config.filesystem]
	ram_gib = -(-utils.ram_mib() // 1024)
	disk_gib = utils.disk_bytes(config.disk) // 2**30
	swap_gib = config.swap_gib(ram_gib, disk_gib) if config.has_swap_partition() else 0
	swap = swap_gib > 0
	steps = ["mklabel gpt"]
	if uefi:
		roles = ["esp", "root"]
		steps.append("mkpart ESP fat32 1MiB 513MiB set 1 esp on")
		root_start = "513MiB"
	else:
		roles = ["bios_grub", "root"]
		steps.append("mkpart bios_grub 1MiB 2MiB set 1 bios_grub on")
		root_start = "2MiB"
	root_end = f"-{swap_gib}GiB" if swap else "100%"
	steps.append(f"mkpart root {filesystem_hint} {root_start} {root_end}")
	if swap:
		roles.append("swap")
		steps.append(f"mkpart swap linux-swap -{swap_gib}GiB 100%")
	_run(f"wipefs -af {config.disk}", quiet=True)
	_run(f"parted -s {config.disk} {' '.join(steps)}", quiet=True)
	partitions = _wait_for_partitions(config.disk, len(roles))
	config.layout = {role: {"device": device, "format": role != "bios_grub"}
	                 for role, device in zip(roles, partitions)}


def _format_layout(config: Config):
	msg("Formatting...")
	for role, part in config.layout.items():
		if not part["format"]:
			continue
		device = part["device"]
		if role == "esp":
			_run(f"mkfs.fat -F32 {device}")
		elif role == "swap":
			_run(f"mkswap {device}")
		elif role in ("root", "home"):
			_make_filesystem(device, config.filesystem)


def _mount_layout(config: Config):
	layout = config.layout
	root = layout["root"]["device"]
	btrfs = config.filesystem == "btrfs"
	os.makedirs(MNT, exist_ok=True)
	if btrfs:
		_run(f"mount {root} {MNT}")
		_run(f"btrfs subvolume create {MNT}/@")
		if "home" not in layout:
			_run(f"btrfs subvolume create {MNT}/@home")
		_run(f"umount {MNT}")
	subvolume_option = "-o subvol=@ " if btrfs else ""
	_run(f"mount {subvolume_option}{root} {MNT}")
	if "home" in layout:
		os.makedirs(f"{MNT}/home", exist_ok=True)
		_run(f"mount {layout['home']['device']} {MNT}/home")
	elif btrfs:
		os.makedirs(f"{MNT}/home", exist_ok=True)
		_run(f"mount -o subvol=@home {root} {MNT}/home")
	if "esp" in layout:
		os.makedirs(f"{MNT}/boot/efi", exist_ok=True)
		_run(f"mount {layout['esp']['device']} {MNT}/boot/efi")


def partition_disk(config: Config):
	if config.manual:
		msg("Using selected partitions...")
	else:
		msg(f"Partitioning {config.disk}...")
		_partition_auto(config)
	_format_layout(config)
	_mount_layout(config)
	ok("Partitioned and mounted")


def _fstab_rows(config: Config):
	layout = config.layout
	root = layout["root"]["device"]
	btrfs = config.filesystem == "btrfs"
	rows = [(root, "/", config.filesystem,
	         FSTAB_OPTIONS[config.filesystem] + (",subvol=@" if btrfs else ""), 1)]
	if "home" in layout:
		rows.append((layout["home"]["device"], "/home", config.filesystem,
		             FSTAB_OPTIONS[config.filesystem], 2))
	elif btrfs:
		rows.append((root, "/home", "btrfs",
		             FSTAB_OPTIONS["btrfs"] + ",subvol=@home", 2))
	if "esp" in layout:
		rows.append((layout["esp"]["device"], "/boot/efi", "vfat", "umask=0077", 2))
	if "swap" in layout:
		rows.append((layout["swap"]["device"], "none", "swap", "sw", 0))
	return rows


def write_fstab(config: Config):
	lines = ["# <filesystem>  <mount>  <type>  <options>  <dump>  <pass>"]
	for device, mount_point, filesystem, options, pass_number in _fstab_rows(config):
		lines.append(f"UUID={_uuid(device)}  {mount_point}  {filesystem}  {options}  0  {pass_number}")
	with open(f"{MNT}/etc/fstab", "w") as fstab_file:
		fstab_file.write("\n".join(lines) + "\n")


# ── Chroot mounts ──────────────────────────────────────────────────────

def mount_chroot_fs():
	_run(f"mount --bind /dev {MNT}/dev")
	_run(f"mount --bind /dev/pts {MNT}/dev/pts")
	_run(f"mount -t proc proc {MNT}/proc")
	_run(f"mount -t sysfs sys {MNT}/sys")
	if os.path.isdir("/sys/firmware/efi/efivars"):
		_try(f"mount --bind /sys/firmware/efi/efivars {MNT}/sys/firmware/efi/efivars")


# ── Apt sources ────────────────────────────────────────────────────────

def write_apt_sources(config: Config):
	components = " ".join(["main"] + config.components)
	keyring = "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg"
	stanzas = [
		f"Types: deb\nURIs: {MIRROR}\nSuites: {SUITE} {SUITE}-updates\n"
		f"Components: {components}\n{keyring}\n",
		f"Types: deb\nURIs: http://security.debian.org/debian-security\n"
		f"Suites: {SUITE}-security\nComponents: {components}\n{keyring}\n",
	]

	backports_enabled = config.backports_repo or config.backports_kernel or config.backports_mesa
	if backports_enabled:
		stanzas.append(
			f"Types: deb\nURIs: {MIRROR}\nSuites: {SUITE}-backports\n"
			f"Components: {components}\n{keyring}\n"
		)
		os.makedirs(f"{MNT}/etc/apt/preferences.d", exist_ok=True)
		with open(f"{MNT}/etc/apt/preferences.d/backports", "w") as pin_file:
			# Repo stays opt-in: packages install only with -t
			pin_file.write(f"Package: *\nPin: release a={SUITE}-backports\n"
			               f"Pin-Priority: 100\n")
			if config.backports_kernel:
				pin_file.write(f"\nPackage: linux-image-amd64 linux-headers-amd64\n"
				               f"Pin: release a={SUITE}-backports\n"
				               f"Pin-Priority: 990\n")
			if config.backports_mesa:
				# Glob catches the Mesa libraries and drivers; libgbm1 has no "mesa" in its name
				pin_file.write(f"\nPackage: *mesa* libgbm1\n"
				               f"Pin: release a={SUITE}-backports\n"
				               f"Pin-Priority: 990\n")

	with open(f"{MNT}/etc/apt/sources.list.d/debian.sources", "w") as sources_file:
		sources_file.write("\n".join(stanzas))
	with open(f"{MNT}/etc/apt/sources.list", "w"):
		pass


# ── Base system ────────────────────────────────────────────────────────

def install_base(config: Config):
	msg("Running debootstrap (this will take a while)...")
	_run(f"debootstrap --arch=amd64 --variant=minbase {SUITE} {MNT} {MIRROR}")
	shutil.copy2("/etc/resolv.conf", f"{MNT}/etc/resolv.conf")
	write_apt_sources(config)
	mount_chroot_fs()

	if config.boot_mode == "bios":
		# Keeps grub-pc from prompting for a target device during install
		_run(f"chroot {MNT} sh -c \"echo 'grub-pc grub-pc/install_devices string' "
		     f"| debconf-set-selections\"")
	if config.multiarch_i386:
		_run(f"chroot {MNT} dpkg --add-architecture i386")

	msg("Installing base system...")
	packages = read_lines(TARGET_PACKAGES_PATH) + BOOT_PACKAGES[config.boot_mode]
	if config.zram:
		packages.append("systemd-zram-generator")
	if "non-free-firmware" in config.components:
		packages += read_lines(FIRMWARE_PATH)
	_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get update")
	_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get install -y "
	     f"--no-install-recommends {' '.join(packages)}")

	if config.backports_kernel:
		msg("Installing backports kernel...")
		_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get install -y "
		     f"-t {SUITE}-backports linux-image-amd64 linux-headers-amd64")

	if config.backports_mesa:
		msg("Installing backports Mesa...")
		_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get install -y "
		     f"-t {SUITE}-backports {' '.join(MESA_PACKAGES)}")

	_run(f"chroot {MNT} apt-get clean")
	ok("Base system installed")


# ── System configuration ──────────────────────────────────────────────

def _uuid(device):
	return subprocess.run(["blkid", "-s", "UUID", "-o", "value", device],
	                      capture_output=True, text=True).stdout.strip()


def configure_system(config: Config):
	msg("Configuring system...")
	write_fstab(config)

	with open(f"{MNT}/etc/hostname", "w") as hostname_file:
		hostname_file.write(config.hostname + "\n")
	with open(f"{MNT}/etc/hosts", "w") as hosts_file:
		hosts_file.write(
			f"127.0.0.1   localhost\n"
			f"127.0.1.1   {config.hostname}\n"
			f"\n"
			f"::1         localhost ip6-localhost ip6-loopback\n"
			f"ff02::1     ip6-allnodes\n"
			f"ff02::2     ip6-allrouters\n"
		)

	_run(f"sed -i 's/^# *{config.locale}/{config.locale}/' {MNT}/etc/locale.gen")
	_run(f"chroot {MNT} locale-gen")
	with open(f"{MNT}/etc/default/locale", "w") as locale_file:
		locale_file.write(f"LANG={config.locale}\n")

	_run(f"chroot {MNT} ln -sf /usr/share/zoneinfo/{config.timezone} /etc/localtime")
	with open(f"{MNT}/etc/timezone", "w") as timezone_file:
		timezone_file.write(config.timezone + "\n")

	with open(f"{MNT}/etc/default/keyboard", "w") as keyboard_file:
		keyboard_file.write(keyboard_config(config.keymap))

	with open(f"{MNT}/etc/sysctl.d/99-swappiness.conf", "w") as swappiness_file:
		swappiness_file.write(f"vm.swappiness={config.swappiness}\n")

	_run(f"chroot {MNT} systemctl enable NetworkManager systemd-timesyncd")
	copy_wifi_profiles()
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


def create_users(config: Config):
	msg("Creating users...")
	_run(f"chroot {MNT} groupadd -f lpadmin")
	_run(f"chroot {MNT} useradd -m -G sudo,audio,video,netdev,lpadmin "
	     f"-s /bin/bash {config.username}")
	subprocess.run(["chroot", MNT, "chpasswd"],
	               input=f"{config.username}:{config.user_pass}\n",
	               text=True, check=True)

	if config.root_pass:
		subprocess.run(["chroot", MNT, "chpasswd"],
		               input=f"root:{config.root_pass}\n",
		               text=True, check=True)
	else:
		_run(f"chroot {MNT} passwd -l root")
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
	branch = "" if ref == "-" else f"--branch {ref}"
	return subprocess.run(f"chroot {MNT} git clone --depth 1 {branch} -- "
	                      f"{shlex.quote(repo)} {clone_path}", shell=True).returncode == 0


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
		if subprocess.run(f"chroot {MNT} bash {clone_path}/install.sh", shell=True).returncode != 0:
			print(f"  {RED}{label} failed. Fix it from Shell, or rerun its install.sh.{NC}")
			return False
		ok(f"{label} installed")
		return True
	finally:
		_try(f"rm -rf {MNT}{clone_path}")


def install_extras(config: Config) -> list:
	custom_ids = {custom_id for custom_id, *_rest in utils.custom_rows()}
	labels = {package: label for _menu, _section, package, label, _requires in utils.extras_rows()}
	packages = [package for package in config.hardware_packages + config.software_packages
	            if package not in custom_ids]
	failed = []
	for package in packages:
		label = labels.get(package, package)
		msg(f"Installing {label}...")
		result = subprocess.run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive "
		                        f"apt-get install -y {package}", shell=True)
		if result.returncode != 0:
			print(f"  {RED}{label} failed. Fix it from Shell, or rerun: apt-get install {package}{NC}")
			failed.append(label)
		else:
			ok(f"{label} installed")
	return failed


def install_custom_software(config: Config) -> list:
	selected = set(config.hardware_packages + config.software_packages)
	failed = []
	for custom_id, _menu, _section, label, script, _requirement in utils.custom_rows():
		if custom_id not in selected:
			continue
		msg(f"Installing {label}...")
		staged_path = f"{MNT}/tmp/{script}"
		try:
			shutil.copy2(os.path.join(utils.CUSTOM_DIR, script), staged_path)
			if subprocess.run(f"chroot {MNT} sh /tmp/{script}", shell=True).returncode != 0:
				print(f"  {RED}{label} failed. Fix it from Shell, or rerun the script.{NC}")
				failed.append(label)
				continue
			ok(f"{label} installed")
		finally:
			_try(f"rm -f {staged_path}")
	return failed


# ── Bootloader ─────────────────────────────────────────────────────────

def install_bootloader(config: Config):
	msg(f"Installing GRUB ({config.boot_mode})...")
	if config.boot_mode == "uefi":
		# --removable also writes EFI/BOOT/BOOTX64.EFI so the disk boots
		# when firmware drops its NVRAM entry.
		_run(f"chroot {MNT} grub-install --target=x86_64-efi "
		     f"--efi-directory=/boot/efi "
		     f"--bootloader-id={PROFILE['PROFILE_SLUG']} "
		     f"--removable --recheck")
	else:
		_run(f"chroot {MNT} grub-install --target=i386-pc --recheck {config.disk}")
	_run(f"chroot {MNT} update-grub")
	ok("GRUB installed")


# ── Cleanup and orchestration ─────────────────────────────────────────

def cleanup():
	msg("Unmounting...")
	if os.path.isdir(f"{MNT}/var/log"):
		_try(f"cp {LOG_PATH} {MNT}/var/log/debplus-install.log")
	_try(f"umount -Rl {MNT}")
	ok("Done")


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


def _mark_done(label):
	os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
	with open(STATE_PATH, "a") as state_file:
		state_file.write(label + "\n")


def _run_stage(index, total, label, action):
	msg(f"[{index}/{total}] {label}")
	while True:
		try:
			action()
			_mark_done(label)
			return
		except Exception as error:
			_emit(f"\n  {RED}{BOLD}Error:{NC} {label} failed: {error}", sys.stderr)
			while True:
				choice = _choose("(R)etry, (S)hell, Re(b)oot, or (A)bort?", "rsba")
				if choice != "s":
					break
				_open_shell()
			if choice == "r":
				continue
			cleanup()
			if choice == "b":
				subprocess.run("reboot", shell=True)
			sys.exit(1)


def run_install(config: Config):
	_emit(f"\n  {BOLD}{CYAN}Installing {PROFILE['PROFILE_NAME']}{NC}\n")
	_try(f"hostname {config.hostname}")
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
		("Bootloader", lambda: install_bootloader(config)),
	]
	try:
		for index, (label, action) in enumerate(stages, 1):
			_run_stage(index, len(stages), label, action)
	except KeyboardInterrupt:
		cleanup()
		raise

	if failures:
		_emit(f"\n  {RED}{BOLD}Installed with errors:{NC}")
		for failure in failures:
			_emit(f"  {RED}- {failure}{NC}")
		while True:
			choice = _choose("(R)eboot, (S)hell, or (A)bort?", "rsa")
			if choice != "s":
				break
			_open_shell()
		cleanup()
		if choice == "r":
			subprocess.run("reboot", shell=True)
		return

	_emit(f"\n  {GREEN}{BOLD}Installation complete!{NC}")
	_emit("\n  Rebooting in 30 seconds. Remove the installation media if the")
	_emit("  firmware boots it first. Press Ctrl+C to stay in the live system.")
	try:
		time.sleep(30)
	except KeyboardInterrupt:
		_emit("\n  Reboot cancelled. Target is still mounted at " + MNT + ".")
		return
	cleanup()
	subprocess.run("reboot", shell=True)
