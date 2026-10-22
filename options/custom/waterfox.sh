#!/bin/sh
# Installs Waterfox from its signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://download.opensuse.org/repositories/isv:/BrowserWorks/Debian_13"
KEYRING_PATH="/usr/share/keyrings/waterfox.gpg"

apt-get install -y --no-install-recommends gnupg
curl -fsSL "$REPOSITORY_URL/Release.key" | gpg --dearmor -o "$KEYRING_PATH"
echo "deb [signed-by=$KEYRING_PATH] $REPOSITORY_URL/ /" > /etc/apt/sources.list.d/waterfox.list
apt-get update
apt-get install -y --no-install-recommends waterfox
apt-get clean