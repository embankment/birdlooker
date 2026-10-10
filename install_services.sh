#!/usr/bin/env bash
# Install birdlooker as systemd services, so the camera is simply
# running after a reboot instead of needing two SSH terminals.
#
#     sudo ./install_services.sh
#
# Creates two independent units:
#   birdlooker-stream   -- pi-webrtc, video on 8080
#   birdlooker-control  -- the click server and viewer page on 8081
#
# They are deliberately NOT ordered against each other. Either can
# restart without disturbing the other, and the viewer page already
# reports "no stream" on its own if pi-webrtc is down.
#
# Re-running this is safe; it overwrites the units and restarts them.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Needs root to write to /etc/systemd/system. Try:" >&2
    echo "    sudo $0" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# RUN_USER from the environment wins, then the invoking user, then a
# guess. Resolved before validation so an explicit override is actually
# the thing that gets checked.
RUN_USER="${RUN_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo pi)}}"

if ! id "$RUN_USER" >/dev/null 2>&1; then
    echo "No such user: '$RUN_USER'" >&2
    echo "The services need a real account to run as. Set one with:" >&2
    echo "    sudo RUN_USER=yourname $0" >&2
    exit 1
fi

echo "Repo: $REPO"
echo "User: $RUN_USER"
echo

# --- group checks -----------------------------------------------------
# These fail at runtime rather than install time, which makes for a
# confusing debug session later. Catch them now.
missing_groups=""
for grp in dialout video; do
    if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx "$grp"; then
        missing_groups="${missing_groups:+$missing_groups }$grp"
    fi
done
if [ -n "$missing_groups" ]; then
    echo "WARNING: $RUN_USER is not in: $missing_groups"
    echo "  dialout is needed for /dev/ttyUSB0 (the servo controller)"
    echo "  video is needed for the camera"
    echo
    echo "  Fix with:  sudo usermod -a -G ${missing_groups// /,} $RUN_USER"
    echo "  Group changes apply to new sessions, but systemd services"
    echo "  pick them up on start, so a restart of the units is enough."
    echo
fi

# --- units ------------------------------------------------------------

write_unit() {
    local name="$1"
    local desc="$2"
    local exec_start="$3"
    local extra="${4:-}"

    cat > "/etc/systemd/system/${name}.service" <<EOF
[Unit]
Description=${desc}
After=network-online.target
Wants=network-online.target

# Never give up permanently. This is an unattended garden camera, and a
# unit sitting quietly in failed state is worse than one still trying.
StartLimitIntervalSec=0

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO}
ExecStart=${exec_start}
${extra}
# Restart covers the ordinary failures: the USB serial adapter not yet
# enumerated at boot, the camera busy, a transient crash. Five seconds
# is long enough to avoid a hot loop and short enough not to notice.
Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    echo "  wrote /etc/systemd/system/${name}.service"
}

echo "Writing units:"
write_unit birdlooker-stream \
    "BirdLooker camera stream (pi-webrtc)" \
    "${REPO}/start_stream.sh"

write_unit birdlooker-control \
    "BirdLooker click-to-look control server" \
    "/usr/bin/env python3 ${REPO}/control_server.py"

echo
systemctl daemon-reload

echo "Enabling and starting:"
for unit in birdlooker-stream birdlooker-control; do
    systemctl enable "$unit" >/dev/null 2>&1
    systemctl restart "$unit"
    printf "  %-20s " "$unit"
    if systemctl is-active --quiet "$unit"; then
        echo "running"
    else
        echo "FAILED -- journalctl -u $unit -n 30"
    fi
done

IP=$(hostname -I | awk '{print $1}')
cat <<EOF

Installed. The camera now starts on boot.

  Viewer:   http://${IP}:8081/

Useful:
  systemctl status birdlooker-stream birdlooker-control
  journalctl -u birdlooker-control -f        follow the log
  sudo systemctl restart birdlooker-control  after editing config.py
  sudo systemctl stop birdlooker-stream      to run it by hand instead

Note: after 'git pull', restart the unit whose code changed. Editing
config.py needs a control restart; stream settings need a stream restart.
EOF
