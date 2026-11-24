#!/bin/bash
# Minimal Cinnamon for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

# Network applet ships in cinnamon-core
apt-get install -y --no-install-recommends \
	cinnamon-core nemo gnome-terminal xed celluloid gnome-system-monitor \
	xdg-desktop-portal-gtk lightdm lightdm-gtk-greeter xserver-xorg
apt-get clean