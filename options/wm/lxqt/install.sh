#!/bin/bash
# Minimal LXQt for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends lxqt-core sddm
apt-get clean