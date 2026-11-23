#!/bin/bash
# Setup for https://justaguy.dev/drew/cosmic-trixie
set -e
export DEBIAN_FRONTEND=noninteractive

REPO_URL="https://apt.justaguy.dev/cosmic-trixie"
KEYRING_PATH="/usr/share/keyrings/cosmic-trixie.asc"

curl -fsSL "$REPO_URL/key.asc" -o "$KEYRING_PATH"

echo "deb [arch=amd64 signed-by=$KEYRING_PATH] $REPO_URL trixie main" > /etc/apt/sources.list.d/cosmic-trixie.list
printf 'Package: *\nPin: origin apt.justaguy.dev\nPin-Priority: 100\n' > /etc/apt/preferences.d/cosmic-trixie

apt-get update
apt-get install -y cosmic-desktop

# Make cosmic-greeter the display manager, in case its package didn't claim the slot
GREETER_UNIT="$(dpkg -L cosmic-greeter | grep -m1 -E '/systemd/system/.*\.service$' || true)"
if [ -n "$GREETER_UNIT" ]; then
	ln -sf "$GREETER_UNIT" /etc/systemd/system/display-manager.service
fi

apt-get clean
