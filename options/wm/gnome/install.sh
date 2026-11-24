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

echo "shared/default-x-display-manager select gdm3" | debconf-set-selections

apt-get install -y --no-install-recommends gnome-core gdm3 pkexec \
	gnome-terminal nautilus gnome-text-editor gnome-system-monitor \
	gnome-software totem xdg-desktop-portal-gnome
apt-get autoremove --purge -y
apt-get clean