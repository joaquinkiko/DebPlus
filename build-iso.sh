#!/bin/bash
# Build the DebPlus installer ISO. Run as root on a Debian host.
# Requires: debootstrap, xorriso, mksquashfs (squashfs-tools), mtools,
# grub-pc-bin, grub-efi-amd64-bin.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/profile.env"

WORK_DIR="$SCRIPT_DIR/.iso-work"
ROOTFS_DIR="$WORK_DIR/rootfs"
ISO_DIR="$WORK_DIR/iso"
BUILD_DATE="$(date +%Y%m%d)"
ISO_OUTPUT="$SCRIPT_DIR/${PROFILE_SLUG}-${SUITE}-${BUILD_DATE}.iso"

die() { echo "ERROR: $*" >&2; exit 1; }
msg() { echo ":: $*"; }

cleanup_mounts() {
	for mount_point in "$ROOTFS_DIR/dev/pts" "$ROOTFS_DIR/dev" "$ROOTFS_DIR/proc" "$ROOTFS_DIR/sys"; do
		umount -lf "$mount_point" 2>/dev/null || true
	done
}
trap cleanup_mounts EXIT

[[ $EUID -eq 0 ]] || die "Must be run as root"
for command_name in debootstrap xorriso mksquashfs grub-mkrescue; do
	command -v "$command_name" >/dev/null || die "Missing: $command_name"
done
[ -d /usr/lib/grub/i386-pc ] || die "Missing grub BIOS modules (grub-pc-bin)"
[ -d /usr/lib/grub/x86_64-efi ] || die "Missing grub UEFI modules (grub-efi-amd64-bin)"

rm -rf "$WORK_DIR"
mkdir -p "$ROOTFS_DIR" "$ISO_DIR/boot/grub" "$ISO_DIR/live"

msg "Debootstrapping live environment..."
debootstrap --arch=amd64 --variant=minbase "$SUITE" "$ROOTFS_DIR" "$MIRROR"

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
chroot "$ROOTFS_DIR" env MIRROR="$MIRROR" SUITE="$SUITE" /tmp/setup.sh
rm -f "$ROOTFS_DIR/tmp/setup.sh" "$ROOTFS_DIR/tmp/live.list"

msg "Installing DebPlus into the live environment..."
install -d "$ROOTFS_DIR/opt/debplus/packages"
cp -a "$SCRIPT_DIR/installer" "$ROOTFS_DIR/opt/debplus/"
install -d "$ROOTFS_DIR/opt/debplus/options"
install -m644 "$SCRIPT_DIR/profile.env" "$ROOTFS_DIR/opt/debplus/"
install -m644 "$SCRIPT_DIR"/options/*.list "$ROOTFS_DIR/opt/debplus/options/"
cp -a "$SCRIPT_DIR/options/custom" "$ROOTFS_DIR/opt/debplus/options/"

cleanup_mounts

msg "Creating squashfs (this takes a while)..."
mksquashfs "$ROOTFS_DIR" "$ISO_DIR/live/filesystem.squashfs" \
	-comp zstd -Xcompression-level 22 -noappend -quiet

msg "Copying kernel and initrd..."
cp "$ROOTFS_DIR"/boot/vmlinuz-* "$ISO_DIR/boot/vmlinuz"
cp "$ROOTFS_DIR"/boot/initrd.img-* "$ISO_DIR/boot/initrd.img"

cat > "$ISO_DIR/boot/grub/grub.cfg" << 'GRUB'
set default=0
set timeout=2

menuentry "DebPlus Installer" {
    linux /boot/vmlinuz boot=live quiet
    initrd /boot/initrd.img
}

menuentry "DebPlus Installer (safe graphics)" {
    linux /boot/vmlinuz boot=live quiet nomodeset
    initrd /boot/initrd.img
}
GRUB

msg "Building ISO..."
grub-mkrescue -o "$ISO_OUTPUT" "$ISO_DIR" \
	-- -volid "$ISO_LABEL" \
	-boot_image any mbr_force_bootable=on

rm -rf "$WORK_DIR"
echo "Built: $ISO_OUTPUT"
