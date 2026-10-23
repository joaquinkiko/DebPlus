# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os
import shutil
import subprocess
import sys
import time

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


def msg(text):
	print(f"\n  {CYAN}{BOLD}::{NC} {BOLD}{text}{NC}")


def ok(text):
	print(f"  {GREEN}{text}{NC}")


def die(text):
	print(f"\n  {RED}{BOLD}Error:{NC} {text}\n", file=sys.stderr)
	sys.exit(1)


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


def partition_disk(config: Config):
	msg(f"Partitioning {config.disk}...")
	uefi = config.boot_mode == "uefi"
	fs_hint = PARTED_FS_HINT[config.filesystem]

	_run(f"wipefs -af {config.disk}", quiet=True)
	if uefi:
		layout = (f"mklabel gpt mkpart ESP fat32 1MiB 513MiB set 1 esp on "
		          f"mkpart root {fs_hint} 513MiB 100%")
	else:
		layout = f"mklabel msdos mkpart primary {fs_hint} 1MiB 100% set 1 boot on"
	_run(f"parted -s {config.disk} {layout}", quiet=True)
	_try(f"partprobe {config.disk}")
	time.sleep(1)

	parts = get_partitions(config.disk)
	esp_part, root_part = (parts[0], parts[1]) if uefi else (None, parts[0])

	msg("Formatting...")
	if uefi:
		_run(f"mkfs.fat -F32 {esp_part}")
	_make_filesystem(root_part, config.filesystem)

	os.makedirs(MNT, exist_ok=True)
	_run(f"mount {root_part} {MNT}")
	if uefi:
		os.makedirs(f"{MNT}/boot/efi", exist_ok=True)
		_run(f"mount {esp_part} {MNT}/boot/efi")
	ok("Partitioned and mounted")


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

	if config.backports_repo or config.backports_kernel:
		stanzas.append(
			f"Types: deb\nURIs: {MIRROR}\nSuites: {SUITE}-backports\n"
			f"Components: {components}\n{keyring}\n"
		)
		os.makedirs(f"{MNT}/etc/apt/preferences.d", exist_ok=True)
		with open(f"{MNT}/etc/apt/preferences.d/backports", "w") as pin_file:
			pin_file.write(f"Package: linux-image-amd64 linux-headers-amd64\n"
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
	if "non-free-firmware" in config.components:
		packages += read_lines(FIRMWARE_PATH)
	_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get update")
	_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get install -y "
	     f"--no-install-recommends {' '.join(packages)}")

	if config.backports_kernel:
		msg("Installing backports kernel...")
		_run(f"chroot {MNT} env DEBIAN_FRONTEND=noninteractive apt-get install -y "
		     f"-t {SUITE}-backports linux-image-amd64 linux-headers-amd64")

	_run(f"chroot {MNT} apt-get clean")
	ok("Base system installed")


# ── System configuration ──────────────────────────────────────────────

def _uuid(device):
	return subprocess.run(["blkid", "-s", "UUID", "-o", "value", device],
	                      capture_output=True, text=True).stdout.strip()


def write_fstab(config: Config):
	parts = get_partitions(config.disk)
	uefi = config.boot_mode == "uefi"
	esp_part, root_part = (parts[0], parts[1]) if uefi else (None, parts[0])

	lines = ["# <filesystem>  <mount>  <type>  <options>  <dump>  <pass>",
	         f"UUID={_uuid(root_part)}  /  {config.filesystem}  "
	         f"{FSTAB_OPTIONS[config.filesystem]}  0  1"]
	if uefi:
		lines.append(f"UUID={_uuid(esp_part)}  /boot/efi  vfat  umask=0077  0  2")

	with open(f"{MNT}/etc/fstab", "w") as fstab_file:
		fstab_file.write("\n".join(lines) + "\n")


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
	return subprocess.run(f"chroot {MNT} git clone --depth 1 {branch} {repo} {clone_path}",
	                      shell=True).returncode == 0


def install_window_manager(config: Config) -> bool:
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
	_try(f"umount -Rl {MNT}")
	ok("Done")


def _failure_prompt():
	"""Offer reboot or a chrooted shell after a failed install."""
	while True:
		try:
			choice = input(f"\n  {BOLD}(R)eboot or (S)hell?{NC} ").strip().lower()
		except (EOFError, KeyboardInterrupt):
			print(f"\n  Target remains mounted at {MNT}.")
			return
		if choice in ("r", "reboot"):
			cleanup()
			subprocess.run("reboot", shell=True)
			return
		if choice in ("s", "shell"):
			print(f"\n  Chrooted into {MNT}. Type 'exit' to return.\n")
			subprocess.run(["chroot", MNT, "/bin/bash"])
		else:
			print("  Enter R or S.")


def run_install(config: Config):
	print(f"\n  {BOLD}{CYAN}Installing {PROFILE['PROFILE_NAME']}{NC}\n")
	_try(f"hostname {config.hostname}")

	failures = []
	try:
		partition_disk(config)
		install_base(config)
		configure_system(config)
		create_users(config)
		if not install_window_manager(config):
			failures.append("Window manager")
		failures += install_extras(config)
		failures += install_custom_software(config)
		install_bootloader(config)
	except BaseException:
		cleanup()
		raise

	if failures:
		print(f"\n  {RED}{BOLD}Installed with errors:{NC}")
		for failure in failures:
			print(f"  {RED}- {failure}{NC}")
		_failure_prompt()
		return

	print(f"\n  {GREEN}{BOLD}Installation complete!{NC}")
	print("\n  Rebooting in 30 seconds. Remove the installation media if the")
	print("  firmware boots it first. Press Ctrl+C to stay in the live system.")
	try:
		time.sleep(30)
	except KeyboardInterrupt:
		print("\n  Reboot cancelled. Target is still mounted at " + MNT + ".")
		return
	cleanup()
	subprocess.run("reboot", shell=True)
