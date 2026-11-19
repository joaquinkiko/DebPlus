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
REPO_DIR="$ISO_DIR/install/repo"
PAYLOAD_DIR="$ROOTFS_DIR/opt/debplus"
ISO_OUTPUT="$SCRIPT_DIR/${PROFILE_SLUG}-${SUITE}-$(date +%Y%m%d).iso"
KEYRING=/usr/share/keyrings/debian-archive-keyring.gpg

die() { echo "ERROR: $*" >&2; exit 1; }
msg() { echo ":: $*"; }

# ── Mounts ─────────────────────────────────────────────────────────────

cleanup_mounts() {
	local root_dir mount_point
	for root_dir in "$ROOTFS_DIR" "$TARGET_DIR"; do
		for mount_point in "$root_dir/dev/pts" "$root_dir/dev" "$root_dir/proc" "$root_dir/sys"; do
			umount -lf "$mount_point" 2>/dev/null || true
		done
	done
}

mount_chroot_fs() {
	local root_dir="$1"
	mount --bind /dev "$root_dir/dev"
	mount --bind /dev/pts "$root_dir/dev/pts"
	mount -t proc proc "$root_dir/proc"
	mount -t sysfs sys "$root_dir/sys"
}

assert_no_active_mounts() {
	cleanup_mounts
	if findmnt -rno TARGET | grep -qF "$WORK_DIR/"; then
		die "Mounts still active under $WORK_DIR, unmount them before rebuilding"
	fi
}

# ── Preflight ──────────────────────────────────────────────────────────

preflight() {
	local -a problems=()
	local command_name variable_name relative_path directory free_kilobytes

	[[ $EUID -eq 0 ]] || die "Must be run as root"

	# command -> package that provides it
	local -A command_packages=(
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
	for command_name in "${!command_packages[@]}"; do
		command -v "$command_name" >/dev/null 2>&1 ||
			problems+=("missing command '$command_name' (apt install ${command_packages[$command_name]})")
	done

	[ -f "$KEYRING" ] || problems+=("missing $KEYRING (apt install debian-archive-keyring)")
	[ -d /usr/lib/grub/i386-pc ] || problems+=("missing GRUB BIOS modules (apt install grub-pc-bin)")
	[ -d /usr/lib/grub/x86_64-efi ] || problems+=("missing GRUB UEFI modules (apt install grub-efi-amd64-bin)")

	for variable_name in PROFILE_SLUG SUITE MIRROR ISO_LABEL; do
		[ -n "${!variable_name:-}" ] || problems+=("profile.env does not set $variable_name")
	done

	for relative_path in packages/{live,target,boot,optional,headers,dkms,mesa}.list \
		options/extras.list profile.env; do
		[ -f "$SCRIPT_DIR/$relative_path" ] || problems+=("missing file $relative_path")
	done
	for directory in installer options/custom options/wm; do
		[ -d "$SCRIPT_DIR/$directory" ] || problems+=("missing directory $directory")
	done
	compgen -G "$SCRIPT_DIR/options/*.list" >/dev/null || problems+=("no options/*.list files found")
	[ -f /etc/resolv.conf ] || problems+=("host has no /etc/resolv.conf (needed for chroot networking)")

	# Two rootfs trees, squashfs images and ISO: ~10 GiB to be safe
	free_kilobytes="$(df --output=avail -k "$SCRIPT_DIR" | tail -n1 | tr -d ' ')"
	(( free_kilobytes >= 10 * 1024 * 1024 )) || problems+=("less than 10 GiB free in $SCRIPT_DIR")

	if (( ${#problems[@]} )); then
		echo "ERROR: preflight failed:" >&2
		printf '  - %s\n' "${problems[@]}" >&2
		exit 1
	fi
}

# ── Package list helpers ───────────────────────────────────────────────

pkg_list() { grep -hEv '^[[:space:]]*(#|$)' "$@" || true; }

optional_names() {
	awk -F'|' '!/^[[:space:]]*#/ && NF { gsub(/[ \t]/, "", $1); if ($1) print $1 }' "$1"
}

baked_extras() {
	awk -F'|' '/^[[:space:]]*#/ || NF < 6 { next }
		{ gsub(/^[ \t]+|[ \t]+$/, "", $6); if ($6 == "bake") print $3 }' "$1"
}

boot_set() {
	awk -F'|' -v mode="$1" '/^[[:space:]]*#/ || NF < 2 { next }
		{ gsub(/^[ \t]+|[ \t]+$/, "", $1); if ($1 == mode) print $2 }' "$SCRIPT_DIR/packages/boot.list"
}

word_list() { tr -s ' ' '\n' | sed '/^$/d' | sort -u; }

# ── Chroot helpers ─────────────────────────────────────────────────────

write_apt_sources() {
	local root_dir="$1"
	cat > "$root_dir/etc/apt/sources.list.d/debian.sources" << SOURCES
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates
Components: main contrib non-free-firmware non-free
Signed-By: $KEYRING

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $SUITE-security
Components: main contrib non-free-firmware non-free
Signed-By: $KEYRING
SOURCES
	: > "$root_dir/etc/apt/sources.list"
}

# Reads a setup script from stdin, runs it in the chroot with networking, then clears resolv.conf.
run_setup_in_chroot() {
	local root_dir="$1"
	write_apt_sources "$root_dir"
	cat > "$root_dir/tmp/setup.sh"
	chmod +x "$root_dir/tmp/setup.sh"
	rm -f "$root_dir/etc/resolv.conf"
	cp /etc/resolv.conf "$root_dir/etc/resolv.conf"
	chroot "$root_dir" /tmp/setup.sh
	: > "$root_dir/etc/resolv.conf"
	rm -f "$root_dir/tmp/setup.sh"
}

make_squashfs() {
	local source_path="$1" output_path="$2" compression_level="$3"
	mksquashfs "$source_path" "$output_path" -comp zstd -Xcompression-level "$compression_level" \
		-noappend -quiet
}

latest_file() { printf '%s\n' "$@" | sort -V | tail -n1; }

# ── Live environment ───────────────────────────────────────────────────

install_installer_payload() {
	install -d "$PAYLOAD_DIR/packages" "$PAYLOAD_DIR/options"
	install -m644 "$SCRIPT_DIR"/packages/*.list "$PAYLOAD_DIR/packages/"
	install -m644 "$SCRIPT_DIR/profile.env" "$PAYLOAD_DIR/"
	install -m644 "$SCRIPT_DIR"/options/*.list "$PAYLOAD_DIR/options/"
	cp -a "$SCRIPT_DIR/installer" "$PAYLOAD_DIR/"
	cp -a "$SCRIPT_DIR/options/custom" "$SCRIPT_DIR/options/wm" "$PAYLOAD_DIR/options/"
}

build_live_rootfs() {
	msg "Debootstrapping live environment..."
	debootstrap --arch=amd64 --variant=minbase --keyring="$KEYRING" "$SUITE" "$ROOTFS_DIR" "$MIRROR"
	mount_chroot_fs "$ROOTFS_DIR"
	pkg_list "$SCRIPT_DIR/packages/live.list" | word_list > "$ROOTFS_DIR/tmp/live.pkgs"

	run_setup_in_chroot "$ROOTFS_DIR" << 'SETUP'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends $(cat /tmp/live.pkgs)

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

	install_installer_payload
	cleanup_mounts
}

# ── Base system image and local package repo ───────────────────────────
# target.squashfs holds only target.list. Everything optional is downloaded as .debs into a
# flat apt repo on the ISO, and the installer installs just what was selected, offline if need be.
# Both live in install/, outside live/, so live-boot does not layer them.

build_base_image() {
	msg "Debootstrapping base system image..."
	debootstrap --arch=amd64 --variant=minbase --components=main,contrib,non-free-firmware \
		--keyring="$KEYRING" "$SUITE" "$TARGET_DIR" "$MIRROR"
	mount_chroot_fs "$TARGET_DIR"

	pkg_list "$SCRIPT_DIR/packages/target.list" | word_list > "$TARGET_DIR/tmp/base.pkgs"
	# BIOS and UEFI sets are downloaded separately because grub-pc conflicts with the UEFI stack
	{
		optional_names "$SCRIPT_DIR/packages/optional.list"
		pkg_list "$SCRIPT_DIR/packages/headers.list"
		baked_extras "$SCRIPT_DIR/options/extras.list"
	} | word_list > "$TARGET_DIR/tmp/common.pkgs"
	boot_set uefi | word_list > "$TARGET_DIR/tmp/uefi.pkgs"
	boot_set bios | word_list > "$TARGET_DIR/tmp/bios.pkgs"

	run_setup_in_chroot "$TARGET_DIR" << 'SETUP'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

# No services should start inside the build chroot
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d

apt-get update
apt-get install -y --no-install-recommends $(cat /tmp/base.pkgs)

# From here the apt cache must hold only what goes into the repo
apt-get clean
for package_set in common uefi bios; do
	apt-get install -y --download-only --no-install-recommends $(cat /tmp/$package_set.pkgs)
done

# Per-install identity is created by the installer
rm -f /usr/sbin/policy-rc.d
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id /etc/ssh/ssh_host_*
rm -rf /var/lib/apt/lists/* /tmp/*
SETUP
	cleanup_mounts
}

build_local_repo() {
	msg "Building local package repo..."
	mv "$TARGET_DIR"/var/cache/apt/archives/*.deb "$REPO_DIR/"
	(
		cd "$REPO_DIR"
		dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null
		[ -s Packages ] || die "dpkg-scanpackages produced no index"
		gzip -9k Packages
		# The installer runs apt with Check-Date off, so a wrong offline clock cannot reject this
		{
			echo "Origin: DebPlus"
			echo "Date: $(date -Ru)"
			echo "Label: DebPlus"
			echo "Architectures: amd64"
			echo "SHA256:"
			for index_file in Packages Packages.gz; do
				printf ' %s %s %s\n' "$(sha256sum "$index_file" | cut -d' ' -f1)" \
					"$(stat -c %s "$index_file")" "$index_file"
			done
		} > Release
	)
	msg "Repo holds $(ls "$REPO_DIR"/*.deb | wc -l) packages"
}

# ── Boot files and ISO ─────────────────────────────────────────────────

copy_boot_files() {
	msg "Copying kernel and initrd..."
	cp "$(latest_file "$ROOTFS_DIR"/boot/vmlinuz-*)" "$ISO_DIR/boot/vmlinuz"
	cp "$(latest_file "$ROOTFS_DIR"/boot/initrd.img-*)" "$ISO_DIR/boot/initrd.img"
}

menu_entry() {
	local title="$1" extra_args="${2:-}"
	printf 'menuentry "%s" {\n    linux /boot/vmlinuz boot=live%s\n    initrd /boot/initrd.img\n}\n\n' \
		"$title" "${extra_args:+ $extra_args}"
}

write_grub_config() {
	{
		echo "set default=0"
		echo "set timeout=2"
		echo
		menu_entry "$PROFILE_NAME Installer"
		menu_entry "$PROFILE_NAME Installer (safe graphics)" nomodeset
	} > "$ISO_DIR/boot/grub/grub.cfg"
}

build_iso() {
	msg "Building ISO..."
	grub-mkrescue -o "$ISO_OUTPUT" "$ISO_DIR" \
		-- -volid "$ISO_LABEL" \
		-boot_image any mbr_force_bootable=on
}

# ── Main ───────────────────────────────────────────────────────────────

main() {
	preflight
	assert_no_active_mounts
	rm -rf "$WORK_DIR"
	mkdir -p "$ROOTFS_DIR" "$TARGET_DIR" "$ISO_DIR/boot/grub" "$ISO_DIR/live" "$REPO_DIR"

	build_live_rootfs
	build_base_image
	build_local_repo

	msg "Creating base system and live squashfs in parallel (this takes a while)..."
	make_squashfs "$TARGET_DIR" "$ISO_DIR/install/target.squashfs" 19 &
	target_pid=$!
	make_squashfs "$ROOTFS_DIR" "$ISO_DIR/live/filesystem.squashfs" 19 &
	live_pid=$!

	target_status=0
	live_status=0
	wait "$target_pid" || target_status=$?
	wait "$live_pid" || live_status=$?
	(( target_status == 0 && live_status == 0 )) ||
		die "squashfs build failed (target: $target_status, live: $live_status)"
	rm -rf "$TARGET_DIR"

	copy_boot_files
	write_grub_config
	build_iso

	assert_no_active_mounts
	rm -rf "$WORK_DIR"
	echo "Built: $ISO_OUTPUT"
}

trap cleanup_mounts EXIT
main "$@"