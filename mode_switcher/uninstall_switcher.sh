#!/bin/bash
# Removes the dongle mode-switch helper (sudo rule, root-owned files, any old daemon/agent). Run: sudo ./mode_switcher/uninstall_switcher.sh
set -euo pipefail

LABEL="sn.globalsoft.smsgateway.modeswitcher"

if [ "$(id -u)" -ne 0 ]; then
    echo "Lancez ce script avec sudo : sudo $0" >&2
    exit 1
fi
TARGET_USER="${SUDO_USER:-}"
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    launchctl bootout "gui/$(id -u "$TARGET_USER")/${LABEL}" 2>/dev/null || true
fi
launchctl bootout "system/${LABEL}" 2>/dev/null || true
rm -f "/Library/LaunchAgents/${LABEL}.plist" "/Library/LaunchDaemons/${LABEL}.plist"
rm -f /etc/sudoers.d/sms-gateway-mode-switcher
rm -rf /usr/local/libexec/sms-gateway
echo "Désinstallé."
