#!/bin/sh
# Uses backport Steam if backports enabled to fix install bug
# If install still fails, manually install i386 mesa drivers an install again
set -e
export DEBIAN_FRONTEND=noninteractive

target_flag=""
if [ -n "$DEBPLUS_BACKPORTS_SUITE" ]; then
	target_flag="-t $DEBPLUS_BACKPORTS_SUITE"
fi

dpkg --add-architecture i386
apt-get update

install_steam() {
	apt-get install -y $target_flag steam-installer steam-devices
}

if ! install_steam; then
	echo "steam-installer failed, installing i386 mesa and retrying"
	apt-get install -y $target_flag libgl1-mesa-dri:i386 libglx-mesa0:i386
	install_steam
fi

apt-get clean