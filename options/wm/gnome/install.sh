#!/bin/bash
# Minimal GNOME for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

purge_installed() {
	local installed_packages=()
	local package
	for package in "$@"; do
		dpkg -s "$package" &>/dev/null && installed_packages+=("$package")
	done
	if [ ${#installed_packages[@]} -gt 0 ]; then
		apt-get purge -y "${installed_packages[@]}"
	fi
}

apt-get install -y --no-install-recommends gnome-core gdm3
purge_installed gnome-initial-setup gnome-user-docs gnome-software \
    gnome-maps gnome-weather gnome-contacts gnome-clocks gnome-music \
	gnome-calendar epiphany-browser
apt-get autoremove --purge -y
apt-get clean