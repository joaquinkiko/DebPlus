#!/bin/sh
# Installs Sublime from its signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://download.sublimetext.com"
KEYRING_PATH="/etc/apt/keyrings/sublimehq-pub.asc"

wget -qO - $REPOSITORY_URL/sublimehq-pub.gpg | sudo tee $KEYRING_PATH > /dev/null
echo -e "Types: deb\nURIs: $REPOSITORY_URL/\nSuites: apt/stable/\nSigned-By: $KEYRING_PATH" | sudo tee /etc/apt/sources.list.d/sublime-text.sources

apt-get update
apt-get install -y --no-install-recommends sublime-text
apt-get clean