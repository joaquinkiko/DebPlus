#!/bin/sh
# Installs VS Code from Microsoft's signed apt repository. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

REPOSITORY_URL="https://packages.microsoft.com/repos/code"
KEYRING_PATH="/usr/share/keyrings/microsoft.gpg"

apt-get install -y --no-install-recommends gnupg
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes -o "$KEYRING_PATH"
echo "deb [arch=amd64 signed-by=$KEYRING_PATH] $REPOSITORY_URL stable main" > /etc/apt/sources.list.d/vscode.list

echo "code code/add-microsoft-repo boolean false" | debconf-set-selections

apt-get update
apt-get install -y --no-install-recommends code
apt-get clean
