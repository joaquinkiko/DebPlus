#!/bin/sh
# Installs Signal Desktop from its signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://updates.signal.org/desktop/apt"
KEYRING_PATH="/usr/share/keyrings/signal-desktop-keyring.gpg"

apt-get install -y --no-install-recommends gnupg
curl -fsSL "$REPOSITORY_URL/keys.asc" | gpg --dearmor --yes -o "$KEYRING_PATH"
echo "deb [arch=amd64 signed-by=$KEYRING_PATH] $REPOSITORY_URL xenial main" > /etc/apt/sources.list.d/signal-xenial.list

apt-get update
apt-get install -y --no-install-recommends signal-desktop
apt-get clean
