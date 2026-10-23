#!/bin/bash
# Minimal Cinnamon for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends cinnamon-core gnome-terminal \
	lightdm lightdm-gtk-greeter
apt-get clean