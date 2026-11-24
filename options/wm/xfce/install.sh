#!/bin/bash
# Minimal Xfce for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	xfce4-session xfwm4 xfdesktop4 xfce4-panel xfce4-settings \
	xfce4-terminal xfce4-power-manager xfce4-notifyd xfce4-screensaver \
	xfce4-appfinder xfce4-taskmanager thunar mousepad parole xserver-xorg \
	network-manager-gnome xdg-desktop-portal-gtk lightdm lightdm-gtk-greeter
apt-get clean