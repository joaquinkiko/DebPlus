#!/bin/sh
# Installs VSCodium from its signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://download.vscodium.com/debs"
KEYRING_PATH="/usr/share/keyrings/vscodium-archive-keyring.gpg"

apt-get install -y --no-install-recommends gnupg
curl -fsSL https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/-/raw/master/pub.gpg | gpg --dearmor -o "$KEYRING_PATH"
printf 'Types: deb\nURIs: %s\nSuites: vscodium\nComponents: main\nArchitectures: amd64\nSigned-By: %s\n' \
	"$REPOSITORY_URL" "$KEYRING_PATH" > /etc/apt/sources.list.d/vscodium.sources

apt-get update
apt-get install -y --no-install-recommends codium
apt-get clean
