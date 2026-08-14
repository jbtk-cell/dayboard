#!/usr/bin/env bash
#
# Turn a Raspberry Pi with a monitor into the screen itself: Chromium, full
# screen, no chrome, no sleep, back again after a power cut.
#
#   ./deploy/kiosk.sh                    point it at the dayboard on this machine
#   ./deploy/kiosk.sh http://pi.local:8080
#
# Not needed for a tablet, which is the cheaper way to do this. There you open
# the URL, set the screen to never sleep, and add it to the home screen.
#
# This is the part most likely to need adjusting: Raspberry Pi OS has changed
# desktop session three times in as many releases, and the autostart mechanism
# moved each time. If the browser does not come up after a reboot, that is
# where to look, and nothing about dayboard itself is wrong.

set -euo pipefail

URL="${1:-http://127.0.0.1:8080}"
AUTOSTART="$HOME/.config/autostart"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as the desktop user, not with sudo: the browser belongs to" >&2
  echo "whoever is logged in at the screen." >&2
  exit 1
fi

BROWSER=""
for candidate in chromium-browser chromium google-chrome firefox; do
  if command -v "$candidate" >/dev/null 2>&1; then BROWSER="$candidate"; break; fi
done

if [ -z "$BROWSER" ]; then
  echo "No browser found. On Raspberry Pi OS:" >&2
  echo "    sudo apt install -y chromium-browser" >&2
  exit 1
fi

case "$BROWSER" in
  firefox) FLAGS="--kiosk" ;;
  *)       FLAGS="--kiosk --noerrdialogs --disable-infobars --incognito
--disable-session-crashed-bubble --check-for-update-interval=31536000" ;;
esac

mkdir -p "$AUTOSTART"
cat > "$AUTOSTART/dayboard-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=dayboard
Comment=The screen
Exec=$BROWSER $(echo $FLAGS | tr '\n' ' ')$URL
X-GNOME-Autostart-enabled=true
EOF

# Stop the screen going black. A memory aid that has to be woken up is not one.
if command -v xset >/dev/null 2>&1; then
  cat > "$AUTOSTART/dayboard-nosleep.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=dayboard: keep the screen on
Exec=sh -c "xset s off; xset -dpms; xset s noblank"
X-GNOME-Autostart-enabled=true
EOF
fi

printf '\n%s\n' "Kiosk set up for $URL using $BROWSER."
echo "It starts with the desktop. To try it now without rebooting:"
echo
echo "    $BROWSER $(echo $FLAGS | tr '\n' ' ')$URL"
echo
echo "To undo:  rm $AUTOSTART/dayboard-*.desktop"
