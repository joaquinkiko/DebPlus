#!/bin/sh
# Uses backport Steam if backports enabled to fix install bug
# If install still fails, manually install i386 mesa drivers an install again
set -e
export DEBIAN_FRONTEND=noninteractive

# Check if we're using backports
target_flag=""
backportsSuite="trixie-backports"
sourcesPaths="/etc/apt/sources.list /etc/apt/sources.list.d/*"
if grep -hsv '^[[:space:]]*#' $sourcesPaths | grep -q "$backportsSuite"; then
	target_flag="-t trixie-backports"
fi

# Ensure i386 is activeuser
if ! dpkg --print-foreign-architectures | grep -qx 'i386'; then
	dpkg --add-architecture i386
    apt-get update
fi


install_steam() {
	apt-get install -y $target_flag steam-installer steam-devices
}

if ! install_steam; then
	echo "steam-installer failed, installing i386 mesa and retrying"
	apt-get install -y $target_flag libgl1-mesa-dri:i386 libglx-mesa0:i386
	install_steam
fi

apt-get clean