#!/bin/bash
# Minimal KDE Plasma for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends plasma-desktop plasma-workspace \
	plasma-nm plasma-pa systemsettings sddm konsole dolphin
apt-get clean