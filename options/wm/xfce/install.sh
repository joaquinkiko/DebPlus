#!/bin/bash
# Minimal Xfce for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends xfce4-session xfwm4 xfdesktop4 \
	xfce4-panel xfce4-settings xfce4-terminal xfce4-power-manager \
	xfce4-notifyd xfce4-screensaver thunar network-manager-gnome lightdm lightdm-gtk-greeter
apt-get clean