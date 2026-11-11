from dataclasses import dataclass, field

# Fields that must be set before Install unlocks. Everything else is optional.
REQUIRED_FIELDS = (
	"keymap", "timezone", "locale", "disk", "filesystem",
	"hostname", "username", "user_pass", "desktop",
)


@dataclass
class Config:
	fake_mode: bool = False  # --fake: UI only, Install disabled
	boot_mode: str = ""     # "uefi" or "bios", detected at startup
	keymap: str = ""
	timezone: str = ""
	locale: str = ""
	disk: str = ""
	filesystem: str = ""    # "btrfs", "ext4", or "xfs"
	hostname: str = ""
	username: str = ""
	user_pass: str = ""
	root_pass: str = ""     # blank disables root login
	desktop: str = ""       # id from desktops.list or wms.list, "console" for none, "custom" for a git URL
	custom_repo: str = ""   # git URL, used when desktop == "custom"
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
	manual: bool = False
	layout: dict = field(default_factory=dict)  # role -> {"device", "format"}
	zram: bool = True  # compressed swap in RAM, from systemd-zram-generator
	swap_partition: bool = True  # automatic mode only; manual uses the layout
	swappiness: int = 60
	
	def has_swap_partition(self) -> bool:
		if self.manual:
			return "swap" in self.layout
		return self.swap_partition

	def layout_ready(self) -> bool:
		if not self.manual:
			return True
		needed = {"root"} | ({"esp"} if self.boot_mode == "uefi" else set())
		return needed <= set(self.layout)

	def erase_text(self) -> str:
		if self.manual:
			return "Only the selected partitions will be formatted"
		return f"All data on {self.disk} will be erased"

	def missing_count(self) -> int:
		missing = sum(1 for name in REQUIRED_FIELDS if not getattr(self, name))
		missing += 0 if self.layout_ready() else 1
		missing += 1 if self.luks and not self.luks_pass else 0
		return missing

	def ready(self) -> bool:
		return self.missing_count() == 0

	def advanced_labels(self) -> list:
		labels = []
		if self.multiarch_i386:
			labels.append("32-bit")
		if self.backports_repo:
			labels.append("backports")
		if self.backports_kernel:
			labels.append("backports kernel")
		if self.backports_mesa:
			labels.append("backports mesa")
		if self.swappiness != 60:
			labels.append(f"swappiness {self.swappiness}")
		if not self.zram:
			labels.append("no zram")
		return labels

	def desktop_label(self) -> str:
		return self.custom_repo if self.desktop == "custom" else self.desktop
