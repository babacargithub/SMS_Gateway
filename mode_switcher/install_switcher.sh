#!/bin/bash
# One-time install of the dongle mode-switch helper. Run it yourself:  sudo ./mode_switcher/install_switcher.sh
#
# What it sets up (read it before running):
#  1. /usr/local/libexec/sms-gateway/  root-owned copy of the helper + its own Python environment (built from Apple's
#     /usr/bin/python3). Nothing a normal user can edit is ever run as root.
#  2. /etc/sudoers.d/sms-gateway-mode-switcher  ONE passwordless rule for YOUR user, for exactly this command:
#         <python> <helper> --switch-now
#     The command takes no arguments and only switches dongles that are in CD-ROM mode.
# The SMS Gateway app calls that command itself when it sees a dongle in CD-ROM mode. It must be the app (started from your
# terminal, inside your login session) and not a background daemon or agent: macOS blocks those from opening the dongle's
# storage interface, even as root. Any earlier LaunchDaemon / LaunchAgent version is removed.
set -euo pipefail

LABEL="sn.globalsoft.smsgateway.modeswitcher"
SOURCE_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIRECTORY="/usr/local/libexec/sms-gateway"
HELPER_PYTHON="$INSTALL_DIRECTORY/venv/bin/python"
HELPER_SCRIPT="$INSTALL_DIRECTORY/dongle_mode_switcher.py"
SUDOERS_DESTINATION="/etc/sudoers.d/sms-gateway-mode-switcher"
OLD_AGENT_PLIST="/Library/LaunchAgents/${LABEL}.plist"
OLD_DAEMON_PLIST="/Library/LaunchDaemons/${LABEL}.plist"
SYSTEM_PYTHON="/usr/bin/python3"  # root-owned; a Homebrew or Anaconda Python is user-writable

if [ "$(id -u)" -ne 0 ]; then
    echo "Lancez ce script avec sudo : sudo $0" >&2
    exit 1
fi
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
    echo "Lancez-le depuis votre session avec sudo (pas depuis un shell root), pour savoir quel utilisateur autoriser." >&2
    exit 1
fi
TARGET_USER_ID="$(id -u "$TARGET_USER")"
if [ ! -x "$SYSTEM_PYTHON" ]; then
    echo "$SYSTEM_PYTHON introuvable (installez les outils en ligne de commande : xcode-select --install)" >&2
    exit 1
fi

# --- stop and remove any earlier (daemon / agent) version
launchctl bootout "system/${LABEL}" 2>/dev/null || true
rm -f "$OLD_DAEMON_PLIST" "$OLD_AGENT_PLIST"
launchctl bootout "gui/${TARGET_USER_ID}/${LABEL}" 2>/dev/null || true

# --- 1. root-owned helper
mkdir -p "$INSTALL_DIRECTORY"
chown root:wheel "$INSTALL_DIRECTORY"
chmod 755 "$INSTALL_DIRECTORY"
if [ ! -x "$HELPER_PYTHON" ]; then
    "$SYSTEM_PYTHON" -m venv "$INSTALL_DIRECTORY/venv"
fi
"$HELPER_PYTHON" -m pip install --quiet --disable-pip-version-check --upgrade pyusb
install -o root -g wheel -m 755 "$SOURCE_DIRECTORY/dongle_mode_switcher.py" "$HELPER_SCRIPT"

# --- 2. the single sudo rule (validated before it is installed: a broken sudoers file would lock sudo)
TEMPORARY_SUDOERS="$(mktemp)"
echo "$TARGET_USER ALL=(root) NOPASSWD: $HELPER_PYTHON $HELPER_SCRIPT --switch-now" > "$TEMPORARY_SUDOERS"
if ! visudo -cf "$TEMPORARY_SUDOERS" >/dev/null; then
    rm -f "$TEMPORARY_SUDOERS"
    echo "Règle sudoers invalide : rien n'a été installé pour sudo." >&2
    exit 1
fi
install -o root -g wheel -m 440 "$TEMPORARY_SUDOERS" "$SUDOERS_DESTINATION"
rm -f "$TEMPORARY_SUDOERS"

echo "Installé. Lancez l'application depuis votre terminal : elle basculera seule le dongle en mode modem."
echo "Test manuel : sudo -n $HELPER_PYTHON $HELPER_SCRIPT --switch-now"
