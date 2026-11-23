#!/bin/bash
# Minimal MATE for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	mate-core mate-terminal caja pluma mate-system-monitor vlc \
	network-manager-gnome xdg-desktop-portal-gtk lightdm lightdm-gtk-greeter
apt-get clean