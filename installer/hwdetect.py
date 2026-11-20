# DebPlus: hardware detection for the splash screen. Reads sysfs and /proc directly.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import glob
import os

from . import utils

LAPTOP_CHASSIS = {"8", "9", "10", "14", "31", "32"}
PCI_GPU_CLASS = "0x03"
PCI_AUDIO_CLASSES = ("0x0401", "0x0403")
NVIDIA_VENDOR = "10de"
# Device id prefixes of Turing and newer, which need the open kernel module.
NVIDIA_OPEN_PREFIXES = ("1e", "1f", "20", "21", "22", "23", "24", "25", "26", "27", "28",
                        "2b", "2c", "2d", "2f")
# Broadcom device ids the wl driver (broadcom-sta) supports. Verify against lspci -nn before release.
BROADCOM_VENDOR = "14e4"
BROADCOM_LEGACY_IDS = frozenset({"4311", "4312", "4313", "4315", "4318", "4319", "4320",
                                 "4321", "4322", "4324", "4331", "4353", "4357", "4358",
                                 "4359", "4360"})
SMARTCARD_INTERFACE_CLASS = "0b"
# Triggers skipped on a virtual machine, where hardware is emulated.
VM_SKIPPED = frozenset({"gpu", "nvidia-open", "nvidia-legacy", "wifi", "broadcom-legacy",
                        "bluetooth", "audio", "usb", "thunderbolt"})


def _read(path):
	"""Lowercase, stripped contents of a sysfs file, or empty string if unreadable."""
	try:
		with open(path) as sysfs_file:
			return sysfs_file.read().strip().lower()
	except OSError:
		return ""


def _lines(path):
	try:
		with open(path) as text_file:
			return text_file.readlines()
	except OSError:
		return []


def _cpu_vendor(root):
	for line in _lines(os.path.join(root, "proc/cpuinfo")):
		if line.startswith("vendor_id"):
			return line.split(":", 1)[1].strip().lower()
	return ""


def _is_vm(root):
	"""True when the CPU advertises a hypervisor."""
	for line in _lines(os.path.join(root, "proc/cpuinfo")):
		if line.startswith("flags"):
			return "hypervisor" in line.split()
	return False


def _pci_detections(root):
	"""(trigger, vendor) for each GPU and audio device, plus NVIDIA generation and Broadcom legacy."""
	found = []
	for device in glob.glob(os.path.join(root, "sys/bus/pci/devices/*")):
		device_class = _read(os.path.join(device, "class"))
		vendor = _read(os.path.join(device, "vendor")).removeprefix("0x")
		product = _read(os.path.join(device, "device")).removeprefix("0x")
		if device_class.startswith(PCI_GPU_CLASS):
			found.append(("gpu", vendor))
			if vendor == NVIDIA_VENDOR:
				generation = "nvidia-open" if product.startswith(NVIDIA_OPEN_PREFIXES) else "nvidia-legacy"
				found.append((generation, "*"))
		elif device_class.startswith(PCI_AUDIO_CLASSES):
			found.append(("audio", vendor))
	return found


def _wifi_ids(root, interface):
	"""(vendor, product) of a Wi-Fi interface, from its PCI device or its USB parent."""
	device = os.path.realpath(os.path.join(root, "sys/class/net", interface, "device"))
	for directory in (device, os.path.dirname(device)):
		vendor = _read(os.path.join(directory, "vendor")) or _read(os.path.join(directory, "idVendor"))
		if vendor:
			product = _read(os.path.join(directory, "device")) or _read(os.path.join(directory, "idProduct"))
			return vendor.removeprefix("0x"), product.removeprefix("0x")
	return "", ""


def _wifi_detections(root):
	found = []
	for path in glob.glob(os.path.join(root, "sys/class/net/*/wireless")):
		vendor, product = _wifi_ids(root, os.path.basename(os.path.dirname(path)))
		if not vendor:
			continue
		found.append(("wifi", vendor))
		if vendor == BROADCOM_VENDOR and product in BROADCOM_LEGACY_IDS:
			found.append(("broadcom-legacy", "*"))
	return found


def _usb_detections(root):
	"""(usb, vendor) per USB device, and (smartcard, *) for any smart card interface."""
	found = [("usb", _read(path))
	         for path in glob.glob(os.path.join(root, "sys/bus/usb/devices/*/idVendor"))]
	for path in glob.glob(os.path.join(root, "sys/bus/usb/devices/*/bInterfaceClass")):
		if _read(path) == SMARTCARD_INTERFACE_CLASS:
			found.append(("smartcard", "*"))
	return found


def _is_laptop(root):
	chassis = _read(os.path.join(root, "sys/class/dmi/id/chassis_type"))
	batteries = glob.glob(os.path.join(root, "sys/class/power_supply/BAT*"))
	return chassis in LAPTOP_CHASSIS or bool(batteries)


def _has_thunderbolt(root):
	return bool(glob.glob(os.path.join(root, "sys/bus/thunderbolt/devices/*")))


def _has_sensors(root):
	"""True when any hwmon device other than NVMe reports."""
	names = [_read(path) for path in glob.glob(os.path.join(root, "sys/class/hwmon/*/name"))]
	return any(name and name != "nvme" for name in names)


def detect(root="/"):
	"""(trigger, identifier) pairs found under root. Identifier is lowercase, or '*' for any."""
	found = [("cpu", _cpu_vendor(root))]
	found += _pci_detections(root)
	found += _wifi_detections(root)
	found += _usb_detections(root)
	if glob.glob(os.path.join(root, "sys/class/bluetooth/hci*")):
		found.append(("bluetooth", "*"))
	if _is_laptop(root):
		found.append(("laptop", "*"))
	if _is_vm(root):
		found.append(("vm", "*"))
	if _has_thunderbolt(root):
		found.append(("thunderbolt", "*"))
	if sum(1 for trigger, _identifier in found if trigger == "gpu") > 1:
		found.append(("hybrid", "*"))
	if _has_sensors(root):
		found.append(("sensors", "*"))
	return [(trigger, identifier) for trigger, identifier in found if identifier]


def plan(detections):
	"""(recommended, suggested): (package, label) pairs from matching rules, one entry per package."""
	labels = {package: label for _menu, _section, package, label, _requires in utils.extras_rows()}
	in_vm = any(trigger == "vm" for trigger, _identifier in detections)
	recommended = []
	suggested = []
	seen = set()
	for row_trigger, row_match, package, mode in utils.autodetect_rows():
		if in_vm and row_trigger in VM_SKIPPED:
			continue
		hit = row_trigger == "always" or any(
			row_trigger == trigger and row_match.lower() in ("*", identifier)
			for trigger, identifier in detections)
		if not hit or package in seen:
			continue
		seen.add(package)
		target = recommended if mode == "add" else suggested
		target.append((package, labels.get(package, package)))
	return recommended, suggested


def apply(config, packages):
	"""Add packages to hardware, ticking the components and multiarch they need."""
	component_ids = {component_id for component_id, _label, _description in utils.components_rows()}
	requirements = utils.package_requirements()
	for package in packages:
		if package not in config.hardware_packages:
			config.hardware_packages.append(package)
		for requirement in requirements.get(package, "-").split(","):
			if requirement in component_ids and requirement not in config.components:
				config.components.append(requirement)
			elif requirement == "i386":
				config.multiarch_i386 = True
