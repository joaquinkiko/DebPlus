# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import functools
import os

import urwid

from . import hwdetect, screens, utils
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
SPLASH_FOOTER = "  Tab/↑↓ navigate  Enter select  q quit"
INTRO_FOOTER = "  Tab/↑↓ navigate  Space toggle  Enter select  q quit"
LOGO = """
       _,###._                                        ,#.                                    
     ,#########.             ##:          :##        :###:                                   
   .######^######.           ##'          `##         `#'                           ####     
  .####'     `####       __  ##     __     ##  __      _     __          _          ####     
  ####' ,###. `###:    ,####:##  ,######.  ##.#####. :### ,######. ###.####:       ,####.    
  ###'  ##'`# ,###    ,##' `###  ##:  :##  ###' `###  ##' #:   `## `###' `##:    ##########  
  ###.  ###._,###'    ##    `##  ##    ##  ##'   `##  ##    ___,##  ##:   `##    ##########  
  `###  `######'      ##     ##  #######:  ##     ##  ##  .#######  ##'    ##    ##########  
   `###    `"'        ##     ##  ##'       ##     ##  ##  ##'  `##  ##     ##      `####'    
    `###.             ##.   ,##  ##        ##    ,##  ##  ##    ##  ##     ##       ####     
      `###,           :#:._,###  ##:__,##  ##:__,##' ,##. ##.__:##. ##     ##       ####     
        `"##:.        `:#### ###  ######'  `######'  #### `#####"## ##     ##                
""".strip("\n")


def _chosen_label(selected):
	"""Rail value for an optional step: a count, or blank when nothing is chosen."""
	return f"{len(selected)} selected" if selected else ""


def _rail_item(glyph, glyph_style, label, label_style):
	return Selectable([(glyph_style, f" {glyph} "), (label_style, label)])


class App:
	def __init__(self, config: Config):
		self.config = config
		self.install_requested = False
		self.preflight = []
		self.autodetect_used = False
		self.detections = []
		self.recommended = []
		self.suggestions = []
		self.picked = {}
		self._in_main = True
		self._in_splash = False
		self._menu_focus = 0
		self._active_step = None
		self.online = False
		self.refresh_online()

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
		self.show_splash()

	def run(self):
		self.loop.run()
		return self.install_requested

	def refresh_online(self):
		"""Re-check connectivity and drop any selection that needs the network."""
		self.online = utils.is_online()
		self.config.set_online(self.online)
		screens.drop_unavailable(self.config)
		return self.online

	def refresh_preflight(self):
		"""Re-run the pre-flight checks. The single place they are run from the UI."""
		self.preflight = utils.preflight(self.config, self.online)

	def preflight_issues(self):
		return any(check.level != "ok" for check in self.preflight)

	def preflight_blocked(self):
		return any(check.level == "block" for check in self.preflight)

	def preflight_passed(self):
		return not self.preflight_blocked()

	def _filter_input(self, keys, raw):
		"""Map Tab and Shift+Tab to Down and Up."""
		return ["down" if key == "tab" else "up" if key == "shift tab" else key
		        for key in keys]

	def _input(self, key):
		if key == "esc" and not self._in_main and not self._in_splash:
			self.show_main()
		elif key in ("q", "Q") and (self._in_main or self._in_splash):
			raise urwid.ExitMainLoop()

	def _make_header(self):
		left = urwid.Text(f"  {utils.PROFILE['PROFILE_NAME']} Installer")
		right = urwid.Text(f"{self.config.boot_mode.upper()}  ", align="right")
		return urwid.AttrMap(urwid.Columns([left, right]), "header")

	def _make_footer(self, text):
		return urwid.AttrMap(urwid.Text(text), "footer")

	def _steps(self):
		"""(label, value, callback) per step in display order. A truthy value marks it done."""
		config = self.config
		steps = [
			("Network",      "connected" if self.online else "", lambda: screens.network(self)),
			("Keyboard",     config.keymap,     lambda: screens.keyboard(self)),
			("Timezone",     config.timezone,   lambda: screens.timezone(self)),
			("Locale",       config.locale,     lambda: screens.locale(self)),
			("Disk",         config.disk,       lambda: screens.disk(self)),
			("Filesystem",   config.filesystem, lambda: screens.filesystem(self)),
			("Hostname",     config.hostname,   lambda: screens.hostname(self)),
			("User account", config.username,   lambda: screens.user(self)),
			("Desktop",      config.desktop,    lambda: screens.desktop(self)),
			("Components",   _chosen_label(config.components),
			                 lambda: screens.packages_menu(self)),
			("Hardware",     _chosen_label(config.hardware_packages),
			                 lambda: screens.hardware(self)),
			("Software",     _chosen_label(config.software_packages),
			                 lambda: screens.software(self)),
		]
		if config.needs_mok():
			steps.append(("Secure Boot", "set" if config.mok_pass else "",
			              lambda: screens.secure_boot(self)))
		steps.append(("Advanced", _chosen_label(config.advanced_labels()),
		              lambda: screens.advanced(self)))
		if self.preflight_blocked():
			steps.insert(0, ("Pre-flight", "", lambda: screens.preflight(self)))
		return steps

	def _build_rail(self, steps, active=None):
		items = [urwid.Divider()]
		for index, (label, value, callback) in enumerate(steps):
			if index == active:
				item = _rail_item(GLYPH_ACTIVE, "title", label, "bold")
			elif value:
				item = _rail_item(GLYPH_DONE, "success", label, "bold")
			else:
				item = _rail_item(GLYPH_TODO, "unset", label, "label")
			urwid.connect_signal(item, "activate",
			                     functools.partial(self._menu_activate, callback, index))
			items.append(item)

		items.append(urwid.Divider())
		items.append(self._install_item())

		shell = Selectable(("bold", "   Shell"))
		urwid.connect_signal(shell, "activate", lambda _: screens.drop_to_shell(self))
		items.append(shell)

		abort = Selectable(("bold", "   Abort"))
		urwid.connect_signal(abort, "activate", lambda _: self._exit())
		items.append(abort)

		return urwid.ListBox(urwid.SimpleFocusListWalker(items))

	def _install_item(self):
		"""Install button, or a status line saying what is still missing."""
		if self.config.fake_mode:
			return urwid.Text(("warning", "   Install (testing mode)"))
		if self.config.ready() and self.preflight_passed():
			install = Selectable(("success", f" {GLYPH_ACTIVE} Install"))
			urwid.connect_signal(install, "activate", lambda _: screens.summary(self))
			return install
		reason = (f"{self.config.missing_count()} to go"
		          if self.config.missing_count() else "pre-flight")
		return urwid.Text(("unset", f"   Install ({reason})"))

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
			("Encryption", "LUKS2" if config.luks else "none"),
			("Snapshots",  "snapper" if config.snapper else "none"),
			("Components", ", ".join(["main"] + config.components)),
			("Extras",     ", ".join(config.selected_packages())),
			("Advanced",   ", ".join(config.advanced_labels())),
		]:
			style = "value" if value else "unset"
			rows.append(urwid.Text(
				[("label", f" {label:<13}"), (style, value or "not set")]))

		if config.disk:
			rows.append(urwid.Divider())
			rows.append(urwid.Text(("warning", f" ! {config.erase_text()}")))

		box = urwid.LineBox(
			urwid.Filler(urwid.Pile(rows), valign="top"),
			title="Install plan", title_align="left", title_attr="title",
			**CORNERS,
		)
		return urwid.AttrMap(box, "border")

	def _set_frame(self, steps, right, footer, active=None, focus_right=False):
		"""Draw the rail beside right. Returns the rail so the caller can set its focus."""
		rail = self._build_rail(steps, active)
		body = urwid.Columns([(RAIL_WIDTH, rail), right], dividechars=2)
		body.focus_position = 1 if focus_right else 0
		self.frame.body = urwid.Padding(body, left=1, right=1)
		self.frame.footer = self._make_footer(footer)
		return rail

	def show_splash(self):
		"""Logo and entry buttons, shown before the install menu."""
		self._in_main = False
		self._in_splash = True

		start = Selectable(("bold", "  Start Setup"))
		urwid.connect_signal(start, "activate", lambda _: self.show_intro(autodetect=False))
		detect = Selectable(("bold", "  Hardware Detect"))
		urwid.connect_signal(detect, "activate", lambda _: self.show_intro(autodetect=True))
		abort = Selectable(("bold", "  Abort"))
		urwid.connect_signal(abort, "activate", lambda _: self._exit())

		pile = urwid.Pile([
			urwid.Padding(urwid.Text(("title", LOGO)), align="center", width="pack"),
			urwid.Divider(),
			urwid.Padding(start, align="center", width=24),
			urwid.Padding(detect, align="center", width=24),
			urwid.Padding(abort, align="center", width=24),
		])
		pile.focus_position = 2  # Start Setup

		self.frame.body = urwid.Filler(pile, valign="middle")
		self.frame.footer = self._make_footer(SPLASH_FOOTER)

	def show_intro(self, autodetect):
		"""Intro screen. With autodetect, shows detections and checkboxes; always shows pre-flight warnings."""
		self._in_splash = True
		self._in_main = False
		self.refresh_preflight()
		self.autodetect_used = autodetect
		self.detections = hwdetect.detect() if autodetect else []
		self.recommended, self.suggestions = hwdetect.plan(self.detections)
		self.picked = ({package: True for package, _label in self.recommended}
		               | {package: False for package, _label in self.suggestions})

		widgets = [urwid.Text(("title", "\n  Before you start\n")), urwid.Divider()]
		if autodetect:
			widgets += screens.detect_rows(self.detections)
			if self.recommended:
				widgets.append(urwid.Text(("title", "  Recommended")))
				widgets += [self._pick_box(package, label)
				            for package, label in self.recommended]
				widgets.append(urwid.Divider())
			if self.suggestions:
				widgets.append(urwid.Text(("title", "  Suggested")))
				widgets += [self._pick_box(package, label)
				            for package, label in self.suggestions]
				widgets.append(urwid.Divider())
			if not (self.recommended or self.suggestions):
				widgets.append(urwid.Text(("label", "  Nothing to recommend for this hardware.")))
				widgets.append(urwid.Divider())
		widgets += screens.warning_rows(self.preflight)

		continue_button = Selectable(("bold", "  Continue"))
		urwid.connect_signal(continue_button, "activate", lambda _: self.finish_intro())
		widgets += [urwid.Divider(), continue_button]

		self.frame.body = urwid.ListBox(urwid.SimpleFocusListWalker(widgets))
		self.frame.footer = self._make_footer(INTRO_FOOTER)

	def _pick_box(self, package, label):
		return urwid.CheckBox(f"  {label}", state=self.picked[package],
		                      on_state_change=self._set_pick, user_data=package)

	def _set_pick(self, _box, state, package):
		self.picked[package] = state

	def finish_intro(self):
		"""Apply the ticked packages, which turns on components and multiarch only when needed."""
		chosen = [package for package, _label in self.recommended + self.suggestions
		          if self.picked.get(package)]
		hwdetect.apply(self.config, chosen)
		screens.drop_unavailable(self.config)
		self.show_main()

	def show_main(self):
		"""Rail focused, install plan in the right pane."""
		self.refresh_preflight()
		if not (self._in_main or self._in_splash):
			self._menu_focus += 1

		self._in_main = True
		self._in_splash = False
		self._active_step = None

		steps = self._steps()
		self._menu_focus = min(self._menu_focus, len(steps) - 1)
		rail = self._set_frame(steps, self._plan_panel(), MAIN_FOOTER)
		try:
			rail.set_focus(self._menu_focus + 1)  # +1 skips the top divider
		except (IndexError, ValueError):
			pass

	def show_screen(self, widget, footer_text=None):
		"""Open a step's screen in the right pane, rail stays visible."""
		self._in_main = False
		self._set_frame(self._steps(), widget, footer_text or SCREEN_FOOTER,
		                active=self._active_step, focus_right=True)

	def _menu_activate(self, callback, index, _source):
		self._menu_focus = index
		self._active_step = index
		callback()

	def _exit(self):
		raise urwid.ExitMainLoop()
