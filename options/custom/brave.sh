#!/bin/sh
# Installs Brave from its signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://brave-browser-apt-release.s3.brave.com"
KEYRING_PATH="/usr/share/keyrings/brave-browser-archive-keyring.gpg"

sudo curl -fsSLo $KEYRING_PATH $REPOSITORY_URL/brave-browser-archive-keyring.gpg
echo "deb [signed-by=$KEYRING_PATH] $REPOSITORY_URL/ stable main" | tee /etc/apt/sources.list.d/brave-browser-release.list
apt-get update
apt-get install -y --no-install-recommends brave-browser
apt-get clean