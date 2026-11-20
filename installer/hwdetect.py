# DebPlus: hardware detection for the splash screen. Reads sysfs and /proc directly.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import glob
import os

from . import utils

LAPTOP_CHASSIS = {"8", "9", "10", "14", "31", "32"}
PCI_GPU_CLASS = "0x03"
PCI_AUDIO_CLASSES = ("0x0401", "0x0403")


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


def _pci_detections(root):
	"""(trigger, vendor) for each GPU and audio device on the PCI bus."""
	found = []
	for device in glob.glob(os.path.join(root, "sys/bus/pci/devices/*")):
		device_class = _read(os.path.join(device, "class"))
		vendor = _read(os.path.join(device, "vendor")).removeprefix("0x")
		if device_class.startswith(PCI_GPU_CLASS):
			found.append(("gpu", vendor))
		elif device_class.startswith(PCI_AUDIO_CLASSES):
			found.append(("audio", vendor))
	return found


def _wifi_vendor(root, interface):
	"""Vendor id of a Wi-Fi interface, from its PCI device or its USB parent."""
	device = os.path.realpath(os.path.join(root, "sys/class/net", interface, "device"))
	for directory in (device, os.path.dirname(device)):
		for name in ("vendor", "idVendor"):
			vendor = _read(os.path.join(directory, name)).removeprefix("0x")
			if vendor:
				return vendor
	return ""


def _wifi_detections(root):
	return [("wifi", _wifi_vendor(root, os.path.basename(os.path.dirname(path))))
	        for path in glob.glob(os.path.join(root, "sys/class/net/*/wireless"))]


def _is_laptop(root):
	chassis = _read(os.path.join(root, "sys/class/dmi/id/chassis_type"))
	batteries = glob.glob(os.path.join(root, "sys/class/power_supply/BAT*"))
	return chassis in LAPTOP_CHASSIS or bool(batteries)


def _has_sensors(root):
	"""True when any hwmon device other than NVMe reports."""
	names = [_read(path) for path in glob.glob(os.path.join(root, "sys/class/hwmon/*/name"))]
	return any(name and name != "nvme" for name in names)


def detect(root="/"):
	"""(trigger, identifier) pairs found under root. Identifier is lowercase, or '*' for any."""
	found = [("cpu", _cpu_vendor(root))]
	found += _pci_detections(root)
	found += _wifi_detections(root)
	if glob.glob(os.path.join(root, "sys/class/bluetooth/hci*")):
		found.append(("bluetooth", "*"))
	if _is_laptop(root):
		found.append(("laptop", "*"))
		gpu_vendors = {vendor for trigger, vendor in found if trigger == "gpu"}
		if len(gpu_vendors) > 1:
			found.append(("hybrid", "*"))
	if _has_sensors(root):
		found.append(("sensors", "*"))
	return [(trigger, identifier) for trigger, identifier in found if identifier]


def matched_packages(detections):
	"""Packages from autodetect.list whose trigger and vendor match a detection, without repeats."""
	packages = []
	for row_trigger, row_match, package in utils.autodetect_rows():
		hit = any(row_trigger == trigger and row_match.lower() in ("*", identifier)
		          for trigger, identifier in detections)
		if hit and package not in packages:
			packages.append(package)
	return packages


def apply(config, detections):
	"""Tick matched packages and the components they need. Returns the matched packages."""
	component_ids = {component_id for component_id, _label, _description in utils.components_rows()}
	requirements = utils.package_requirements()
	matched = matched_packages(detections)
	for package in matched:
		if package not in config.hardware_packages:
			config.hardware_packages.append(package)
		for requirement in requirements.get(package, "-").split(","):
			if requirement in component_ids and requirement not in config.components:
				config.components.append(requirement)
	return matched
