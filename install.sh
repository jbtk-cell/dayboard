#!/usr/bin/env bash
#
# Put dayboard on a machine and keep it there across reboots.
#
#   sudo ./install.sh                 the screen and the console
#   sudo ./install.sh --bridge        and the Zigbee bridge
#   sudo ./install.sh --uninstall     take it off, keeping the day
#
# Installs to /opt/dayboard rather than running from wherever this was cloned,
# because the service runs with ProtectHome=yes and would not be able to read
# its own code out of somebody's home directory. The day is kept in
# /var/lib/dayboard, which systemd creates owner-only.

set -euo pipefail

PREFIX=/opt/dayboard
STATE=/var/lib/dayboard
SERVICE_USER=dayboard
REPO=jbtk-cell/dayboard
PORT=8080
HOST=0.0.0.0
WITH_BRIDGE=0
UNINSTALL=0

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --bridge)     WITH_BRIDGE=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    --port)       PORT="$2"; shift ;;
    --host)       HOST="$2"; shift ;;
    --user)       SERVICE_USER="$2"; shift ;;
    -h|--help)    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n%s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "This needs root, to install a service that survives a reboot:" >&2
  echo "    sudo $0 $*" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "No systemd here, so there is nothing for this script to install into." >&2
  echo "To run it in the foreground instead:" >&2
  echo "    uv run python -m dayboard.cli serve" >&2
  exit 1
fi

# ---- taking it off -------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
  for unit in dayboard-bridge dayboard; do
    if systemctl list-unit-files | grep -q "^${unit}.service"; then
      systemctl disable --now "${unit}.service" >/dev/null 2>&1 || true
      rm -f "/etc/systemd/system/${unit}.service"
    fi
  done
  systemctl daemon-reload
  rm -rf "$PREFIX"
  say "Removed. The day is still in $STATE; delete it yourself if you mean to."
  exit 0
fi

# ---- the user it runs as -------------------------------------------------

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  say "Created the system user $SERVICE_USER."
fi

# ---- the code ------------------------------------------------------------

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  echo "python3 is not installed:  sudo apt install -y python3 python3-venv" >&2
  exit 1
fi

say "Installing to $PREFIX"
install -d -m 0755 "$PREFIX"
rm -rf "$PREFIX/dayboard"
cp -r "$SOURCE/dayboard" "$PREFIX/dayboard"
cp "$SOURCE/pyproject.toml" "$PREFIX/"
cp "$SOURCE/README.md" "$PREFIX/" 2>/dev/null || true
cp "$SOURCE/LICENSE" "$PREFIX/" 2>/dev/null || true

if [ ! -x "$PREFIX/venv/bin/python" ]; then
  "$PYTHON" -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip wheel

if [ "$WITH_BRIDGE" -eq 1 ]; then
  "$PREFIX/venv/bin/pip" install --quiet "$PREFIX[bridge]"
else
  "$PREFIX/venv/bin/pip" install --quiet "$PREFIX"
fi

# The code is root's; the service only ever reads it.
chown -R root:root "$PREFIX"

# ---- the services --------------------------------------------------------

render() {
  sed -e "s|__PREFIX__|$PREFIX|g" \
      -e "s|__USER__|$SERVICE_USER|g" \
      -e "s|__PORT__|$PORT|g" \
      -e "s|__HOST__|$HOST|g" \
      -e "s|__REPO__|$REPO|g" \
      "$SOURCE/deploy/$1" > "/etc/systemd/system/$1"
}

render dayboard.service
[ "$WITH_BRIDGE" -eq 1 ] && render dayboard-bridge.service

systemctl daemon-reload
systemctl enable --now dayboard.service

# The token is made on first run, so wait for the service to write it before
# printing the link that the whole setup depends on.
TOKEN=""
for _ in $(seq 1 25); do
  if [ -s "$STATE/token" ]; then TOKEN="$(cat "$STATE/token")"; break; fi
  sleep 0.2
done

if [ "$WITH_BRIDGE" -eq 1 ]; then
  if [ ! -f "$STATE/bridge.json" ]; then
    runuser -u "$SERVICE_USER" -- "$PREFIX/venv/bin/dayboard" bridge --data "$STATE" >/dev/null 2>&1 || true
    say "Wrote a starter bridge config to $STATE/bridge.json"
    echo "Its device names are placeholders. Set them to the friendly names"
    echo "zigbee2mqtt shows, then:  sudo systemctl restart dayboard-bridge"
  fi
  systemctl enable --now dayboard-bridge.service
fi

# ---- where to go now -----------------------------------------------------

ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$ADDRESS" ] || ADDRESS="$(hostname)"

say "dayboard is running, and will come back after a reboot."
echo
echo "  her screen   http://$ADDRESS:$PORT"
if [ -n "$TOKEN" ]; then
  echo "  the console  http://$ADDRESS:$PORT/console?token=$TOKEN"
else
  echo "  the console  http://$ADDRESS:$PORT/console?token=\$(sudo cat $STATE/token)"
fi
echo
echo "Point the tablet at the first link and leave it there."
echo "Keep the second one: it is the only way back into the console."
echo
echo "  systemctl status dayboard        is it running"
echo "  journalctl -u dayboard -f        what it is doing"
if [ "$WITH_BRIDGE" -eq 1 ]; then
  echo "  journalctl -u dayboard-bridge -f every sensor as it reports"
fi
