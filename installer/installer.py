# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

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
from .utils import MNT, MIRROR, PROFILE, get_partitions, keyboard_config

BOLD = "\033[1m"
RED = "\033[0;31m"
GREEN = "\033[0;32m"
CYAN = "\033[0;36m"
NC = "\033[0m"

SUITE = PROFILE["SUITE"]
KEYRING_LINE = "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg"
PARTED_FS_HINT = {"btrfs": "btrfs", "ext4": "ext4", "xfs": "ext2"}
FSTAB_OPTIONS = {
	"btrfs": "defaults,compress=zstd,noatime",
	"ext4": "errors=remount-ro",
	"xfs": "defaults",
}

LOG_PATH = "/var/log/debplus/install.log"
STATE_PATH = "/run/debplus/state"
ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")

LUKS_ROOT = "cryptroot"
LUKS_SWAP = "cryptswap"
SWAP_KEY_LIVE = "/run/debplus/swap.key"

LOCAL_REPO_MOUNT = "/media/debplus-repo"  # inside the target, only during install
APT = "env DEBIAN_FRONTEND=noninteractive apt-get"
# Offline apt: read only the local repo. Check-Date off because an offline RTC is often wrong.
LOCAL_APT = ("-o Dir::Etc::sourcelist=debplus-local.list -o Dir::Etc::sourceparts=- "
             "-o APT::Sandbox::User=root -o Acquire::Check-Date=false")

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


def _run(command, quiet=False):
	result = subprocess.run(command, shell=True, capture_output=quiet, text=True)
	if result.returncode != 0:
		detail = f"\n{result.stderr}" if quiet else ""
		die(f"Command failed: {command}{detail}")
	return result


def _try(command):
	subprocess.run(command, shell=True, capture_output=True)


def _chroot(command, quiet=False):
	return _run(f"chroot {MNT} {command}", quiet)


def _chroot_ok(command):
	"""Run a chroot command and return success instead of raising."""
	return subprocess.run(f"chroot {MNT} {command}", shell=True).returncode == 0


def _apt(arguments, quiet=False):
	return _chroot(f"{APT} {arguments}", quiet)


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


def _unmount(path, flags=""):
	"""Unmount quietly. flags carries a trailing space when given, e.g. "-l "."""
	_try(f"umount {flags}{path}")


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
	disk_mib = utils.disk_bytes(config.disk) // 2**20
	disk_gib = disk_mib // 1024
	swap_gib = config.swap_gib(ram_gib, disk_gib) if config.has_swap_partition() else 0
	swap = swap_gib > 0
	swap_start = f"{disk_mib - swap_gib * 1024}MiB"
	steps = ["mklabel gpt"]
	if uefi:
		roles = ["esp", "root"]
		steps.append("mkpart ESP fat32 1MiB 513MiB set 1 esp on")
		root_start = "513MiB"
	else:
		roles = ["bios_grub", "root"]
		steps.append("mkpart bios_grub 1MiB 2MiB set 1 bios_grub on")
		root_start = "2MiB"
	root_end = swap_start if swap else "100%"
	steps.append(f"mkpart root {filesystem_hint} {root_start} {root_end}")
	if swap:
		roles.append("swap")
		steps.append(f"mkpart swap linux-swap {swap_start} 100%")
	_run(f"wipefs -af {config.disk}", quiet=True)
	_run(f"parted -s {config.disk} {' '.join(steps)}", quiet=True)
	partitions = _wait_for_partitions(config.disk, len(roles))
	config.layout = {role: {"device": device, "format": role != "bios_grub"}
	                 for role, device in zip(roles, partitions)}


def _format_layout(config: Config):
	msg("Formatting...")
	for role, part in config.layout.items():
		if not part["format"] or (role == "swap" and config.luks):
			continue
		device = _device(config, role)
		if role == "esp":
			_run(f"mkfs.fat -F32 {device}")
		elif role == "swap":
			_run(f"mkswap {device}")
		elif role in ("root", "home"):
			_make_filesystem(device, config.filesystem)


def _mount_layout(config: Config):
	root = _device(config, "root")
	btrfs = config.filesystem == "btrfs"
	os.makedirs(MNT, exist_ok=True)
	if btrfs:
		_run(f"mount {root} {MNT}")
		_run(f"btrfs subvolume create {MNT}/@")
		if "home" not in config.layout:
			_run(f"btrfs subvolume create {MNT}/@home")
		_run(f"umount {MNT}")
	subvolume_option = "-o subvol=@ " if btrfs else ""
	_run(f"mount {subvolume_option}{root} {MNT}")


def mount_secondary(config: Config):
	"""Mount /home and the ESP. Runs after extraction so the image never writes onto them."""
	layout = config.layout
	root = _device(config, "root")
	home = None
	if "home" in layout:
		home = layout["home"]["device"]
	elif config.filesystem == "btrfs":
		home = f"-o subvol=@home {root}"
	if home and not os.path.ismount(f"{MNT}/home"):
		os.makedirs(f"{MNT}/home", exist_ok=True)
		_run(f"mount {home} {MNT}/home")
	if "esp" in layout and not os.path.ismount(f"{MNT}/boot/efi"):
		os.makedirs(f"{MNT}/boot/efi", exist_ok=True)
		_run(f"mount {layout['esp']['device']} {MNT}/boot/efi")


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
	_try(f"swapoff /dev/mapper/{LUKS_SWAP}")
	_try(f"cryptsetup close {LUKS_SWAP}")
	_try(f"cryptsetup close {LUKS_ROOT}")


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
		_run(f"mkswap /dev/mapper/{LUKS_SWAP}")


def partition_disk(config: Config):
	_unmount(MNT, "-Rl ")
	_close_mappers()
	if config.manual:
		msg("Using selected partitions...")
	else:
		msg(f"Partitioning {config.disk}...")
		_partition_auto(config)
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
	for command in (f"mount --bind /dev {MNT}/dev",
	                f"mount --bind /dev/pts {MNT}/dev/pts",
	                f"mount -t proc proc {MNT}/proc",
	                f"mount -t sysfs sys {MNT}/sys"):
		_run(command)
	if os.path.isdir("/sys/firmware/efi/efivars"):
		_try(f"mount --bind /sys/firmware/efi/efivars {MNT}/sys/firmware/efi/efivars")


# ── Apt sources ────────────────────────────────────────────────────────

def _apt_stanza(uri, suites, components):
	return f"Types: deb\nURIs: {uri}\nSuites: {suites}\nComponents: {components}\n{KEYRING_LINE}\n"


def _write_backports_pins(config: Config):
	"""Keep backports opt-in: pin it low, then raise only the packages the user ticked."""
	pin_blocks = [f"Package: *\nPin: release a={SUITE}-backports\nPin-Priority: 100\n"]
	if config.backports_kernel:
		pin_blocks.append(f"Package: linux-image-amd64 linux-headers-amd64\n"
		                  f"Pin: release a={SUITE}-backports\nPin-Priority: 990\n")
	if config.backports_mesa:
		# Glob catches the Mesa libraries and drivers; libgbm1 has no "mesa" in its name
		pin_blocks.append(f"Package: *mesa* libgbm1\n"
		                  f"Pin: release a={SUITE}-backports\nPin-Priority: 990\n")
	os.makedirs(f"{MNT}/etc/apt/preferences.d", exist_ok=True)
	_write_target("/etc/apt/preferences.d/backports", "\n".join(pin_blocks))


def write_apt_sources(config: Config):
	components = " ".join(["main"] + config.components)
	stanzas = [
		_apt_stanza(MIRROR, f"{SUITE} {SUITE}-updates", components),
		_apt_stanza("http://security.debian.org/debian-security", f"{SUITE}-security", components),
	]
	if config.backports_repo or config.backports_kernel or config.backports_mesa:
		stanzas.append(_apt_stanza(MIRROR, f"{SUITE}-backports", components))
		_write_backports_pins(config)
	_write_target("/etc/apt/sources.list.d/debian.sources", "\n".join(stanzas))
	_write_target("/etc/apt/sources.list", "")


# ── Base system ────────────────────────────────────────────────────────

def packages_to_install(config: Config) -> list:
	"""Packages beyond the base image: bootloader, chosen options, shipped extras."""
	wanted = set(utils.boot_packages(config.boot_mode))
	wanted |= {package for package, flag in utils.optional_rows() if getattr(config, flag)}
	if not config.backports_kernel:  # backports path installs matching headers below
		wanted |= set(utils.header_packages())
	for entry in config.hardware_packages + config.software_packages:
		if not utils.needs_network(entry):  # shipped on the ISO. The rest go through install_extras
			wanted |= set(entry.split())
	return sorted(wanted)


def _unmount_all():
	"""Release chroot and secondary mounts so a retried extraction starts clean."""
	for sub in ("sys/firmware/efi/efivars", "sys", "proc", "dev/pts", "dev", "boot/efi", "home"):
		_unmount(f"{MNT}/{sub}", "-l ")


def reset_identity():
	"""The image is shared by every install, so give this one its own identity."""
	msg("Resetting machine identity...")
	_write_target("/etc/machine-id", "")  # empty: systemd generates a new id on first boot
	_try(f"rm -f {MNT}/var/lib/dbus/machine-id")
	_try(f"ln -s /etc/machine-id {MNT}/var/lib/dbus/machine-id")
	_try(f"rm -f {MNT}/etc/ssh/ssh_host_*")
	_try(f"chroot {MNT} sh -c 'dpkg -s openssh-server >/dev/null 2>&1 && "
	     f"dpkg-reconfigure -f noninteractive openssh-server'")


def _install_from_local_repo(packages):
	"""Offline install: apt reads only the repo bind-mounted from the install medium."""
	repo = utils.target_repo()
	if not repo:
		die("Package repo not found on the install medium.")
	msg("Installing selected packages from the install medium...")
	mount_point = f"{MNT}{LOCAL_REPO_MOUNT}"
	os.makedirs(mount_point, exist_ok=True)
	_run(f"mount --bind {shlex.quote(repo)} {mount_point}")
	try:
		_write_target("/etc/apt/debplus-local.list", f"deb [trusted=yes] file:{LOCAL_REPO_MOUNT} ./\n")
		_apt(f"{LOCAL_APT} update", quiet=True)
		_apt(f"{LOCAL_APT} install -y --no-install-recommends {' '.join(packages)}", quiet=True)
	finally:
		_unmount(mount_point)
		_try(f"rmdir {mount_point}")
		_try(f"rm -f {MNT}/etc/apt/debplus-local.list")
		_try(f"rm -rf {MNT}/var/lib/apt/lists/*")


def install_packages(config: Config):
	"""Install the packages this install needs, then bring the system up to date if online."""
	packages = packages_to_install(config)
	if config.boot_mode == "bios":
		# Empty install_devices keeps grub-pc from prompting. The bootloader stage installs GRUB.
		_chroot("sh -c \"echo 'grub-pc grub-pc/install_devices string' | debconf-set-selections\"")
	if not config.online:
		if packages:
			_install_from_local_repo(packages)
		return
	msg("Updating packages...")
	_apt("update")
	if packages:
		_apt(f"install -y --no-install-recommends {' '.join(packages)}")
	_apt("upgrade -y")
	if config.backports_kernel:
		msg("Installing backports kernel...")
		_apt(f"install -y -t {SUITE}-backports linux-image-amd64 linux-headers-amd64")
	if config.backports_mesa:
		msg("Installing backports Mesa...")
		_apt(f"install -y -t {SUITE}-backports {' '.join(utils.mesa_packages())}")
	_chroot("apt-get clean")


def install_base(config: Config):
	image = utils.target_image()
	if not image:
		die("Base system image not found on the install medium.")
	_unmount_all()
	msg("Extracting base system (this will take a while)...")
	_run(f"unsquashfs -f -n -d {MNT} {shlex.quote(image)}")
	_try(f"rm -f {MNT}/etc/resolv.conf")
	shutil.copy2("/etc/resolv.conf", f"{MNT}/etc/resolv.conf")
	mount_secondary(config)
	write_apt_sources(config)
	mount_chroot_fs()
	reset_identity()
	if config.multiarch_i386:
		_chroot("dpkg --add-architecture i386")
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


def configure_snapper():
	msg("Configuring snapper...")
	_chroot("snapper -c root create-config /")
	_chroot("snapper -c root create --description post-install")
	ok("Snapper configured")


def configure_auto_updates():
	msg("Enabling security updates...")
	_write_target("/etc/apt/apt.conf.d/20auto-upgrades",
	              'APT::Periodic::Update-Package-Lists "1";\n'
	              'APT::Periodic::Unattended-Upgrade "1";\n')
	ok("Security updates enabled")


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

	_run(f"sed -i 's/^# *{config.locale}/{config.locale}/' {MNT}/etc/locale.gen")
	_chroot("locale-gen")
	_write_target("/etc/default/locale", f"LANG={config.locale}\n")

	_chroot(f"ln -sf /usr/share/zoneinfo/{config.timezone} /etc/localtime")
	_write_target("/etc/timezone", config.timezone + "\n")
	_write_target("/etc/default/keyboard", keyboard_config(config.keymap))
	_write_target("/etc/sysctl.d/99-swappiness.conf", f"vm.swappiness={config.swappiness}\n")

	_chroot("systemctl enable NetworkManager systemd-timesyncd")
	copy_wifi_profiles()

	if config.luks:
		configure_encryption(config)
	if config.snapper:
		configure_snapper()
	if config.auto_updates:
		configure_auto_updates()

	msg("Rebuilding initramfs...")  # fstab and crypttab exist now
	_chroot("update-initramfs -u -k all")
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
	_chroot("groupadd -f lpadmin")
	user_known = subprocess.run(["chroot", MNT, "id", config.username],
	                            capture_output=True).returncode == 0
	if not user_known:
		_chroot(f"useradd -m -G sudo,audio,video,netdev,lpadmin -s /bin/bash {config.username}")
	_chpasswd(f"{config.username}:{config.user_pass}")
	if config.root_pass:
		_chpasswd(f"root:{config.root_pass}")
	else:
		_chroot("passwd -l root")
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
	return _chroot_ok(f"git clone --depth 1 {branch} -- {shlex.quote(repo)} {clone_path}")


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
		return _report(label, _chroot_ok(f"bash {clone_path}/install.sh"),
		               "Fix it from Shell, or rerun its install.sh.")
	finally:
		_try(f"rm -rf {MNT}{clone_path}")


def install_extras(config: Config) -> list:
	custom_ids = {custom_id for custom_id, *_rest in utils.custom_rows()}
	labels = {package: label for _menu, _section, package, label, _requires in utils.extras_rows()}
	failed = []
	for package in config.hardware_packages + config.software_packages:
		if package in custom_ids or not utils.needs_network(package):
			continue  # custom scripts run separately; local-repo packages are already installed
		label = labels.get(package, package)
		msg(f"Installing {label}...")
		if not _report(label, _chroot_ok(f"{APT} install -y {package}"),
		               f"Fix it from Shell, or rerun: apt-get install {package}"):
			failed.append(label)
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
			if not _report(label, _chroot_ok(f"sh /tmp/{script}"),
			               "Fix it from Shell, or rerun the script."):
				failed.append(label)
		finally:
			_try(f"rm -f {staged_path}")
	return failed


# ── Bootloader ─────────────────────────────────────────────────────────

def _install_removable_fallback():
	"""Copy shim and GRUB to EFI/BOOT so the disk boots if firmware drops its NVRAM entry."""
	source = f"{MNT}/boot/efi/EFI/debian"
	target = f"{MNT}/boot/efi/EFI/BOOT"
	os.makedirs(target, exist_ok=True)
	shutil.copy2(f"{source}/shimx64.efi", f"{target}/BOOTX64.EFI")
	for name in ("grubx64.efi", "mmx64.efi"):
		shutil.copy2(f"{source}/{name}", f"{target}/{name}")


def install_bootloader(config: Config):
	msg(f"Installing bootloader ({config.boot_mode})...")
	if config.boot_mode == "uefi":
		_chroot("grub-install --target=x86_64-efi --efi-directory=/boot/efi "
		        "--bootloader-id=debian --recheck")
		if not os.path.isfile(f"{MNT}/boot/efi/EFI/debian/shimx64.efi"):
			die("Signed shim was not installed to the EFI partition.")
		_install_removable_fallback()
	else:
		_chroot(f"grub-install --target=i386-pc --recheck {config.disk}")
	_chroot("update-grub")
	ok("Bootloader installed")


def configure_secure_boot(config: Config):
	if not config.needs_mok():
		return
	msg("Setting up Secure Boot signing...")
	key_dir = f"{MNT}/var/lib/dkms"
	os.makedirs(key_dir, exist_ok=True)
	_chroot("openssl req -new -x509 -newkey rsa:2048 -nodes -days 36500 "
	        "-subj '/CN=DebPlus Secure Boot/' -keyout /var/lib/dkms/mok.key "
	        "-out /var/lib/dkms/mok.pub")
	os.chmod(f"{key_dir}/mok.key", 0o600)
	_chroot("openssl x509 -outform DER -in /var/lib/dkms/mok.pub -out /var/lib/dkms/mok.der")
	_write_target("/etc/dkms/framework.conf",
	              "mok_signing_key=/var/lib/dkms/mok.key\n"
	              "mok_certificate=/var/lib/dkms/mok.pub\n", append=True)
	modules_dir = f"{MNT}/lib/modules"
	for version in sorted(os.listdir(modules_dir)) if os.path.isdir(modules_dir) else []:
		if os.path.isdir(f"{MNT}/usr/src/linux-headers-{version}"):
			_chroot(f"dkms autoinstall -k {shlex.quote(version)}")
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
		_try(f"cp {LOG_PATH} {MNT}/var/log/debplus-install.log")
	_unmount(MNT, "-Rl ")
	_try(f"rm -f {SWAP_KEY_LIVE}")
	ok("Done")


def _reboot():
	subprocess.run("reboot", shell=True)


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
		("Secure Boot", lambda: configure_secure_boot(config)),
		("Bootloader", lambda: install_bootloader(config)),
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
