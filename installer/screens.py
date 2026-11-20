# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os
import re
import subprocess

import urwid

from . import utils
from .widgets import Selectable, SubmitEdit

KEYMAP_CHOICES = {
	"us": "English (US)", "gb": "English (UK)", "de": "German", "fr": "French",
	"es": "Spanish", "it": "Italian", "pt": "Portuguese", "br": "Portuguese (Brazil)",
	"ru": "Russian", "pl": "Polish", "se": "Swedish", "no": "Norwegian",
	"fi": "Finnish", "dk": "Danish", "ch": "German (Switzerland)", "jp": "Japanese",
}
FILESYSTEM_CHOICES = [
	("btrfs", "Checksums and copy-on-write snapshots"),
	("ext4",  "Simple and reliable"),
	("xfs",   "High throughput, large files"),
]
FIX_LABELS = {"network": "Fix network", "disk": "Choose another disk",
              "clock": "Turn on time sync"}
FORMAT_DEFAULT = {"root": True, "home": True, "swap": True,
                  "esp": True, "bios_grub": False}
LOCALE_RESULT_LIMIT = 40
DEFAULT_ROOT_GIB = 30
RESERVED_USERNAMES = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp",
                      "mail", "news", "uucp", "proxy", "www-data", "backup", "list",
                      "irc", "gnats", "nobody", "messagebus", "sshd", "_apt"}


DIGIT_PATTERN = re.compile(r"\d")
UPPER_PATTERN = re.compile(r"[A-Z]")
SYMBOL_PATTERN = re.compile(r"[^A-Za-z0-9]")
STRENGTH_LABELS = ("Very Weak", "Very Weak", "Weak", "Okay", "Strong", "Very Strong", "Very Strong")
STRENGTH_STYLES = {"Can't be empty": "error", "Very Weak": "error", "Weak": "warning",
                   "Okay": "warning", "Strong": "success", "Very Strong": "success"}


def _password_strength(text):
	if not text:
		return "Can't be empty"
	classes = sum(bool(pattern.search(text))
	              for pattern in (DIGIT_PATTERN, UPPER_PATTERN, SYMBOL_PATTERN))
	length = len(text)
	length_points = 0 if length < 8 else 1 if length < 12 else 2 if length < 16 else 3
	return STRENGTH_LABELS[classes + length_points]


def _strength_text(edit, empty_note=None):
	"""Label that updates as the user types into edit. empty_note replaces the empty-state message."""
	label = urwid.Text("")

	def refresh(_, text):
		if not text and empty_note:
			label.set_text(("warning", f"      {empty_note}"))
			return
		name = _password_strength(text)
		label.set_text((STRENGTH_STYLES[name], f"      {name}"))

	urwid.connect_signal(edit, "change", refresh)
	refresh(None, edit.edit_text)
	return label


# ── Layout helpers ─────────────────────────────────────────────────────

def _screen(title, items):
	widgets = [urwid.Text(("title", f"\n  {title}\n")), urwid.Divider()] + items
	return urwid.ListBox(urwid.SimpleFocusListWalker(widgets))


def _open(app, title, items, footer_text=None):
	app.show_screen(_screen(title, items), footer_text)


def _action(markup, callback):
	button = Selectable(markup)
	urwid.connect_signal(button, "activate", callback)
	return button


def _back_button(app):
	return _action(("unset", "  Back"), lambda _: app.show_main())


def _with_back(app, items):
	return items + [urwid.Divider(), _back_button(app)]


def _described(control, description, style="label", divider=True):
	"""Control with an indented description beneath it, optionally followed by a divider."""
	rows = [control, urwid.Text((style, f"      {description}"))]
	return rows + [urwid.Divider()] if divider else rows


def _set(app, attribute, value):
	setattr(app.config, attribute, value)
	app.show_main()


def _show_error(error_text, message):
	error_text.set_text(("error", f"  {message}"))


def _validate(rules):
	"""Message of the first (failed, message) rule that failed, or None."""
	return next((message for failed, message in rules if failed), None)


def _form_screen(app, title, items, fields, on_submit):
	"""Show a form. Enter on a field moves to the next; Enter on the last calls on_submit."""
	listbox = _screen(title, items)

	def advance(position):
		if position == len(fields) - 1:
			on_submit()
		else:
			listbox.set_focus(listbox.body.index(fields[position + 1]))

	for position, field in enumerate(fields):
		urwid.connect_signal(field, "activate", lambda _, p=position: advance(p))
	app.show_screen(listbox)


def _search_results(walker, matches, on_pick, label=str):
	"""Refill a results walker with selectable matches."""
	walker.clear()
	for match in matches:
		walker.append(_action(f"    {label(match)}", lambda _, v=match: on_pick(v)))


def _bind_filter(edit, results, candidates, on_pick, label=str, limit=None):
	"""Refill results from (value, search_text) candidates on every edit change."""
	def refresh(text):
		pattern = text.strip().lower()
		matches = [value for value, search_text in candidates if pattern in search_text]
		if limit and pattern:
			matches = matches[:limit]
		_search_results(results, matches, on_pick, label)

	urwid.connect_signal(edit, "change", lambda _, text: refresh(text))
	refresh("")


# ── Change handlers ────────────────────────────────────────────────────

def _on_toggle(_box, state, user_data):
	"""Add or remove a value from a list attribute on the config."""
	app, attribute, value = user_data
	chosen = getattr(app.config, attribute)
	if state and value not in chosen:
		chosen.append(value)
	elif not state and value in chosen:
		chosen.remove(value)


def _on_flag(_box, state, user_data):
	"""Set a boolean attribute on the config."""
	app, attribute = user_data
	setattr(app.config, attribute, state)


def _on_format(_box, state, user_data):
	app, role = user_data
	app.config.layout[role]["format"] = state


def _on_swappiness(_edit, new_text, app):
	if new_text.isdigit() and int(new_text) <= 100:
		app.config.swappiness = int(new_text)


def _on_multiarch(_box, state, app):
	app.config.multiarch_i386 = state
	drop_unavailable(app.config)


def _flag_rows(app, attribute, label, description):
	"""Checkbox bound to a config flag, with a description line beneath it."""
	return _described(
		urwid.CheckBox(label, state=getattr(app.config, attribute),
		               on_state_change=_on_flag, user_data=(app, attribute)),
		description)


def _wifi_busy(app, message):
	_open(app, "Wi-Fi", [urwid.Text(("warning", f"  {message}"))])
	app.loop.draw_screen()


# ── Network ────────────────────────────────────────────────────────────

def network(app):
	app.refresh_online()
	items = [
		urwid.Text(("success", "  Connected") if app.online else ("error", "  Offline")),
		urwid.Divider(),
		_action("  Wi-Fi networks", lambda _: _wifi_scan(app)),
		_action("  Launch nmtui (full network manager)", lambda _: _launch_nmtui(app)),
	]
	_open(app, "Network", _with_back(app, items))


def _launch_nmtui(app):
	app.loop.stop()
	subprocess.run(["nmtui"])
	app.loop.start()
	network(app)


def _wifi_scan(app):
	_wifi_busy(app, "Scanning...")
	networks = utils.wifi_scan()
	items = []
	if not networks:
		items.append(urwid.Text(("error", "  No networks found. Check the adapter is recognized.")))
	for ssid, signal, security in networks:
		lock = "" if security in ("", "--") else "  [secured]"
		items.append(_action(f"  {ssid:<30}{signal:>3}%{lock}",
		                     lambda _, s=ssid, sec=security: _wifi_password(app, s, sec)))
	items += [
		urwid.Divider(),
		_action(("unset", "  Rescan"), lambda _: _wifi_scan(app)),
		_action(("unset", "  Back"), lambda _: network(app)),
	]
	_open(app, "Wi-Fi", items)


def _wifi_password(app, ssid, security):
	secured = security not in ("", "--")
	password_edit = SubmitEdit("  Password: ", mask="*")
	status = urwid.Text("")

	def connect(_=None):
		password = password_edit.edit_text if secured else None
		if secured and not password:
			status.set_text(("error", "  Password cannot be empty"))
		else:
			_wifi_connect(app, ssid, password, security)

	urwid.connect_signal(password_edit, "activate", connect)
	items = [urwid.Text([("bold", "  Network: "), ("value", ssid)]), urwid.Divider()]
	if secured:
		items += [password_edit, urwid.Divider()]
	items += [
		_action(("bold", "  Connect"), connect),
		status,
		urwid.Divider(),
		_action(("unset", "  Back"), lambda _: _wifi_scan(app)),
	]
	_open(app, "Wi-Fi", items)


def _wifi_connect(app, ssid, password, security):
	_wifi_busy(app, f"Connecting to {ssid}...")
	connected, message = utils.wifi_connect(ssid, password, security)
	_wifi_status(app, connected, message)


def _wifi_status(app, passed, message):
	items = [
		urwid.Text(("success" if passed else "error", f"  {message}")),
		urwid.Divider(),
		_action(("bold", "  Test connection (deb.debian.org)"), lambda _: _wifi_test(app)),
		_action(("unset", "  Back to network"), lambda _: network(app)),
	]
	_open(app, "Wi-Fi", items)


def _wifi_test(app):
	_wifi_busy(app, "Testing...")
	passed, message = utils.wifi_test()
	_wifi_status(app, passed, message)


# ── Pre-flight ─────────────────────────────────────────────────────────

def _apply_fix(app, fix):
	if fix == "network":
		network(app)
	elif fix == "disk":
		disk(app)
	elif fix == "clock":
		utils.enable_ntp()
		preflight(app)


def _check_rows(app, checks, style):
	rows = []
	for check in checks:
		rows.append(urwid.Text([(style, f"  {check.title}: "), ("label", check.message)]))
		if check.fix:
			rows.append(_action(("bold", f"    {FIX_LABELS[check.fix]}"),
			                    lambda _, f=check.fix: _apply_fix(app, f)))
		rows.append(urwid.Divider())
	return rows


def preflight(app, show_passed=False):
	app.refresh_preflight()
	if not app.preflight_issues():
		app.show_main()
		return
	blockers = [check for check in app.preflight if check.level == "block"]
	warnings = [check for check in app.preflight if check.level == "warn"]
	passed = [check for check in app.preflight if check.level == "ok"]

	items = []
	if blockers:
		items.append(urwid.Text(("error", "  Can't continue")))
		items += _check_rows(app, blockers, "error")
	if warnings:
		items.append(urwid.Text(("warning", "  Worth knowing")))
		items += _check_rows(app, warnings, "warning")
	if show_passed:
		items.append(urwid.Text(("success", "  Passed")))
		items += [urwid.Text([("success", f"  {check.title}: "), ("label", check.message)])
		          for check in passed]
	if blockers:
		items.append(urwid.Text(("error", "  Issues above preventing install.")))
	else:
		items.append(urwid.Text(("success", "  Ready to continue.")))

	items += [
		urwid.Divider(),
		_action(("bold", f"  {'Hide' if show_passed else 'Show'} passed checks ({len(passed)})"),
		        lambda _: preflight(app, not show_passed)),
		_action(("bold", "  Re-check"), lambda _: preflight(app, show_passed)),
	]
	_open(app, "Pre-flight", items + [_back_button(app)])


# ── Keyboard ───────────────────────────────────────────────────────────

def keyboard(app):
	error_text = urwid.Text("")
	edit = SubmitEdit("  > ")

	def choose(layout):
		error = utils.apply_keymap(layout)
		if error:
			_show_error(error_text, error)
		else:
			_set(app, "keymap", layout)

	items = [_action(f"  {layout:<6}{language}", lambda _, v=layout: choose(v))
	         for layout, language in KEYMAP_CHOICES.items()]
	urwid.connect_signal(edit, "activate", lambda _: choose(edit.edit_text.strip()))
	items += [
		urwid.Divider(),
		urwid.Text(("label", "  Or type an XKB layout name (e.g., nl, cz, latam):")),
		edit,
		_action(("bold", "  Accept"), lambda _: choose(edit.edit_text.strip())),
		error_text,
	]
	_open(app, "Keyboard Layout", _with_back(app, items))


# ── Timezone ───────────────────────────────────────────────────────────

def _pick_timezone(app, zone):
	if not app.config.locale:
		app.config.locale = utils.locale_for_timezone(zone) or ""
	_set(app, "timezone", zone)


def timezone(app):
	app.refresh_online()
	results = urwid.SimpleFocusListWalker([])
	edit = urwid.Edit("  > ")
	status = urwid.Text("")

	def autodetect(_):
		zone, reason = utils.detect_timezone()
		if zone:
			_pick_timezone(app, zone)
		else:
			status.set_text(("error", f"  Autodetect failed: {reason}"))

	candidates = [(zone, zone.lower()) for zone in utils.get_timezones()]
	_bind_filter(edit, results, candidates, lambda zone: _pick_timezone(app, zone))

	items = [
		urwid.Text(("label", "  Scroll the list or type to filter (e.g., chicago, Europe/Oslo):")),
		edit,
	]
	if app.online:
		items += [_action(("bold", "  Autodetect from network"), autodetect), status]
	items.append(urwid.BoxAdapter(urwid.ListBox(results), 14))
	_open(app, "Timezone", _with_back(app, items))


# ── Locale ─────────────────────────────────────────────────────────────

def _normalize_locale(value):
	value = value.strip()
	if re.match(r"^[a-z]{2}_[A-Z]{2}$", value):
		value += ".UTF-8"
	return value


def locale(app):
	supported = utils.get_supported_locales()
	utf8_locales = sorted(loc for loc in supported if loc.endswith(".UTF-8"))
	results = urwid.SimpleFocusListWalker([])
	error_text = urwid.Text("")
	edit = SubmitEdit("  > ")

	def accept(value):
		value = _normalize_locale(value)
		if not value or (supported and value not in supported):
			_show_error(error_text, f"'{value}' is not a known locale")
		else:
			_set(app, "locale", value)

	labels = {loc: f"{loc:<18}{utils.locale_language(loc):<20}{utils.locale_country(loc)}"
	          for loc in utf8_locales}
	candidates = [(loc, f"{loc} {utils.locale_language(loc)} {utils.locale_country(loc)}".lower())
	              for loc in utf8_locales]
	_bind_filter(edit, results, candidates, accept, labels.get, LOCALE_RESULT_LIMIT)
	urwid.connect_signal(edit, "activate", lambda _: accept(edit.edit_text))

	items = [
		urwid.Text(("label", "  Scroll the list or type to filter (e.g., en_US, SE, nb_NO):")),
		edit,
		urwid.BoxAdapter(urwid.ListBox(results), 8),
		error_text,
	]
	_open(app, "Locale", _with_back(app, items))


# ── Disk and partitioning ──────────────────────────────────────────────

def disk(app):
	items = [
		_action(f"  {device:<14}{size:<10}{model}", lambda _, v=device: _choose_disk(app, v))
		for device, size, model in utils.get_disks()
	]
	if not items:
		items.append(urwid.Text(("error", "  No disks found")))
	_open(app, "Disk Selection", _with_back(app, items),
	      footer_text="  ↑↓ navigate  Enter select  WARNING: selected disk will be erased")


def _reset_mode(config, manual=False):
	"""Clear the partitioning mode flags before a new choice is applied."""
	config.manual = manual
	config.custom = False
	config.free_space = False


def _choose_disk(app, device):
	app.config.disk = device
	app.config.layout = {}
	_reset_mode(app.config)
	partition_mode(app)


def _manual_roles(boot_mode):
	roles = [("root", "Root (/)"), ("home", "Home (/home), optional"),
	         ("swap", "Swap, optional")]
	if boot_mode == "uefi":
		roles.insert(1, ("esp", "EFI system partition"))
	else:
		roles.insert(0, ("bios_grub", "BIOS boot partition (needed on GPT disks)"))
	return roles


def _set_automatic(app, swap_partition):
	_reset_mode(app.config)
	app.config.swap_partition = swap_partition
	app.show_main()


def partition_mode(app):
	modes = [
		("Automatic", "Erase the whole disk. Creates swap equal to RAM if available disk space.",
		 lambda: _set_automatic(app, True)),
		("Automatic without swap", "Erase the whole disk. No swap partition is created.",
		 lambda: _set_automatic(app, False)),
		("Custom sizes", "Erase the whole disk. Set swap and an optional separate root. Home takes the rest.",
		 lambda: custom_sizes(app)),
		("Free space (dual boot)", "Install into unallocated space. Existing partitions are kept.",
		 lambda: custom_sizes(app, free_space=True)),
		("Manual", "Use existing partitions. Only the ones you pick are formatted.",
		 lambda: partition_layout(app)),
	]
	items = []
	for title, description, callback in modes:
		items += _described(_action(("bold", f"  {title}"), lambda _, cb=callback: cb()), description)
	_open(app, "Partitioning", items + [_back_button(app)])


def _parse_gib(text):
	"""Whole GB from text, 0 if blank, or None if it isn't a whole number."""
	text = text.strip()
	if not text:
		return 0
	return int(text) if text.isdigit() else None


def _free_space_problem(config):
	"""Reason free space can't be used on this disk, or empty string if it can."""
	label = utils.partition_table_type(config.disk)
	if not label:
		return "This disk has no partition table. Use Automatic"
	if config.boot_mode == "uefi" and not utils.find_esp(config.disk):
		return "No EFI system partition found. Use Automatic or Manual"
	if config.boot_mode == "bios" and label == "gpt" and not utils.has_bios_grub(config.disk):
		return "GPT on BIOS needs a BIOS boot partition. Use Automatic or Manual"
	return ""


def custom_sizes(app, free_space=False):
	config = app.config
	boot_mib = utils.boot_reserve_mib(config.boot_mode)
	if free_space:
		region = utils.largest_free_region(config.disk)
		if region is None:
			_open(app, "Custom Sizes",
			      _with_back(app, [urwid.Text(("error", "  No unallocated space on this disk"))]))
			return
		space_mib = region[1] - region[0]
		heading = f"  Free space: {space_mib // 1024} GB"
	else:
		disk_mib = utils.disk_bytes(config.disk) // 2**20
		space_mib = disk_mib - boot_mib
		heading = f"  Total disk size: {disk_mib // 1024} GB"
	swap_edit = SubmitEdit("  Swap (GB, leave blank to not set): ", str(config.custom_swap_gib or ""))
	root_edit = SubmitEdit("  Root (GB, leave blank to not set): ", str(config.custom_root_gib or ""))
	home_edit = SubmitEdit("  Home (GB, leave blank to fill the rest): ", str(config.custom_home_gib or ""))
	summary = urwid.Text("")
	error_text = urwid.Text("")

	def read_sizes():
		return tuple(_parse_gib(edit.edit_text) for edit in (swap_edit, root_edit, home_edit))

	def refresh(*_):
		swap_gib, root_gib, home_gib = read_sizes()
		if None in (swap_gib, root_gib, home_gib):
			summary.set_text(("error", "  Enter whole GB"))
			return
		shown = config.custom_layout(space_mib, swap_gib, root_gib, home_gib)
		lines = [] if free_space else [f"Boot: {boot_mib} MiB"]
		if swap_gib:
			lines.append(f"Swap: {swap_gib} GB")
		lines.append(f"Root: {max(shown['root'], 0)} GB")
		if shown["home"] is not None:
			lines.append(f"Home: {max(shown['home'], 0)} GB")
		if shown["unused"]:
			lines.append(f"Left unformatted: {shown['unused']} GB")
		if shown["free"] < 0:
			lines.append(f"Over the space by {-shown['free']} GB. Reduce a size.")
		summary.set_text(("warning" if shown["free"] < 0 else "label",
		                  "\n".join(f"  {line}" for line in lines)))

	def accept(_=None):
		swap_gib, root_gib, home_gib = read_sizes()
		if None in (swap_gib, root_gib, home_gib):
			_show_error(error_text, "Sizes must be whole numbers of GB")
			return
		problem = config.custom_error(space_mib, swap_gib, root_gib, home_gib)
		if not problem and free_space:
			problem = _free_space_problem(config)
		if problem:
			_show_error(error_text, problem)
			return
		_reset_mode(config)
		config.custom = True
		config.free_space = free_space
		config.custom_swap_gib = swap_gib
		config.custom_root_gib = root_gib
		config.custom_home_gib = home_gib
		app.show_main()

	for edit in (swap_edit, root_edit, home_edit):
		urwid.connect_signal(edit, "postchange", refresh)
	refresh()
	notes = [
		urwid.Text(("warning", "  Windows: turn off Fast Startup and hibernation first.")),
		urwid.Text(("warning", "  BitLocker: suspend it and keep the recovery key handy.")),
		urwid.Divider(),
	] if free_space else []
	items = [
		urwid.Text(("value", heading)),
		urwid.Divider(),
		*notes,
		swap_edit,
		root_edit,
		home_edit,
		urwid.Divider(),
		summary,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
	]
	_form_screen(app, "Custom Sizes", _with_back(app, items),
	             [swap_edit, root_edit, home_edit], accept)


def _default_format(disk, role, device):
	"""An existing FAT EFI partition is kept unformatted so other systems' boot files survive."""
	if role != "esp":
		return FORMAT_DEFAULT[role]
	filesystem = next((fs for part, _size, fs in utils.partition_rows(disk) if part == device), "")
	return filesystem != "vfat"


def _assign(app, role, device):
	layout = app.config.layout
	for other_role in [name for name, part in layout.items() if part["device"] == device]:
		del layout[other_role]
	if device is None:
		layout.pop(role, None)
	else:
		layout[role] = {"device": device, "format": _default_format(app.config.disk, role, device)}
	partition_layout(app)


def _finish_manual(app):
	config = app.config
	if not config.layout_ready():
		partition_layout(app, "Choose a root partition, and an EFI partition on UEFI.")
		return
	filesystems = {device: fs for device, _size, fs in utils.partition_rows(config.disk)}
	esp = config.layout.get("esp")
	if esp and not esp["format"] and filesystems.get(esp["device"]) != "vfat":
		partition_layout(app, "EFI partition is not FAT. Tick Format for it.")
		return
	home = config.layout.get("home")
	if home and not home["format"] and filesystems.get(home["device"], "unformatted") == "unformatted":
		partition_layout(app, "Home has no filesystem. Tick Format for it.")
		return
	if (config.boot_mode == "bios" and "bios_grub" not in config.layout
	        and utils.partition_table_type(config.disk) == "gpt"):
		partition_layout(app, "This disk uses GPT, so it needs a BIOS boot partition.")
		return
	app.show_main()


def partition_layout(app, error=""):
	config = app.config
	_reset_mode(config, manual=True)
	partitions = utils.partition_rows(config.disk)
	items = []
	for role, label in _manual_roles(config.boot_mode):
		selected = config.layout.get(role)
		items.append(urwid.Text(("bold", f"  {label}")))
		for device, size, filesystem in partitions:
			marker = "*" if selected and selected["device"] == device else " "
			items.append(_action(f"  {marker} {device:<16}{size:<9}{filesystem}",
			                     lambda _, r=role, d=device: _assign(app, r, d)))
		items.append(_action("    None", lambda _, r=role: _assign(app, r, None)))
		if role in ("home", "esp") and selected:
			items.append(urwid.CheckBox(
				"    Format (erases this partition)", state=selected["format"],
				on_state_change=_on_format, user_data=(app, role)))
		items.append(urwid.Divider())
	items += [
		urwid.Text(("error", f"  {error}" if error else "")),
		_action(("bold", "  Done"), lambda _: _finish_manual(app)),
		_back_button(app),
	]
	_open(app, "Manual Partitioning", items,
	      footer_text="  ↑↓ navigate  Space toggle  Enter select  Esc back")


# ── Filesystem ─────────────────────────────────────────────────────────

def _choose_filesystem(app, name):
	app.config.filesystem = name
	if name != "btrfs":
		app.config.snapper = False
	else:
		app.config.custom_root_gib = 0  # separate root needs ext4 or xfs
	filesystem(app)


def _on_luks(_box, state, app):
	app.config.luks = state
	if state:
		luks_passphrase(app)
	else:
		app.config.luks_pass = ""


def filesystem(app):
	config = app.config
	items = []
	for name, description in FILESYSTEM_CHOICES:
		marker = ">" if config.filesystem == name else " "
		items += _described(_action(("bold", f"  {marker} {name}"),
		                            lambda _, v=name: _choose_filesystem(app, v)),
		                    description, divider=False)
	items.append(urwid.Divider())
	if config.filesystem == "btrfs":
		items.append(urwid.CheckBox("  Snapshots with snapper", state=config.snapper,
		                            on_state_change=_on_flag, user_data=(app, "snapper")))
	else:
		items.append(urwid.Text(("unset", "  Snapshots with snapper  (needs btrfs)")))
	items.append(urwid.Text(("label", "      Roll back system changes. Uses @ and @home subvolumes.")))
	items.append(urwid.Divider())
	items.append(urwid.CheckBox("  Encrypt with LUKS", state=config.luks,
	                            on_state_change=_on_luks, user_data=app))
	items.append(urwid.Text(("label", "      Encrypts the root partition. You enter a passphrase at boot.")))
	_open(app, "Filesystem", _with_back(app, items))


def luks_passphrase(app):
	passphrase = SubmitEdit("  Passphrase: ", mask="*")
	confirm = SubmitEdit("  Confirm:    ", mask="*")
	error_text = urwid.Text("")

	def accept(_=None):
		error = _validate([
			(not passphrase.edit_text, "Passphrase cannot be empty"),
			(passphrase.edit_text != confirm.edit_text, "Passphrases don't match"),
		])
		if error:
			_show_error(error_text, error)
		else:
			app.config.luks_pass = passphrase.edit_text
			app.show_main()

	def cancel(_=None):
		app.config.luks = False
		app.config.luks_pass = ""
		app.show_main()

	items = [
		urwid.Text(("warning", "  Losing this passphrase means losing the data. There is no recovery.")),
		urwid.Text(("label", "  You type it at every boot, using the keyboard layout you chose.")),
	]
	if (app.config.custom and (app.config.custom_root_gib or app.config.custom_home_gib)) or (
			app.config.manual and "home" in app.config.layout):
		items.append(urwid.Text(("warning", "  Your separate home partition will not be encrypted.")))
	items += [
		urwid.Divider(),
		passphrase,
		_strength_text(passphrase),
		confirm,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
		_action(("unset", "  Cancel (turn off encryption)"), cancel),
	]
	_form_screen(app, "Encryption", items, [passphrase, confirm], accept)


# ── Hostname and user ──────────────────────────────────────────────────

def hostname(app):
	edit = SubmitEdit("  Hostname: ", app.config.hostname)
	error_text = urwid.Text("")

	def accept(_=None):
		value = edit.edit_text
		if re.match(r"^[a-zA-Z][a-zA-Z0-9-]*$", value):
			_set(app, "hostname", value)
		else:
			_show_error(error_text, "Invalid hostname")

	items = [
		urwid.Text(("label", "  System identifier. Must start with a letter.")),
		urwid.Divider(),
		edit,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
	]
	_form_screen(app, "Hostname", _with_back(app, items), [edit], accept)


def user(app):
	username_edit = SubmitEdit("  Username: ", app.config.username)
	pass_edit = SubmitEdit("  Password: ", mask="*")
	pass_confirm = SubmitEdit("  Confirm:  ", mask="*")
	root_edit = SubmitEdit("  Root password: ", mask="*")
	root_confirm = SubmitEdit("  Confirm:       ", mask="*")
	error_text = urwid.Text("")

	def accept(_=None):
		name = username_edit.edit_text
		error = _validate([
			(not re.match(r"^[a-z][a-z0-9_-]{0,31}$", name), "Invalid username (1-32 characters)"),
			(name in RESERVED_USERNAMES or name.startswith("systemd-"), f"'{name}' is reserved"),
			(not pass_edit.edit_text, "Password cannot be empty"),
			(pass_edit.edit_text != pass_confirm.edit_text, "User passwords don't match"),
			(root_edit.edit_text and root_edit.edit_text != root_confirm.edit_text,
			 "Root passwords don't match"),
		])
		if error:
			_show_error(error_text, error)
		else:
			app.config.username = name
			app.config.user_pass = pass_edit.edit_text
			app.config.root_pass = root_edit.edit_text
			app.show_main()

	items = [
		urwid.Text(("label", "  Lowercase letters, numbers, hyphens, underscores.")),
		urwid.Divider(),
		username_edit,
		pass_edit,
		_strength_text(pass_edit),
		pass_confirm,
		urwid.Divider(),
		urwid.Text(("label", "  Root password (leave blank to disable root login):")),
		root_edit,
		_strength_text(root_edit, "Root login disabled"),
		root_confirm,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
	]
	fields = [username_edit, pass_edit, pass_confirm, root_edit, root_confirm]
	_form_screen(app, "User Account", _with_back(app, items), fields, accept)


# ── Desktop and window managers ────────────────────────────────────────

def _choice_rows(app, choice_id, label, detail, description, enabled=True):
	if not enabled:
		return _described(urwid.Text(("unset", f"  {label:<14}{detail}  needs internet")),
		                  description, style="unset")
	return _described(
		_action(f"  {label:<14}{detail}", lambda _, v=choice_id: _set(app, "desktop", v)),
		description)


def _custom_repo(app):
	edit = SubmitEdit("  URL: ", app.config.custom_repo)
	status = urwid.Text("")

	def check(_=None):
		url = edit.edit_text.strip()
		status.set_text(("warning", "  Checking..."))
		app.loop.draw_screen()
		passed, message = utils.check_repo(url)
		if passed:
			app.config.custom_repo = url
			_set(app, "desktop", "custom")
		else:
			status.set_text(("error", f"  {message}"))

	urwid.connect_signal(edit, "activate", check)
	items = [
		urwid.Text(("label", "  Git URL (https, git, or user@host:path)")),
		urwid.Divider(),
		edit,
		status,
		urwid.Divider(),
		_action(("bold", "  Check and use"), check),
	]
	_open(app, "Custom Repo", _with_back(app, items))


def desktop(app):
	app.refresh_online()
	items = []
	if not app.online:
		items += [
			urwid.Text(("warning", "  Offline: only Console only can be installed. Connect for desktops.")),
			urwid.Divider(),
		]
	desktop_id, label, package, description = utils.CONSOLE_ROW
	items += _choice_rows(app, desktop_id, label, package, description)
	for wm_id, label, _repo, _ref, description in utils.wm_rows():
		items += _choice_rows(app, wm_id, label, "", description, enabled=app.online)
	if app.online:
		items.append(_action("  Custom Repo", lambda _: _custom_repo(app)))
	else:
		items.append(urwid.Text(("unset", "  Custom Repo  needs internet")))
	items += [
		urwid.Text(("label", "      Any git repo with an install.sh at its root.")),
		urwid.Divider(),
		urwid.Text(("label", "  Default GUI environment.")),
	]
	_open(app, "Desktop / Window Manager", _with_back(app, items))


# ── Packages (apt components) ──────────────────────────────────────────

def packages_menu(app):
	items = []
	for component_id, label, description in utils.components_rows():
		items.append(urwid.CheckBox(
			f"  {label}", state=component_id in app.config.components,
			on_state_change=_on_archive_toggle,
			user_data=(app, "components", component_id)))
		items.append(urwid.Text(("label", f"      {description}")))
	items += [
		urwid.Divider(),
		urwid.Text(("label", "  Optional. Adds apt sources to the installed system.")),
	]
	_open(app, "Packages", _with_back(app, items),
	      footer_text="  ↑↓ navigate  Space toggle  Esc back")


# ── Hardware and Software (optional packages) ──────────────────────────

def drop_unavailable(config):
	"""Remove selected packages whose requirements are no longer met."""
	requirements = utils.package_requirements()
	for attribute in ("hardware_packages", "software_packages"):
		chosen = getattr(config, attribute)
		chosen[:] = [package for package in chosen
		             if _requirements_met(config, requirements.get(package, "-"))
		             and (config.online or not utils.needs_network(package))]


def _requirements_met(config, requirement):
	needed = {name for name in requirement.split(",") if name != "-"}
	available = set()
	for entry in set(config.components) | set(config.hardware_packages) | set(config.software_packages):
		available |= set(entry.split())
	if config.multiarch_i386:
		available.add("i386")
	return needed <= available


def _on_archive_toggle(box, state, user_data):
	_on_toggle(box, state, user_data)
	if not state:
		drop_unavailable(user_data[0].config)


def _on_package_toggle(box, state, user_data):
	_on_toggle(box, state, user_data)
	drop_unavailable(user_data[0].config)


def _package_row(app, menu, package, label, requirement):
	chosen = getattr(app.config, f"{menu}_packages")
	if not app.online and utils.needs_network(package):
		return urwid.Text(("unset", f"  {label}  needs internet"))
	if _requirements_met(app.config, requirement):
		return urwid.CheckBox(
			f"  {label}", state=package in chosen,
			on_state_change=_on_package_toggle,
			user_data=(app, f"{menu}_packages", package))
	return urwid.Text(("unset", f"  {label}  requires {requirement}"))


def _extras_screen(app, menu, title):
	app.refresh_online()
	entries = [(section, package, label, requirement)
	           for row_menu, section, package, label, requirement in utils.extras_rows()
	           if row_menu == menu]
	entries += [(section, custom_id, label, requirement)
	            for custom_id, row_menu, section, label, _script, requirement in utils.custom_rows()
	            if row_menu == menu]
	sections = {}
	for section, package, label, requirement in entries:
		sections.setdefault(section, []).append((package, label, requirement))
	items = []
	for section, section_entries in sections.items():
		items += [urwid.Divider(), urwid.Text(("title", f"  {section}"))]
		for package, label, requirement in section_entries:
			items.append(_package_row(app, menu, package, label, requirement))
	items += [
		urwid.Divider(),
		urwid.Text(("label", "  Optional. Installed after the desktop." if app.online
		            else "  Offline: only packages bundled on this ISO can be installed.")),
	]
	_open(app, title, _with_back(app, items),
	      footer_text="  ↑↓ navigate  Space toggle  Esc back")


def hardware(app):
	_extras_screen(app, "hardware", "Hardware")


def software(app):
	_extras_screen(app, "software", "Software")


def hardware_detect(app, detections, matched):
	labels = {package: label for _menu, _section, package, label, _requires in utils.extras_rows()}
	found = sorted({trigger if identifier == "*" else f"{trigger} {identifier}"
	                for trigger, identifier in detections})
	items = [urwid.Text(("label", f"  Detected: {', '.join(found) or 'nothing'}")),
	         urwid.Divider()]
	if matched:
		items.append(urwid.Text(("success", "  Ticked in Hardware:")))
		items += [urwid.Text(("value", f"    {labels.get(package, package)}"))
		          for package in matched]
	else:
		items.append(urwid.Text(("label", "  No matching hardware found.")))
	items += [urwid.Divider(), urwid.Text(("label", "  Adjust anything under Hardware."))]
	_open(app, "Hardware Detect", _with_back(app, items))


# ── Secure Boot ────────────────────────────────────────────────────────

def secure_boot(app):
	passphrase = SubmitEdit("  Password: ", mask="*")
	confirm = SubmitEdit("  Confirm:  ", mask="*")
	error_text = urwid.Text("")

	def accept(_=None):
		error = _validate([
			(not 8 <= len(passphrase.edit_text) <= 16, "Use 8 to 16 characters"),
			(passphrase.edit_text != confirm.edit_text, "Passwords don't match"),
		])
		if error:
			_show_error(error_text, error)
		else:
			app.config.mok_pass = passphrase.edit_text
			app.show_main()

	items = [
		urwid.Text(("label", "  Secure Boot is on, so DKMS drivers must be signed.")),
		urwid.Text(("label", "  Set a one-time password. On first boot a blue MOK screen")),
		urwid.Text(("label", "  appears: choose Enroll MOK and enter this password.")),
		urwid.Divider(),
		passphrase,
		confirm,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
	]
	_form_screen(app, "Secure Boot", _with_back(app, items), [passphrase, confirm], accept)


def _custom_mirror(app):
	edit = SubmitEdit("  URL: ", app.config.mirror)
	error_text = urwid.Text("")

	def accept(_=None):
		url = edit.edit_text.strip()
		if re.match(r"^https?://\S+$", url):
			_set(app, "mirror", url)
		else:
			_show_error(error_text, "Use an http:// or https:// URL")

	items = [
		urwid.Text(("label", "  Full apt base URL, e.g. http://ftp.example.org/debian/")),
		urwid.Divider(),
		edit,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
	]
	_form_screen(app, "Custom Mirror", _with_back(app, items), [edit], accept)


def mirror(app):
	items = [
		_action(f"  {label:<16}{url}", lambda _, v=url: _set(app, "mirror", v))
		for label, url in utils.mirror_rows()
	]
	items.append(_action(("bold", "  Custom..."), lambda _: _custom_mirror(app)))
	_open(app, "Debian Mirror", _with_back(app, items),
	      footer_text="  ↑↓ navigate  Enter select  Esc back")


def _cycle_policy(app, attribute, options):
	current = getattr(app.config, attribute)
	setattr(app.config, attribute, options[(options.index(current) + 1) % len(options)])
	firewall(app)


def firewall(app):
	config = app.config
	items = [
		urwid.CheckBox("  Enable UFW", state=config.ufw,
		              on_state_change=_on_flag, user_data=(app, "ufw")),
		urwid.Divider(),
		_action(f"  Incoming  {config.ufw_incoming}",
			lambda _: _cycle_policy(app, "ufw_incoming", ["deny", "reject", "allow"])),
		_action(f"  Outgoing  {config.ufw_outgoing}",
			lambda _: _cycle_policy(app, "ufw_outgoing", ["allow", "deny", "reject"])),
		urwid.Text(("label", "      Incoming deny blocks all unsolicited connections, including SSH unless ticked.")),
		urwid.Divider(),
		urwid.Text(("bold", "  Allow rules")),
	]
	for rule_id, label, _rule in utils.firewall_rows():
		items.append(urwid.CheckBox(
			f"  {label}", state=rule_id in config.firewall_rules,
			on_state_change=_on_toggle, user_data=(app, "firewall_rules", rule_id)))
	_open(app, "Firewall (UFW)", _with_back(app, items),
		footer_text="  ↑↓ navigate  Enter select  Space toggle  Esc back")


GIT_EDITORS = ["nano", "vim", "nvim"]
GIT_PULL = ["", "false", "true"]
GIT_PULL_LABELS = {"": "not set", "false": "merge", "true": "rebase"}


def _cycle_git(app, attribute, options, store):
	store()
	current = getattr(app.config, attribute)
	setattr(app.config, attribute, options[(options.index(current) + 1) % len(options)])
	git(app)


def git(app):
	config = app.config
	name_edit = SubmitEdit("  Name:           ", config.git_name)
	email_edit = SubmitEdit("  Email:          ", config.git_email)
	branch_edit = SubmitEdit("  Default branch: ", config.git_default_branch)
	error_text = urwid.Text("")

	def store():
		config.git_name = name_edit.edit_text.strip()
		config.git_email = email_edit.edit_text.strip()
		config.git_default_branch = branch_edit.edit_text.strip()

	def accept(_=None):
		email = email_edit.edit_text.strip()
		branch = branch_edit.edit_text.strip()
		error = _validate([
			(email and not re.match(r"^[^@\s]+@[^@\s]+$", email), "Email needs an @"),
			(not branch or " " in branch, "Branch name cannot be empty or contain spaces"),
		])
		if error:
			_show_error(error_text, error)
		else:
			store()
			app.show_main()

	items = [
		name_edit,
		email_edit,
		branch_edit,
		_action(f"  Editor          {config.git_editor}",
			lambda _: _cycle_git(app, "git_editor", GIT_EDITORS, store)),
		_action(f"  Pull behavior   {GIT_PULL_LABELS[config.git_pull_rebase]}",
			lambda _: _cycle_git(app, "git_pull_rebase", GIT_PULL, store)),
		urwid.Text(("label", "      Merge keeps history. Rebase replays your commits on top of upstream.")),
		urwid.Divider(),
		error_text,
		_action(("bold", "  Accept"), accept),
	]
	_form_screen(app, "Git", _with_back(app, items),
		[name_edit, email_edit, branch_edit], accept)


# ── Advanced ───────────────────────────────────────────────────────────

def _on_backports_repo(_box, state, app):
	app.config.backports_repo = state
	if not state:
		app.config.backports_kernel = False
		app.config.backports_mesa = False
	advanced(app)


def advanced(app):
	app.refresh_online()
	config = app.config
	items = [
		*_flag_rows(app, "zram", "  zram (compressed swap in RAM)",
		            "Recommended. Used before any swap partition. Doesn't support hibernation."),
		*_flag_rows(app, "auto_updates", "  Automatic security updates",
		            "Installs security fixes daily. Never reboots on its own."),
		*_described(urwid.CheckBox("  32-bit (i386) support", state=config.multiarch_i386,
		                           on_state_change=_on_multiarch, user_data=app),
		            "Adds the i386 architecture for 32-bit libraries and apps."),
	]
	if not app.online:
		items += [urwid.Text(("unset", "  Backports repository, kernel and Mesa need internet.")),
		          urwid.Divider()]
	else:
		items += _described(
			urwid.CheckBox("  Backports repository", state=config.backports_repo,
			               on_state_change=_on_backports_repo, user_data=app),
			"trixie-backports, pinned so nothing upgrades unless requested.")
	if app.online and config.backports_repo:
		items += _flag_rows(app, "backports_kernel", "  Backports kernel",
		                    "Newer kernel from trixie-backports, kept updated.")
		items += _flag_rows(app, "backports_mesa", "  Backports Mesa",
		                    "Newer Mesa drivers from trixie-backports, kept updated.")
	elif app.online:
		items += [urwid.Text(("unset", "  Backports kernel and Mesa require the backports repository.")),
		          urwid.Divider()]
	if app.online:
		items += _described(
			_action(f"  Debian mirror  {config.mirror}", lambda _: mirror(app)),
			"Package source for the installed system. Used for online installs only.")
	items += _described(
		_action(f"  Firewall (UFW)  {'enabled' if config.ufw else 'disabled'}", lambda _: firewall(app)),
		"Default policies and allow rules for common services.")
	items += _described(_action("  Git", lambda _: git(app)),
		"Name, email, default branch, editor, and pull behavior.")
	swappiness_edit = urwid.Edit("  Swappiness (0-100): ", str(config.swappiness))
	urwid.connect_signal(swappiness_edit, "change", _on_swappiness, app)
	items += [
		swappiness_edit,
		urwid.Text(("label", "      Lower keeps more in RAM. 10 suits gaming, 60 is the default.")),
	]
	_open(app, "Advanced", _with_back(app, items),
	      footer_text="  ↑↓ navigate  Space toggle  Esc back")


# ── Summary / install ──────────────────────────────────────────────────

def summary(app):
	app.refresh_online()  # connectivity may have changed since the choices were made
	config = app.config
	if not config.ready():
		app.show_main()
		return
	rows = [
		("Boot mode",      config.boot_mode.upper()),
		("Install mode",   "online" if config.online else "offline (console only)"),
		("Disk",           config.disk),
		("Filesystem",     config.filesystem),
		("Keyboard",       config.keymap),
		("Timezone",       config.timezone),
		("Locale",         config.locale),
		("Hostname",       config.hostname),
		("User",           config.username),
		("Root login",     "yes" if config.root_pass else "disabled"),
		("Swap partition", "yes" if config.has_swap_partition() else "no"),
		("zram",           "yes" if config.zram else "no"),
		("Encryption",     "LUKS2" if config.luks else "no"),
		("Snapshots",      "snapper" if config.snapper else "no"),
		("Auto updates",   "yes" if config.auto_updates else "no"),
		("Desktop",        config.desktop_label()),
		("Mirror",         config.mirror),
		("Firewall",       f"ufw, {config.ufw_incoming} in, {config.ufw_outgoing} out" if config.ufw else "no"),
		("Components",     ", ".join(config.components) or "main only"),
		("Hardware",       ", ".join(config.hardware_packages) or "none"),
		("Software",       ", ".join(config.software_packages) or "none"),
		("Git",            f"{config.git_name or 'no name'} <{config.git_email or 'no email'}>, "
		                   f"branch {config.git_default_branch}, editor {config.git_editor}"),
		("Advanced",       ", ".join(config.advanced_labels()) or "none"),
	]
	items = [urwid.Text([("label", f"  {label:<16}"), ("value", value)]) for label, value in rows]
	items += [
		urwid.Divider(),
		urwid.Text(("warning", f"  WARNING: {config.erase_text()}!")),
		urwid.Divider(),
		_action(("error", "  Begin installation"), lambda _: _begin_install(app)),
	]
	_open(app, "Installation Summary", _with_back(app, items))


def _begin_install(app):
	app.install_requested = True
	raise urwid.ExitMainLoop()


# ── Shell ──────────────────────────────────────────────────────────────

def drop_to_shell(app):
	"""chroot into the target if it is mounted, otherwise open a live shell."""
	app.loop.stop()
	if os.path.isdir(f"{utils.MNT}/etc"):
		print(f"\n  Target mounted at {utils.MNT}, chrooting in.")
		print("  Type 'exit' to return to the installer.\n")
		subprocess.run(["chroot", utils.MNT, "/bin/bash"])
	else:
		print("\n  No active install, dropping to live shell.")
		print("  Type 'exit' to return to the installer.\n")
		subprocess.run(["/bin/bash"])
	app.loop.start()
	app.show_main()
