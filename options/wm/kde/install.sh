#!/bin/bash
# Minimal KDE Plasma for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

echo "shared/default-x-display-manager select sddm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	plasma-desktop plasma-workspace kwin-x11 kwin-wayland \
	plasma-nm plasma-pa systemsettings sddm \
	konsole dolphin kate dragonplayer plasma-systemmonitor plasma-discover \
	xdg-desktop-portal-kde
apt-get clean