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
	desktop: str = ""       # id from desktops.list or wms.list, "console" for none
	components: list = field(default_factory=list)
	hardware_packages: list = field(default_factory=list)
	software_packages: list = field(default_factory=list)
	multiarch_i386: bool = False
	backports_repo: bool = False
	backports_kernel: bool = False
	backports_mesa: bool = False

	def missing_count(self) -> int:
		return sum(1 for name in REQUIRED_FIELDS if not getattr(self, name))

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
		return labels
