# DebPlus: derived from butterknife (GPL-2.0), Copyright (C) the butterknife authors.
# Modifications Copyright (C) 2026 JoaquinKiko.
# SPDX-License-Identifier: GPL-2.0-only. See COPYING and NOTICE.

import urwid


class Selectable(urwid.WidgetWrap):
	"""Selectable text row that emits 'activate' on Enter."""

	signals = ["activate"]

	def __init__(self, markup):
		self.text = urwid.Text(markup)
		super().__init__(urwid.AttrMap(self.text, "", focus_map="focus"))

	def selectable(self):
		return True

	def keypress(self, size, key):
		if key == "enter":
			self._emit("activate")
			return None
		return key


class SubmitEdit(urwid.Edit):
	"""Edit that emits 'activate' on Enter."""

	signals = ["activate", "change", "postchange"]

	def keypress(self, size, key):
		if key == "enter":
			self._emit("activate")
			return None
		return super().keypress(size, key)
