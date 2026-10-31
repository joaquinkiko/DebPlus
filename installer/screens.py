# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os
import re
import subprocess

import urwid

from . import utils
from .widgets import Selectable, SubmitEdit

KEYMAP_CHOICES = ["us", "gb", "de", "fr", "es", "it", "pt", "br",
                  "ru", "pl", "se", "no", "fi", "dk", "ch", "jp"]
FILESYSTEM_CHOICES = [
	("btrfs", "Checksums and copy-on-write snapshots"),
	("ext4",  "Simple and reliable"),
	("xfs",   "High throughput, large files"),
]


# ── Helpers ────────────────────────────────────────────────────────────

def _on_swappiness(_edit, new_text, app):
	if new_text.isdigit() and int(new_text) <= 100:
		app.config.swappiness = int(new_text)


def _on_multiarch(_box, state, app):
	app.config.multiarch_i386 = state
	_drop_unavailable(app.config)


def _requirements_met(config, requirement):
	needed = {name for name in requirement.split(",") if name != "-"}
	available = set()
	for entry in set(config.components) | set(config.hardware_packages) | set(config.software_packages):
		available |= set(entry.split())
	if config.multiarch_i386:
		available.add("i386")
	return needed <= available


def _screen(title, items):
	widgets = [urwid.Text(("title", f"\n  {title}\n")), urwid.Divider()] + items
	return urwid.ListBox(urwid.SimpleFocusListWalker(widgets))


def _action(markup, callback):
	button = Selectable(markup)
	urwid.connect_signal(button, "activate", callback)
	return button


def _back_button(app):
	return _action(("unset", "  Back"), lambda _: app.show_main())


def _set(app, attribute, value):
	setattr(app.config, attribute, value)
	app.show_main()


def _search_results(walker, matches, on_pick):
	"""Refill a results walker with selectable matches."""
	walker.clear()
	for match in matches:
		walker.append(_action(f"    {match}", lambda _, v=match: on_pick(v)))


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


# ── Network ────────────────────────────────────────────────────────────

def network(app):
	app.online = utils.is_online()
	items = [
		urwid.Text(("success", "  Connected") if app.online else ("error", "  Offline")),
		urwid.Divider(),
		_action("  Wi-Fi networks", lambda _: _wifi_scan(app)),
		_action("  Launch nmtui (full network manager)", lambda _: _launch_nmtui(app)),
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Network", items))


def _launch_nmtui(app):
	app.loop.stop()
	subprocess.run(["nmtui"])
	app.loop.start()
	network(app)


def _wifi_scan(app):
	app.show_screen(_screen("Wi-Fi", [urwid.Text(("warning", "  Scanning..."))]))
	app.loop.draw_screen()

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
	app.show_screen(_screen("Wi-Fi", items))


def _wifi_password(app, ssid, security):
	secured = security not in ("", "--")
	password_edit = SubmitEdit("  Password: ", mask="*")
	status = urwid.Text("")

	def connect(_=None):
		password = password_edit.edit_text if secured else None
		if secured and not password:
			status.set_text(("error", "  Password cannot be empty"))
		else:
			_wifi_connect(app, ssid, password)

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
	app.show_screen(_screen("Wi-Fi", items))


def _wifi_connect(app, ssid, password):
	app.show_screen(_screen("Wi-Fi", [urwid.Text(("warning", f"  Connecting to {ssid}..."))]))
	app.loop.draw_screen()
	connected, message = utils.wifi_connect(ssid, password)
	_wifi_status(app, connected, message)


def _wifi_status(app, passed, message):
	items = [
		urwid.Text(("success" if passed else "error", f"  {message}")),
		urwid.Divider(),
		_action(("bold", "  Test connection (deb.debian.org)"), lambda _: _wifi_test(app)),
		_action(("unset", "  Back to network"), lambda _: network(app)),
	]
	app.show_screen(_screen("Wi-Fi", items))


def _wifi_test(app):
	app.show_screen(_screen("Wi-Fi", [urwid.Text(("warning", "  Testing..."))]))
	app.loop.draw_screen()
	passed, message = utils.wifi_test()
	_wifi_status(app, passed, message)


# ── Keyboard ───────────────────────────────────────────────────────────

def keyboard(app):
	error_text = urwid.Text("")
	edit = SubmitEdit("  > ")

	def choose(layout):
		error = utils.apply_keymap(layout)
		if error:
			error_text.set_text(("error", f"  {error}"))
		else:
			_set(app, "keymap", layout)

	items = [_action(f"  {layout}", lambda _, v=layout: choose(v))
	         for layout in KEYMAP_CHOICES]
	urwid.connect_signal(edit, "activate", lambda _: choose(edit.edit_text.strip()))
	items += [
		urwid.Divider(),
		urwid.Text(("label", "  Or type an XKB layout name (e.g., nl, cz, latam):")),
		edit,
		_action(("bold", "  Accept"), lambda _: choose(edit.edit_text.strip())),
		error_text,
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Keyboard Layout", items))


# ── Timezone ───────────────────────────────────────────────────────────

def timezone(app):
	all_zones = utils.get_timezones()
	results = urwid.SimpleFocusListWalker([])
	edit = urwid.Edit("  > ")

	def refresh(text):
		pattern = text.strip().lower()
		matches = [zone for zone in all_zones if pattern in zone.lower()]
		_search_results(results, matches, lambda zone: _set(app, "timezone", zone))

	urwid.connect_signal(edit, "change", lambda _, text: refresh(text))
	refresh("")

	items = [
		urwid.Text(("label", "  Scroll the list or type to filter (e.g., chicago, Europe/Oslo):")),
		edit,
		urwid.BoxAdapter(urwid.ListBox(results), 14),
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Timezone", items))


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
			error_text.set_text(("error", f"  '{value}' is not a known locale"))
		else:
			_set(app, "locale", value)

	def refresh(text):
		pattern = text.strip().lower()
		matches = [loc for loc in utf8_locales if pattern in loc.lower()]
		_search_results(results, matches, accept)

	urwid.connect_signal(edit, "change", lambda _, text: refresh(text))
	urwid.connect_signal(edit, "activate", lambda _: accept(edit.edit_text))
	refresh("")

	items = [
		urwid.Text(("label", "  Scroll the list or type to filter (e.g., en_US, SE, nb_NO):")),
		edit,
		urwid.BoxAdapter(urwid.ListBox(results), 8),
		error_text,
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Locale", items))


# ── Disk ───────────────────────────────────────────────────────────────

def disk(app):
	items = [
		_action(f"  {device:<14}{size:<10}{model}",
		        lambda _, v=device: _set(app, "disk", v))
		for device, size, model in utils.get_disks()
	]
	if not items:
		items.append(urwid.Text(("error", "  No disks found")))
	items += [urwid.Divider(), _back_button(app)]
	app.show_screen(
		_screen("Disk Selection", items),
		footer_text="  ↑↓ navigate  Enter select  WARNING: selected disk will be erased",
	)


# ── Filesystem ─────────────────────────────────────────────────────────

def filesystem(app):
	items = []
	for name, description in FILESYSTEM_CHOICES:
		items.append(_action(("bold", f"  {name}"),
		                     lambda _, v=name: _set(app, "filesystem", v)))
		items.append(urwid.Text(("label", f"  {description}")))
		items.append(urwid.Divider())
	items.append(_back_button(app))
	app.show_screen(_screen("Filesystem", items))


# ── Hostname ───────────────────────────────────────────────────────────

def hostname(app):
	edit = SubmitEdit("  Hostname: ", app.config.hostname)
	error_text = urwid.Text("")

	def accept(_):
		value = edit.edit_text
		if re.match(r"^[a-zA-Z][a-zA-Z0-9-]*$", value):
			_set(app, "hostname", value)
		else:
			error_text.set_text(("error", "  Invalid hostname"))

	urwid.connect_signal(edit, "activate", accept)
	items = [
		urwid.Text(("label", "  System identifier. Must start with a letter.")),
		urwid.Divider(),
		edit,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Hostname", items))


# ── User ───────────────────────────────────────────────────────────────

def user(app):
	username_edit = SubmitEdit("  Username: ", app.config.username)
	pass_edit = SubmitEdit("  Password: ", mask="*")
	pass_confirm = SubmitEdit("  Confirm:  ", mask="*")
	root_edit = SubmitEdit("  Root password: ", mask="*")
	root_confirm = SubmitEdit("  Confirm:       ", mask="*")
	fields = [username_edit, pass_edit, pass_confirm, root_edit, root_confirm]
	error_text = urwid.Text("")

	def accept(_=None):
		name = username_edit.edit_text
		if not re.match(r"^[a-z][a-z0-9_-]*$", name):
			error_text.set_text(("error", "  Invalid username"))
		elif not pass_edit.edit_text:
			error_text.set_text(("error", "  Password cannot be empty"))
		elif pass_edit.edit_text != pass_confirm.edit_text:
			error_text.set_text(("error", "  User passwords don't match"))
		elif root_edit.edit_text and root_edit.edit_text != root_confirm.edit_text:
			error_text.set_text(("error", "  Root passwords don't match"))
		else:
			app.config.username = name
			app.config.user_pass = pass_edit.edit_text
			app.config.root_pass = root_edit.edit_text
			app.show_main()

	def advance(field):
		position = fields.index(field)
		if position == len(fields) - 1:
			accept()
		else:
			listbox.set_focus(listbox.body.index(fields[position + 1]))

	for field in fields:
		urwid.connect_signal(field, "activate", lambda _, f=field: advance(f))

	items = [
		urwid.Text(("label", "  Lowercase letters, numbers, hyphens, underscores.")),
		urwid.Divider(),
		username_edit,
		pass_edit,
		pass_confirm,
		urwid.Divider(),
		urwid.Text(("label", "  Root password (leave blank to disable root login):")),
		root_edit,
		root_confirm,
		error_text,
		urwid.Divider(),
		_action(("bold", "  Accept"), accept),
		urwid.Divider(),
		_back_button(app),
	]
	listbox = _screen("User Account", items)
	app.show_screen(listbox)


# ── Desktop and window managers ────────────────────────────────────────

def _choice_rows(app, choice_id, label, detail, description):
	return [
		_action(f"  {label:<14}{detail}",
		        lambda _, v=choice_id: _set(app, "desktop", v)),
		urwid.Text(("label", f"      {description}")),
		urwid.Divider(),
	]


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
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Custom Repo", items))


def desktop(app):
	items = []
	for desktop_id, label, package, description in utils.desktop_rows():
		items += _choice_rows(app, desktop_id, label, package, description)
	wm_items = []
	for wm_id, label, repo, _ref, description in utils.wm_rows():
		wm_items += _choice_rows(app, wm_id, label, "", description)
	if wm_items:
		items += wm_items
	items += [
		_action("  Custom Repo", lambda _: _custom_repo(app)),
		urwid.Text(("label", "      Any git repo with an install.sh at its root.")),
		urwid.Divider(),
	]
	items += [
		urwid.Text(("label", "  Default GUI environment.")),
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Desktop / Window Manager", items))


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
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Packages", items),
	                footer_text="  ↑↓ navigate  Space toggle  Esc back")


# ── Hardware and Software (optional packages) ──────────────────────────

def _drop_unavailable(config):
	"""Remove selected packages whose requirements are no longer met."""
	requirements = utils.package_requirements()
	for attribute in ("hardware_packages", "software_packages"):
		chosen = getattr(config, attribute)
		chosen[:] = [package for package in chosen
		             if _requirements_met(config, requirements.get(package, "-"))]


def _on_archive_toggle(box, state, user_data):
	_on_toggle(box, state, user_data)
	if not state:
		_drop_unavailable(user_data[0].config)


def _on_package_toggle(box, state, user_data):
	_on_toggle(box, state, user_data)
	_drop_unavailable(user_data[0].config)


def _package_row(app, menu, package, label, requirement):
	chosen = getattr(app.config, f"{menu}_packages")
	if _requirements_met(app.config, requirement):
		return urwid.CheckBox(
			f"  {label}", state=package in chosen,
			on_state_change=_on_package_toggle,
			user_data=(app, f"{menu}_packages", package))
	return urwid.Text(("unset", f"  {label}  requires {requirement}"))


def _extras_screen(app, menu, title):
	chosen = getattr(app.config, f"{menu}_packages")
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
		urwid.Text(("label", "  Optional. Installed after the desktop.")),
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen(title, items),
	                footer_text="  ↑↓ navigate  Space toggle  Esc back")


def hardware(app):
	_extras_screen(app, "hardware", "Hardware")


def software(app):
	_extras_screen(app, "software", "Software")


# ── Advanced ───────────────────────────────────────────────────────────
def _on_backports_repo(_box, state, app):
	app.config.backports_repo = state
	if not state:
		app.config.backports_kernel = False
		app.config.backports_mesa = False
	advanced(app)


def advanced(app):
	config = app.config
	items = [
		urwid.CheckBox("  32-bit (i386) support", state=config.multiarch_i386,
		               on_state_change=_on_multiarch,
		               user_data=app),
		urwid.Text(("label", "      Adds the i386 architecture for 32-bit libraries and apps.")),
		urwid.Divider(),
		urwid.CheckBox("  Backports repository", state=config.backports_repo,
		               on_state_change=_on_backports_repo,
		               user_data=app),
		urwid.Text(("label", "      trixie-backports, pinned so nothing upgrades unless requested.")),
		urwid.Divider(),
	]
	if config.backports_repo:
		items += [
			urwid.CheckBox("  Backports kernel", state=config.backports_kernel,
			               on_state_change=_on_flag,
			               user_data=(app, "backports_kernel")),
			urwid.Text(("label", "      Newer kernel from trixie-backports, kept updated.")),
			urwid.Divider(),
			urwid.CheckBox("  Backports Mesa", state=config.backports_mesa,
			               on_state_change=_on_flag,
			               user_data=(app, "backports_mesa")),
			urwid.Text(("label", "      Newer Mesa drivers from trixie-backports, kept updated.")),
			urwid.Divider(),
		]
	else:
		items += [
			urwid.Text(("unset", "  Backports kernel and Mesa require the backports repository.")),
			urwid.Divider(),
		]
	swappiness_edit = urwid.Edit("  Swappiness (0-100): ", str(config.swappiness))
	urwid.connect_signal(swappiness_edit, "change", _on_swappiness, app)
	items += [
		swappiness_edit,
		urwid.Text(("label", "      Lower keeps more in RAM. 10 suits gaming, 60 is the default.")),
		urwid.Divider(),
	]
	items.append(_back_button(app))
	app.show_screen(_screen("Advanced", items),
	                footer_text="  ↑↓ navigate  Space toggle  Esc back")


# ── Summary / install ──────────────────────────────────────────────────

def summary(app):
	config = app.config
	rows = [
		("Boot mode",  config.boot_mode.upper()),
		("Disk",       config.disk),
		("Filesystem", config.filesystem),
		("Keyboard",   config.keymap),
		("Timezone",   config.timezone),
		("Locale",     config.locale),
		("Hostname",   config.hostname),
		("User",       config.username),
		("Root login", "yes" if config.root_pass else "disabled"),
		("Desktop",    config.desktop_label()),
		("Components", ", ".join(config.components) or "main only"),
		("Hardware",   ", ".join(config.hardware_packages) or "none"),
		("Software",   ", ".join(config.software_packages) or "none"),
		("Advanced",   ", ".join(config.advanced_labels()) or "none"),
	]
	items = [urwid.Text([("label", f"  {label:<16}"), ("value", value)])
	         for label, value in rows]
	items += [
		urwid.Divider(),
		urwid.Text(("warning",
		            f"  WARNING: ALL data on {config.disk} will be destroyed!")),
		urwid.Divider(),
		_action(("error", "  Begin installation"), lambda _: _begin_install(app)),
		urwid.Divider(),
		_back_button(app),
	]
	app.show_screen(_screen("Installation Summary", items))


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
