import math
from dataclasses import dataclass, field

from .utils import (BOOT_PARTITION_GIB, ESP_RESERVE_GIB, MIN_DISK_GIB, MIN_HOME_GIB, MIRROR, disk_bytes,
                    boot_reserve_mib, dkms_packages, firewall_default_ids)

# Fields that must be set before Install unlocks. Everything else is optional.
REQUIRED_FIELDS = (
	"keymap", "timezone", "locale", "disk", "filesystem",
	"hostname", "username", "user_pass", "desktop",
)


@dataclass
class Config:
	fake_mode: bool = False  # --test: UI only, Install disabled
	boot_mode: str = ""  # "uefi" or "bios", detected at startup
	keymap: str = ""
	timezone: str = ""
	locale: str = ""
	disk: str = ""
	filesystem: str = ""  # "btrfs", "ext4", or "xfs"
	hostname: str = ""
	username: str = ""
	user_pass: str = ""
	root_pass: str = ""  # blank disables root login
	desktop: str = ""  # id from wms.list, "console" for none, "custom" for a git URL
	custom_repo: str = ""  # git URL, used when desktop == "custom"
	components: list = field(default_factory=list)
	hardware_packages: list = field(default_factory=list)
	software_packages: list = field(default_factory=list)
	multiarch_i386: bool = False
	backports_repo: bool = False
	backports_kernel: bool = False
	backports_mesa: bool = False
	luks: bool = False
	luks_pass: str = field(default="", repr=False)
	timeshift: bool = False  # snapshots with Timeshift, rsync mode
	timeshift_auto: bool = False  # scheduled daily snapshots
	timeshift_btrfs: bool = False  # btrfs snapshot mode, btrfs only
	auto_updates: bool = True  # daily security upgrades via unattended-upgrades
	mirror: str = MIRROR  # apt base URL, default from profile.env
	ufw: bool = False  # enable UFW with the rules from firewall.list
	ufw_incoming: str = "deny"
	ufw_outgoing: str = "allow"
	firewall_rules: list = field(default_factory=firewall_default_ids)  # ids from firewall.list
	git_name: str = ""
	git_email: str = ""
	git_default_branch: str = "main"
	git_editor: str = "nano"
	git_pull_rebase: str = ""  # "", "false" (merge), or "true" (rebase)
	manual: bool = False
	free_space: bool = False  # install into unallocated space, keeping existing partitions
	dual_boot: bool = False  # other systems may be present. Set during partitioning
	custom: bool = False  # custom sizes from the Partitioning screen
	custom_swap_gib: int = 0  # 0 means no swap partition
	custom_root_gib: int = 0  # 0 means not set: one root that takes the rest
	custom_home_gib: int = 0  # 0 means not set: home fills the rest when root is set
	online: bool = True  # False when there is no internet. Set through set_online()
	layout: dict = field(default_factory=dict)  # role -> {"device", "format"}
	zram: bool = True  # compressed swap in RAM, from systemd-zram-generator
	swap_size: str = "normal"  # "normal" or "small", automatic mode only
	swappiness: int = 60
	secure_boot: bool = False  # set at startup from mokutil
	mok_pass: str = field(default="", repr=False)  # one-time MOK enrollment password

	def set_online(self, online: bool):
		"""Record connectivity. Going offline drops the choices that need the network."""
		self.online = online
		if online:
			return
		if self.desktop not in ("", "console"):
			self.desktop = ""
		self.custom_repo = ""
		self.mirror = MIRROR
		self.backports_repo = self.backports_kernel = self.backports_mesa = False

	def selected_packages(self) -> list:
		"""Hardware and software entries the user chose."""
		return self.hardware_packages + self.software_packages

	def has_swap_partition(self) -> bool:
		if self.manual:
			return "swap" in self.layout
		if self.custom:
			return self.custom_swap_gib > 0
		return True

	def layout_ready(self) -> bool:
		if not self.manual:
			return True
		needed = {"root"} | ({"esp"} if self.boot_mode == "uefi" else set())
		if self.luks:
			needed.add("boot")
		return needed <= set(self.layout)

	def erase_text(self) -> str:
		if self.manual:
			return "Only the selected partitions will be formatted"
		if self.free_space:
			return f"Only unallocated space on {self.disk} will be used"
		return f"All data on {self.disk} will be erased"

	def custom_layout(self, space_mib: int, swap_gib: int, root_gib: int, home_gib: int) -> dict:
		"""Sizes the custom fields resolve to, in GiB. Root and home come back as the fixed value or the auto fill."""
		boot_gib = BOOT_PARTITION_GIB if self.luks else 0
		free_gib = (space_mib - (swap_gib + root_gib + home_gib + boot_gib) * 1024) // 1024
		root_auto = root_gib == 0
		home_exists = bool(root_gib or home_gib)
		home_auto = home_exists and home_gib == 0
		return {
			"free": free_gib,
			"root": free_gib if root_auto else root_gib,
			"home": (free_gib if home_auto else home_gib) if home_exists else None,
			"unused": 0 if (root_auto or home_auto) else max(free_gib, 0),
		}

	def custom_error(self, space_mib: int, swap_gib: int, root_gib: int, home_gib: int) -> str:
		"""Reason the custom sizes can't be used, or empty string if they can."""
		if root_gib and self.filesystem == "btrfs":
			return "A separate root needs ext4 or xfs. btrfs uses @ and @home"
		free_gib = self.custom_layout(space_mib, swap_gib, root_gib, home_gib)["free"]
		if free_gib < 0:
			return f"Sizes are {-free_gib} GB over the space available"
		if root_gib and root_gib < MIN_DISK_GIB:
			return f"Root must be at least {MIN_DISK_GIB} GB"
		if home_gib and home_gib < MIN_HOME_GIB:
			return f"Home must be at least {MIN_HOME_GIB} GB"
		if not root_gib and free_gib < MIN_DISK_GIB:
			return f"Root would be {free_gib} GB. Make swap or home smaller"
		if root_gib and not home_gib and free_gib < MIN_HOME_GIB:
			return f"Home would be {free_gib} GB. Make root or swap smaller"
		return ""

	def shared_esp(self) -> bool:
		"""EFI partition is kept without formatting, so other bootloaders may share it."""
		esp = self.layout.get("esp")
		return bool(esp) and not esp["format"]

	def missing_count(self) -> int:
		missing = sum(1 for name in REQUIRED_FIELDS if not getattr(self, name))
		missing += 0 if self.layout_ready() else 1
		missing += 1 if self.luks and not self.luks_pass else 0
		missing += 1 if self.needs_mok() and not self.mok_pass else 0
		return missing

	def ready(self) -> bool:
		return self.missing_count() == 0

	def needs_mok(self) -> bool:
		"""Secure Boot is on and a selected package builds DKMS modules."""
		words = {word for entry in self.selected_packages() for word in entry.split()}
		return self.secure_boot and bool(words & dkms_packages())

	def advanced_labels(self) -> list:
		conditions = (
			(self.multiarch_i386, "32-bit"),
			(self.backports_repo, "backports"),
			(self.backports_kernel, "backports kernel"),
			(self.backports_mesa, "backports mesa"),
			(self.swappiness != 60, f"swappiness {self.swappiness}"),
			(not self.zram, "no zram"),
			(self.mirror != MIRROR, "custom mirror"),
			(self.ufw, "ufw"),
		)
		return [label for enabled, label in conditions if enabled]

	def snapshot_label(self) -> str:
		if not self.timeshift:
			return "none"
		label = "timeshift btrfs" if self.timeshift_btrfs else "timeshift rsync"
		return label + ", auto" if self.timeshift_auto else label

	def desktop_label(self) -> str:
		return self.custom_repo if self.desktop == "custom" else self.desktop

	def auto_swap_gib(self, ram_gib: int, size: str) -> int:
		"""Small is sqrt(RAM), normal is RAM plus sqrt(RAM). Both at least 2 GB."""
		root_part = max(math.ceil(math.sqrt(ram_gib)), 2)
		return root_part if size == "small" else ram_gib + root_part

	def auto_min_disk_gib(self, ram_gib: int, size: str) -> int:
		"""Smallest disk that fits automatic swap of this size, with root and boot reserves."""
		reserve = MIN_DISK_GIB + (ESP_RESERVE_GIB if self.boot_mode == "uefi" else 0)
		reserve += BOOT_PARTITION_GIB if self.luks else 0
		return reserve + self.auto_swap_gib(ram_gib, size)

	def swap_gib_planned(self, ram_gib: int) -> int:
		"""Swap size this install will create, in GB. Manual reads the chosen partition."""
		if self.manual:
			part = self.layout.get("swap")
			return disk_bytes(part["device"]) // 2**30 if part else 0
		if self.custom:
			return self.custom_swap_gib
		return self.auto_swap_gib(ram_gib, self.swap_size)
