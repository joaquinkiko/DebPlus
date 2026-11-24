#!/bin/bash
# Minimal LXQt for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

echo "shared/default-x-display-manager select sddm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	lxqt-core sddm qterminal pcmanfm-qt featherpad vlc lxtask \
	network-manager-gnome xdg-desktop-portal-gtk xserver-xorg \
	libpam-systemd dbus-user-session pipewire pipewire-pulse wireplumber
apt-get clean