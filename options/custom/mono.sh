#!/bin/sh
# Adds the Mono repository and installs mono-devel. Runs inside the target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

KEYRING_PATH="/usr/share/keyrings/mono-official-archive-keyring.gpg"
KEY_ID="3FA7E0328081BFF6A14DA29AA6A19B38D3D831EF"
REPOSITORY_URL="https://download.mono-project.com/repo/debian"
SOURCE_PATH="/etc/apt/sources.list.d/mono-official-stable.list"
SEQUOIA_CONFIG="/usr/share/apt/default-sequoia.config"
SEQUOIA_BACKUP="/usr/share/apt/default-sequoia.config.orig"

restore_sequoia() {
	if [ -f "$SEQUOIA_BACKUP" ]; then
		mv "$SEQUOIA_BACKUP" "$SEQUOIA_CONFIG"
	fi
}
trap restore_sequoia EXIT

apt-get install -y --no-install-recommends dirmngr ca-certificates gnupg

GNUPG_HOME="$(mktemp -d)"
gpg --homedir "$GNUPG_HOME" --no-default-keyring \
	--keyring "gnupg-ring:$KEYRING_PATH" \
	--keyserver hkp://keyserver.ubuntu.com:80 --recv-keys "$KEY_ID"
rm -rf "$GNUPG_HOME"
chmod +r "$KEYRING_PATH"

echo "deb [signed-by=$KEYRING_PATH] $REPOSITORY_URL stable-buster main" > "$SOURCE_PATH"

# Trixie rejects the repo key's SHA1 self-signature. Extend the policy expiry
# on a copy only for this install, restored on exit.
cp -p "$SEQUOIA_CONFIG" "$SEQUOIA_BACKUP"
sed -i 's/2026-[0-9-]*/2099-12-31/g' "$SEQUOIA_CONFIG"

apt-get update
apt-get install -y --no-install-recommends mono-complete
apt-get clean
