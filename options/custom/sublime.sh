#!/bin/sh
# Installs Sublime from its signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://download.sublimetext.com"
KEYRING_PATH="/etc/apt/keyrings/sublimehq-pub.asc"

mkdir -p /etc/apt/keyrings
curl -fsSL "$REPOSITORY_URL/sublimehq-pub.gpg" -o "$KEYRING_PATH"
printf 'Types: deb\nURIs: %s/\nSuites: apt/stable/\nSigned-By: %s\n' \
	"$REPOSITORY_URL" "$KEYRING_PATH" > /etc/apt/sources.list.d/sublime-text.sources

apt-get update
apt-get install -y --no-install-recommends sublime-text
apt-get clean
