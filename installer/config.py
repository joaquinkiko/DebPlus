from dataclasses import dataclass, field

from .utils import ESP_RESERVE_GIB, MIN_DISK_GIB, MIN_HOME_GIB, dkms_packages

# Fields that must be set before Install unlocks. Everything else is optional.
REQUIRED_FIELDS = (
	"keymap", "timezone", "locale", "disk", "filesystem",
	"hostname", "username", "user_pass", "desktop",
)


@dataclass
class Config:
	fake_mode: bool = False  # --fake: UI only, Install disabled
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
	snapper: bool = False  # btrfs only
	auto_updates: bool = True  # daily security upgrades via unattended-upgrades
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
	swap_partition: bool = True  # automatic mode only; manual uses the layout
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
		self.backports_repo = self.backports_kernel = self.backports_mesa = False

	def selected_packages(self) -> list:
		"""Hardware and software entries the user chose."""
		return self.hardware_packages + self.software_packages

	def has_swap_partition(self) -> bool:
		if self.manual:
			return "swap" in self.layout
		if self.custom:
			return self.custom_swap_gib > 0
		return self.swap_partition

	def layout_ready(self) -> bool:
		if not self.manual:
			return True
		needed = {"root"} | ({"esp"} if self.boot_mode == "uefi" else set())
		return needed <= set(self.layout)

	def erase_text(self) -> str:
		if self.manual:
			return "Only the selected partitions will be formatted"
		if self.free_space:
			return f"Only unallocated space on {self.disk} will be used"
		return f"All data on {self.disk} will be erased"

	def custom_free_gib(self, space_mib: int, swap_gib: int, root_gib: int, home_gib: int) -> int:
		"""GiB left in space_mib after swap and any fixed root or home. Negative means over."""
		used_mib = (swap_gib + root_gib + home_gib) * 1024
		return (space_mib - used_mib) // 1024

	def custom_error(self, space_mib: int, swap_gib: int, root_gib: int, home_gib: int) -> str:
		"""Reason the custom sizes can't be used, or empty string if they can."""
		if root_gib and self.filesystem == "btrfs":
			return "A separate root needs ext4 or xfs. btrfs uses @ and @home"
		free_gib = self.custom_free_gib(space_mib, swap_gib, root_gib, home_gib)
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
		)
		return [label for enabled, label in conditions if enabled]

	def desktop_label(self) -> str:
		return self.custom_repo if self.desktop == "custom" else self.desktop

	def swap_gib(self, ram_gib: int, disk_gib: int) -> int:
		"""Swap equal to RAM, limited so root keeps its minimum. 0 means no room."""
		reserve = MIN_DISK_GIB + (ESP_RESERVE_GIB if self.boot_mode == "uefi" else 0)
		return max(0, min(ram_gib, disk_gib - reserve))
