#!/bin/sh
# Install lutris
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://download.opensuse.org/repositories/home:/strycore:/lutris/Debian_13"
KEYRING_PATH="/etc/apt/keyrings/lutris.gpg"

apt-get install -y --no-install-recommends gnupg
echo -e "Types: deb\nURIs: $REPOSITORY_URL/\nSuites: ./\nComponents: \nSigned-By: $KEYRING_PATH" | tee /etc/apt/sources.list.d/lutris.sources > /dev/null
wget -q -O- $REPOSITORY_URL/Release.key | gpg --dearmor -o $KEYRING_PATH

apt-get update
apt-get install -y --no-install-recommends lutris
apt-get clean
