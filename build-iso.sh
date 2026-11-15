#!/bin/bash
# Build the DebPlus installer ISO. Run as root on a Debian host.
# Requires: debootstrap, xorriso, mksquashfs (squashfs-tools), mtools, dpkg-dev, debian-archive-keyring
# grub-pc-bin, grub-efi-amd64-bin.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/profile.env"

WORK_DIR="$SCRIPT_DIR/.iso-work"
ROOTFS_DIR="$WORK_DIR/rootfs"
TARGET_DIR="$WORK_DIR/target"
ISO_DIR="$WORK_DIR/iso"
BUILD_DATE="$(date +%Y%m%d)"
ISO_OUTPUT="$SCRIPT_DIR/${PROFILE_SLUG}-${SUITE}-${BUILD_DATE}.iso"
KEYRING=/usr/share/keyrings/debian-archive-keyring.gpg

die() { echo "ERROR: $*" >&2; exit 1; }
msg() { echo ":: $*"; }

cleanup_mounts() {
	for mount_point in "$ROOTFS_DIR/dev/pts" "$ROOTFS_DIR/dev" "$ROOTFS_DIR/proc" "$ROOTFS_DIR/sys" \
	                   "$TARGET_DIR/dev/pts" "$TARGET_DIR/dev" "$TARGET_DIR/proc" "$TARGET_DIR/sys"; do
		umount -lf "$mount_point" 2>/dev/null || true
	done
}
trap cleanup_mounts EXIT

preflight() {
	local -a problems=()
	local cmd pkg var f

	[[ $EUID -eq 0 ]] || die "Must be run as root"

	# command -> package that provides it
	local -A cmd_pkg=(
		[debootstrap]=debootstrap
		[xorriso]=xorriso
		[mksquashfs]=squashfs-tools
		[grub-mkrescue]=grub-common
		[mformat]=mtools
		[dpkg-scanpackages]=dpkg-dev
		[findmnt]=util-linux
		[mount]=mount
		[umount]=mount
		[chroot]=coreutils
	)
	for cmd in "${!cmd_pkg[@]}"; do
		command -v "$cmd" >/dev/null 2>&1 || problems+=("missing command '$cmd' (apt install ${cmd_pkg[$cmd]})")
	done

	# Keyring, GRUB modules
	[ -f "$KEYRING" ] || problems+=("missing $KEYRING (apt install debian-archive-keyring)")
	[ -d /usr/lib/grub/i386-pc ] || problems+=("missing GRUB BIOS modules (apt install grub-pc-bin)")
	[ -d /usr/lib/grub/x86_64-efi ] || problems+=("missing GRUB UEFI modules (apt install grub-efi-amd64-bin)")

	# profile.env variables
	for var in PROFILE_SLUG SUITE MIRROR ISO_LABEL; do
		[ -n "${!var:-}" ] || problems+=("profile.env does not set $var")
	done

	# Input files and directories
	for f in packages/live.list packages/target.list packages/firmware.list packages/boot.list \
		packages/optional.list options/extras.list profile.env; do
		[ -f "$SCRIPT_DIR/$f" ] || problems+=("missing file $f")
	done
	for f in installer options/custom options/wm; do
		[ -d "$SCRIPT_DIR/$f" ] || problems+=("missing directory $f")
	done
	compgen -G "$SCRIPT_DIR/options/*.list" >/dev/null || problems+=("no options/*.list files found")
	[ -f /etc/resolv.conf ] || problems+=("host has no /etc/resolv.conf (needed for chroot networking)")

	# Disk space (two rootfs trees + squashfs images + ISO), ~10 GiB to be safe
	local free_kb
	free_kb="$(df --output=avail -k "$SCRIPT_DIR" | tail -n1 | tr -d ' ')"
	(( free_kb >= 10 * 1024 * 1024 )) || problems+=("less than 10 GiB free in $SCRIPT_DIR")

	if (( ${#problems[@]} )); then
		echo "ERROR: preflight failed:" >&2
		printf '  - %s\n' "${problems[@]}" >&2
		exit 1
	fi
}
preflight

cleanup_mounts
if findmnt -rno TARGET | grep -qF "$WORK_DIR/"; then
	die "Mounts still active under $WORK_DIR, unmount them before rebuilding"
fi
rm -rf "$WORK_DIR"
mkdir -p "$ROOTFS_DIR" "$TARGET_DIR" "$ISO_DIR/boot/grub" "$ISO_DIR/live" "$ISO_DIR/install/repo"

msg "Debootstrapping live environment..."
debootstrap --arch=amd64 --variant=minbase \
	--keyring=/usr/share/keyrings/debian-archive-keyring.gpg \
	"$SUITE" "$ROOTFS_DIR" "$MIRROR"

mount --bind /dev "$ROOTFS_DIR/dev"
mount --bind /dev/pts "$ROOTFS_DIR/dev/pts"
mount -t proc proc "$ROOTFS_DIR/proc"
mount -t sysfs sys "$ROOTFS_DIR/sys"

cp "$SCRIPT_DIR/packages/live.list" "$ROOTFS_DIR/tmp/live.list"

cat > "$ROOTFS_DIR/tmp/setup.sh" << 'SETUP'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

cat > /etc/apt/sources.list.d/debian.sources << SOURCES
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates
Components: main contrib non-free-firmware non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $SUITE-security
Components: main contrib non-free-firmware non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SOURCES
: > /etc/apt/sources.list

apt-get update
apt-get install -y --no-install-recommends $(grep -v '^#' /tmp/live.list)

echo "debplus-live" > /etc/hostname
systemctl enable NetworkManager
systemctl disable NetworkManager-wait-online.service

cat > /etc/default/console-setup << 'FONT'
ACTIVE_CONSOLES="/dev/tty[1-6]"
CHARMAP="UTF-8"
CODESET="guess"
FONTFACE="Terminus"
FONTSIZE="12x24"
FONT
dpkg-reconfigure -f noninteractive console-setup

mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'AUTO'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
AUTO

cat > /root/.bash_profile << 'PROFILE'
if [ "$(tty)" = "/dev/tty1" ] && [ ! -f /tmp/.debplus-launched ]; then
    touch /tmp/.debplus-launched
    clear
    cd /opt/debplus && python3 -m installer
fi
PROFILE

apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/*
SETUP

chmod +x "$ROOTFS_DIR/tmp/setup.sh"
cp /etc/resolv.conf "$ROOTFS_DIR/etc/resolv.conf"
chroot "$ROOTFS_DIR" env MIRROR="$MIRROR" SUITE="$SUITE" /tmp/setup.sh
: > "$ROOTFS_DIR/etc/resolv.conf"
rm -f "$ROOTFS_DIR/tmp/setup.sh" "$ROOTFS_DIR/tmp/live.list"

msg "Installing DebPlus into the live environment..."
install -d "$ROOTFS_DIR/opt/debplus/packages"
install -m644 "$SCRIPT_DIR"/packages/{firmware,boot}.list "$ROOTFS_DIR/opt/debplus/packages/"
cp -a "$SCRIPT_DIR/installer" "$ROOTFS_DIR/opt/debplus/"
install -d "$ROOTFS_DIR/opt/debplus/options"
install -m644 "$SCRIPT_DIR/profile.env" "$ROOTFS_DIR/opt/debplus/"
install -m644 "$SCRIPT_DIR"/options/*.list "$ROOTFS_DIR/opt/debplus/options/"
cp -a "$SCRIPT_DIR/options/custom" "$ROOTFS_DIR/opt/debplus/options/"
cp -a "$SCRIPT_DIR/options/wm" "$ROOTFS_DIR/opt/debplus/options/"

cleanup_mounts

# ── Base system image and local package repo ──
# target.squashfs holds only target.list. Everything optional (bootloaders, LUKS, snapper,
# zram, auto-updates, firmware, extras.list rows flagged "bake") is downloaded as .debs into
# a flat apt repo on the ISO, and the installer installs just what was selected, offline if
# need be. Both live in install/, outside live/, so live-boot does not layer them.
pkg_list() { grep -hEv '^[[:space:]]*(#|$)' "$@" || true; }
baked_extras() {
	awk -F'|' '/^[[:space:]]*#/ || NF < 6 { next }
		{ gsub(/^[ \t]+|[ \t]+$/, "", $6); if ($6 == "bake") print $3 }' "$1"
}
boot_set() {
	awk -F'|' -v mode="$1" '/^[[:space:]]*#/ || NF < 2 { next }
		{ gsub(/^[ \t]+|[ \t]+$/, "", $1); if ($1 == mode) print $2 }' "$SCRIPT_DIR/packages/boot.list"
}
word_list() { tr -s ' ' '\n' | sed '/^$/d' | sort -u; }

msg "Debootstrapping base system image..."
debootstrap --arch=amd64 --variant=minbase --components=main,non-free-firmware \
	--keyring="$KEYRING" "$SUITE" "$TARGET_DIR" "$MIRROR"

mount --bind /dev "$TARGET_DIR/dev"
mount --bind /dev/pts "$TARGET_DIR/dev/pts"
mount -t proc proc "$TARGET_DIR/proc"
mount -t sysfs sys "$TARGET_DIR/sys"

pkg_list "$SCRIPT_DIR/packages/target.list" | word_list > "$TARGET_DIR/tmp/base.pkgs"
# BIOS and UEFI sets are downloaded separately because grub-pc conflicts with the UEFI stack
{
	pkg_list "$SCRIPT_DIR/packages/optional.list" "$SCRIPT_DIR/packages/firmware.list"
	baked_extras "$SCRIPT_DIR/options/extras.list"
} | word_list > "$TARGET_DIR/tmp/common.pkgs"
boot_set uefi | word_list > "$TARGET_DIR/tmp/uefi.pkgs"
boot_set bios | word_list > "$TARGET_DIR/tmp/bios.pkgs"

cat > "$TARGET_DIR/tmp/setup.sh" << 'SETUP'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

# No services should start inside the build chroot
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d

cat > /etc/apt/sources.list.d/debian.sources << SOURCES
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates
Components: main non-free-firmware non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $SUITE-security
Components: main non-free-firmware non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SOURCES
: > /etc/apt/sources.list

apt-get update
apt-get install -y --no-install-recommends $(cat /tmp/base.pkgs)

# From here the apt cache must hold only what goes into the repo
apt-get clean
for set in common uefi bios; do
	apt-get install -y --download-only --no-install-recommends $(cat /tmp/$set.pkgs)
done

# Per-install identity is created by the installer
rm -f /usr/sbin/policy-rc.d
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id /etc/ssh/ssh_host_*
rm -rf /var/lib/apt/lists/* /tmp/*
SETUP

chmod +x "$TARGET_DIR/tmp/setup.sh"
rm -f "$TARGET_DIR/etc/resolv.conf"
cp /etc/resolv.conf "$TARGET_DIR/etc/resolv.conf"
chroot "$TARGET_DIR" env MIRROR="$MIRROR" SUITE="$SUITE" /tmp/setup.sh
rm -f "$TARGET_DIR/etc/resolv.conf"
: > "$TARGET_DIR/etc/resolv.conf"
cleanup_mounts

msg "Building local package repo..."
REPO_DIR="$ISO_DIR/install/repo"
cp "$TARGET_DIR"/var/cache/apt/archives/*.deb "$REPO_DIR/"
rm -f "$TARGET_DIR"/var/cache/apt/archives/*.deb
(
	cd "$REPO_DIR"
	dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null
	[ -s Packages ] || { echo "ERROR: dpkg-scanpackages produced no index" >&2; exit 1; }
	gzip -9k Packages
	# The installer runs apt with Check-Date off, so a wrong offline clock cannot reject this
	{
		echo "Origin: DebPlus"
		echo "Date: $(date -Ru)"
		echo "Label: DebPlus"
		echo "Architectures: amd64"
		echo "SHA256:"
		for index in Packages Packages.gz; do
			printf ' %s %s %s\n' "$(sha256sum "$index" | cut -d' ' -f1)" "$(stat -c %s "$index")" "$index"
		done
	} > Release
)
msg "Repo holds $(ls "$REPO_DIR"/*.deb | wc -l) packages"

msg "Creating base system squashfs (this takes a while)..."
mksquashfs "$TARGET_DIR" "$ISO_DIR/install/target.squashfs" \
	-comp zstd -Xcompression-level 19 -noappend -quiet
rm -rf "$TARGET_DIR"

msg "Creating squashfs (this takes a while)..."
mksquashfs "$ROOTFS_DIR" "$ISO_DIR/live/filesystem.squashfs" \
	-comp zstd -Xcompression-level 22 -noappend -quiet

msg "Copying kernel and initrd..."
kernel_file="$(ls -1 "$ROOTFS_DIR"/boot/vmlinuz-* | sort -V | tail -n1)"
initrd_file="$(ls -1 "$ROOTFS_DIR"/boot/initrd.img-* | sort -V | tail -n1)"
cp "$kernel_file" "$ISO_DIR/boot/vmlinuz"
cp "$initrd_file" "$ISO_DIR/boot/initrd.img"

cat > "$ISO_DIR/boot/grub/grub.cfg" << 'GRUB'
set default=0
set timeout=2

menuentry "DebPlus Installer" {
    linux /boot/vmlinuz boot=live
    initrd /boot/initrd.img
}

menuentry "DebPlus Installer (safe graphics)" {
    linux /boot/vmlinuz boot=live nomodeset
    initrd /boot/initrd.img
}
GRUB

msg "Building ISO..."
grub-mkrescue -o "$ISO_OUTPUT" "$ISO_DIR" \
	-- -volid "$ISO_LABEL" \
	-boot_image any mbr_force_bootable=on

cleanup_mounts
if findmnt -rno TARGET | grep -qF "$WORK_DIR/"; then
	die "Mounts still active under $WORK_DIR, unmount them before rebuilding"
fi
rm -rf "$WORK_DIR"
echo "Built: $ISO_OUTPUT"
