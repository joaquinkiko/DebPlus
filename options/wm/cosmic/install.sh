#!/bin/bash
# Setup for https://justaguy.dev/drew/cosmic-trixie
set -e
export DEBIAN_FRONTEND=noninteractive

REPO_URL="https://apt.justaguy.dev/cosmic-trixie"
KEYRING_PATH="/usr/share/keyrings/cosmic-trixie.asc"

curl -fsSL $REPO_URL/key.asc -o $KEYRING_PATH

# Add the repository to sources (one line)
echo "deb [arch=amd64 signed-by=$KEYRING_PATH] $REPO_URL trixie main" | tee /etc/apt/sources.list.d/cosmic-trixie.list

apt-get update
apt-get install -y cosmic-desktop
apt-get clean

