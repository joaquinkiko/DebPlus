#!/bin/sh
# Install lutris
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://download.opensuse.org/repositories/home:/strycore:/lutris/Debian_13"
KEYRING_PATH="/etc/apt/keyrings/lutris.gpg"

apt-get install -y --no-install-recommends gnupg
mkdir -p /etc/apt/keyrings
# printf, not echo -e: sh is dash here and its echo does not take -e
printf 'Types: deb\nURIs: %s/\nSuites: ./\nComponents: \nSigned-By: %s\n' \
	"$REPOSITORY_URL" "$KEYRING_PATH" > /etc/apt/sources.list.d/lutris.sources
wget -q -O- "$REPOSITORY_URL/Release.key" | gpg --dearmor --yes -o "$KEYRING_PATH"

apt-get update
apt-get install -y --no-install-recommends lutris
apt-get clean
