#!/bin/bash
# Build the DebPlus installer ISO. Run as root on a Debian host.
# Requires: debootstrap, xorriso, mksquashfs (squashfs-tools), mtools, debian-archive-keyring
# grub-pc-bin, grub-efi-amd64-bin.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/profile.env"

WORK_DIR="$SCRIPT_DIR/.iso-work"
ROOTFS_DIR="$WORK_DIR/rootfs"
ISO_DIR="$WORK_DIR/iso"
BUILD_DATE="$(date +%Y%m%d)"
ISO_OUTPUT="$SCRIPT_DIR/${PROFILE_SLUG}-${SUITE}-${BUILD_DATE}.iso"
KEYRING=/usr/share/keyrings/debian-archive-keyring.gpg

die() { echo "ERROR: $*" >&2; exit 1; }
msg() { echo ":: $*"; }

cleanup_mounts() {
	for mount_point in "$ROOTFS_DIR/dev/pts" "$ROOTFS_DIR/dev" "$ROOTFS_DIR/proc" "$ROOTFS_DIR/sys"; do
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
	for f in packages/live.list packages/target.list packages/firmware.list profile.env; do
		[ -f "$SCRIPT_DIR/$f" ] || problems+=("missing file $f")
	done
	for f in installer options/custom options/wm; do
		[ -d "$SCRIPT_DIR/$f" ] || problems+=("missing directory $f")
	done
	compgen -G "$SCRIPT_DIR/options/*.list" >/dev/null || problems+=("no options/*.list files found")
	[ -f /etc/resolv.conf ] || problems+=("host has no /etc/resolv.conf (needed for chroot networking)")

	# Disk space (rootfs + squashfs + ISO), ~6 GiB to be safe
	local free_kb
	free_kb="$(df --output=avail -k "$SCRIPT_DIR" | tail -n1 | tr -d ' ')"
	(( free_kb >= 6 * 1024 * 1024 )) || problems+=("less than 6 GiB free in $SCRIPT_DIR")

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
mkdir -p "$ROOTFS_DIR" "$ISO_DIR/boot/grub" "$ISO_DIR/live"

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
install -m644 "$SCRIPT_DIR/packages/target.list" "$SCRIPT_DIR/packages/firmware.list" "$ROOTFS_DIR/opt/debplus/packages/"
cp -a "$SCRIPT_DIR/installer" "$ROOTFS_DIR/opt/debplus/"
install -d "$ROOTFS_DIR/opt/debplus/options"
install -m644 "$SCRIPT_DIR/profile.env" "$ROOTFS_DIR/opt/debplus/"
install -m644 "$SCRIPT_DIR"/options/*.list "$ROOTFS_DIR/opt/debplus/options/"
cp -a "$SCRIPT_DIR/options/custom" "$ROOTFS_DIR/opt/debplus/options/"
cp -a "$SCRIPT_DIR/options/wm" "$ROOTFS_DIR/opt/debplus/options/"

cleanup_mounts

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
