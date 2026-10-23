# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import os

import urwid

from . import screens, utils
from .config import Config
from .widgets import Selectable

PALETTE = [
	("header",  "black",            "brown",     None, "#14141c,bold", "#d4a017"),
	("footer",  "dark gray",        "",          None, "#6a6a75",      ""),
	("focus",   "white,bold",       "brown",     None, "#ffffff,bold", "#4a3a12"),
	("title",   "yellow,bold",      "",          None, "#d4a017,bold", ""),
	("value",   "yellow,bold",      "",          None, "#d4a017,bold", ""),
	("unset",   "dark gray",        "",          None, "#6a6a75",      ""),
	("label",   "dark gray",        "",          None, "#8a8a95",      ""),
	("success", "light green,bold", "",          None, "#7bc96f,bold", ""),
	("error",   "light red",        "",          None, "#e05555",      ""),
	("warning", "yellow",           "",          None, "#d4a017",      ""),
	("bold",    "white,bold",       "",          None, "#e8e8ef,bold", ""),
	("border",  "dark gray",        "",          None, "#8a6a20",      ""),
]

# Console fonts lack most non-CP437 glyphs, so fall back to ASCII on tty1.
FANCY = os.environ.get("TERM", "") != "linux"
GLYPH_DONE = "✓" if FANCY else "√"
GLYPH_ACTIVE = "▸" if FANCY else ">"
GLYPH_TODO = "·"
CORNERS = (dict(tlcorner="╭", trcorner="╮", blcorner="╰", brcorner="╯")
           if FANCY else {})

RAIL_WIDTH = 26
MAIN_FOOTER = "  Tab/↑↓ navigate  Enter select  Esc back  q quit"
SCREEN_FOOTER = "  Tab/↑↓ navigate  Enter select  Esc back"


def _chosen_label(selected):
	"""Rail value for an optional step: a count, or blank when nothing is chosen."""
	return f"{len(selected)} selected" if selected else ""


class App:
	def __init__(self, config: Config):
		self.config = config
		self.install_requested = False
		self._in_main = True
		self._menu_focus = 0
		self._active_step = None
		self.online = utils.is_online()

		self.frame = urwid.Frame(
			urwid.SolidFill(),
			header=self._make_header(),
			footer=self._make_footer(MAIN_FOOTER),
		)
		screen = urwid.raw_display.Screen()
		if FANCY:
			try:
				screen.set_terminal_properties(colors=2**24)
			except Exception:
				pass
		self.loop = urwid.MainLoop(
			self.frame, PALETTE, screen=screen,
			unhandled_input=self._input,
			input_filter=self._filter_input,
		)
		self.show_main()

	def run(self):
		self.loop.run()
		return self.install_requested

	def _filter_input(self, keys, raw):
		"""Map Tab and Shift+Tab to Down and Up."""
		return ["down" if key == "tab" else "up" if key == "shift tab" else key
		        for key in keys]

	def _input(self, key):
		if key == "esc" and not self._in_main:
			self.show_main()
		elif key in ("q", "Q") and self._in_main:
			raise urwid.ExitMainLoop()

	def _make_header(self):
		left = urwid.Text(f"  {utils.PROFILE['PROFILE_NAME']} Installer")
		right = urwid.Text(f"{self.config.boot_mode.upper()}  ", align="right")
		return urwid.AttrMap(urwid.Columns([left, right]), "header")

	def _make_footer(self, text):
		return urwid.AttrMap(urwid.Text(text), "footer")

	def _steps(self):
		"""(label, value, callback) per step. A truthy value marks it done."""
		config = self.config
		return [
			("Network",      "connected" if self.online else "",
			                 lambda: screens.network(self)),
			("Keyboard",     config.keymap,     lambda: screens.keyboard(self)),
			("Timezone",     config.timezone,   lambda: screens.timezone(self)),
			("Locale",       config.locale,     lambda: screens.locale(self)),
			("Disk",         config.disk,       lambda: screens.disk(self)),
			("Filesystem",   config.filesystem, lambda: screens.filesystem(self)),
			("Hostname",     config.hostname,   lambda: screens.hostname(self)),
			("User account", config.username,   lambda: screens.user(self)),
			("Desktop",      config.desktop,    lambda: screens.desktop(self)),
			("Components",     _chosen_label(config.components),
			                 lambda: screens.packages_menu(self)),
			("Hardware",     _chosen_label(config.hardware_packages),
			                 lambda: screens.hardware(self)),
			("Software",     _chosen_label(config.software_packages),
			                 lambda: screens.software(self)),
			("Advanced",     _chosen_label(config.advanced_labels()),
			                 lambda: screens.advanced(self)),
		]

	def _build_rail(self, steps, active=None):
		items = [urwid.Divider()]

		for index, (label, value, callback) in enumerate(steps):
			if index == active:
				glyph, glyph_style, label_style = GLYPH_ACTIVE, "title", "bold"
			elif value:
				glyph, glyph_style, label_style = GLYPH_DONE, "success", "bold"
			else:
				glyph, glyph_style, label_style = GLYPH_TODO, "unset", "label"
			item = Selectable([(glyph_style, f" {glyph} "), (label_style, label)])
			urwid.connect_signal(
				item, "activate",
				lambda _, cb=callback, idx=index: self._menu_activate(cb, idx))
			items.append(item)

		items.append(urwid.Divider())

		if self.config.fake_mode:
			install = urwid.Text(("warning", "   Install (testing mode)"))
		elif self.config.ready():
			install = Selectable(("success", f" {GLYPH_ACTIVE} Install"))
			urwid.connect_signal(install, "activate",
			                     lambda _: screens.summary(self))
		else:
			install = urwid.Text(
				("unset", f"   Install ({self.config.missing_count()} to go)"))
		items.append(install)

		shell = Selectable(("bold", "   Shell"))
		urwid.connect_signal(shell, "activate",
		                     lambda _: screens.drop_to_shell(self))
		items.append(shell)

		abort = Selectable(("bold", "   Abort"))
		urwid.connect_signal(abort, "activate", lambda _: self._exit())
		items.append(abort)

		return urwid.ListBox(urwid.SimpleFocusListWalker(items))

	def _plan_panel(self):
		config = self.config
		rows = [urwid.Divider()]
		for label, value in [
			("Boot",       config.boot_mode.upper()),
			("Disk",       config.disk),
			("Filesystem", config.filesystem),
			("Keyboard",   config.keymap),
			("Timezone",   config.timezone),
			("Locale",     config.locale),
			("Hostname",   config.hostname),
			("User",       config.username),
			("Desktop",    config.desktop_label()),
			("Components",   ", ".join(["main"] + config.components)),
			("Extras",     ", ".join(config.hardware_packages + config.software_packages)),
			("Advanced",   ", ".join(config.advanced_labels())),
		]:
			style = "value" if value else "unset"
			rows.append(urwid.Text(
				[("label", f" {label:<13}"), (style, value or "not set")]))

		if config.disk:
			rows.append(urwid.Divider())
			rows.append(urwid.Text(
				("warning", f" ! All data on {config.disk} will be erased")))

		box = urwid.LineBox(
			urwid.Filler(urwid.Pile(rows), valign="top"),
			title="Install plan", title_align="left", title_attr="title",
			**CORNERS,
		)
		return urwid.AttrMap(box, "border")

	def show_main(self):
		"""Rail focused, install plan in the right pane."""
		if not self._in_main:
			self._menu_focus += 1

		self._in_main = True
		self._active_step = None

		steps = self._steps()
		self._menu_focus = min(self._menu_focus, len(steps) - 1)

		rail = self._build_rail(steps)
		body = urwid.Columns([(RAIL_WIDTH, rail), self._plan_panel()],
		                     dividechars=2)
		self.frame.body = urwid.Padding(body, left=1, right=1)
		self.frame.footer = self._make_footer(MAIN_FOOTER)

		try:
			rail.set_focus(self._menu_focus + 1)  # +1 skips the top divider
		except (IndexError, ValueError):
			pass

	def show_screen(self, widget, footer_text=None):
		"""Open a step's screen in the right pane, rail stays visible."""
		self._in_main = False

		rail = self._build_rail(self._steps(), active=self._active_step)
		body = urwid.Columns([(RAIL_WIDTH, rail), widget], dividechars=2)
		body.focus_position = 1

		self.frame.body = urwid.Padding(body, left=1, right=1)
		self.frame.footer = self._make_footer(footer_text or SCREEN_FOOTER)

	def _menu_activate(self, callback, index):
		self._menu_focus = index
		self._active_step = index
		callback()

	def _exit(self):
		raise urwid.ExitMainLoop()
