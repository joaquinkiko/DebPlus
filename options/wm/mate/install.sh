#!/bin/bash
# Minimal MATE for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	mate-core mate-terminal caja pluma mate-system-monitor vlc xserver-xorg \
	network-manager-gnome xdg-desktop-portal-gtk lightdm lightdm-gtk-greeter \
	libpam-systemd dbus-user-session pipewire pipewire-pulse wireplumber
apt-get clean