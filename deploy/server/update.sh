#!/bin/sh
# Updates asked from the console (/admin › Aggiornamenti). The backoffice
# has no Docker socket and no root: it only leaves updates/request.json with
# the version to install. This script, started by systemd as soon as the
# request is written (eullm-update.path) and every minute by root's cron as
# a fallback, takes the request, backs up (backup.sh), pulls the images of
# that version, writes it in .env, restarts and checks that the dashboard
# answers. How it went, with a percentage, goes to updates/status.json and
# updates/update.log, which the console shows. After an update it replaces
# itself with the script of the installed version. Guide: README.md,
# "Updating from the console".
#
#   ./update.sh install   once, as root: the shared folder, systemd and cron
#   ./update.sh trigger   as root: only the systemd units (run by itself)
#   ./update.sh           what systemd and cron run: nothing to do without a request
set -eu
cd "$(dirname "$0")"
here=$PWD
dir=$here/updates
EDITOR_UID=999   # the user of the editor image (editor-mode/Dockerfile)
REPO=https://raw.githubusercontent.com/eullm/eullm-agent

# The request starts the update at once: a systemd path unit watches the
# folder. Without systemd the cron job alone takes it within a minute.
trigger() {
  [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1 || return 0
  cat > /etc/systemd/system/eullm-update.service <<EOF
[Unit]
Description=Redazia update asked from the console
[Service]
Type=oneshot
ExecStart=$here/update.sh
TimeoutStartSec=0
EOF
  cat > /etc/systemd/system/eullm-update.path <<EOF
[Unit]
Description=Redazia: start an update as soon as the console asks
[Path]
PathChanged=$dir
Unit=eullm-update.service
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now eullm-update.path >/dev/null 2>&1 && echo "systemd: eullm-update.path enabled"
}

if [ "${1:-}" = "trigger" ]; then
  [ "$(id -u)" = 0 ] || { echo "run it as root: sudo $0 trigger"; exit 1; }
  trigger
  exit 0
fi

if [ "${1:-}" = "install" ]; then
  [ "$(id -u)" = 0 ] || { echo "run it as root: sudo $0 install"; exit 1; }
  install -d -m 0770 -o "$EDITOR_UID" -g "$EDITOR_UID" "$dir"
  chmod +x "$here/update.sh" "$here/backup.sh"
  # Installations made before this script: their compose.yaml has no shared
  # folder, so it is added by an override file that compose reads by itself.
  if ! grep -q EDITOR_UPDATES_DIR compose.yaml && [ ! -e compose.override.yaml ]; then
    cat > compose.override.yaml <<'EOF'
# Written by update.sh install: the folder of the updates from the console.
services:
  editor-web:
    environment: {EDITOR_UPDATES_DIR: /var/lib/editor/updates}
    volumes: ["./updates:/var/lib/editor/updates"]
  editor-worker:
    environment: {EDITOR_UPDATES_DIR: /var/lib/editor/updates}
    volumes: ["./updates:/var/lib/editor/updates"]
EOF
    echo "written: compose.override.yaml"
  fi
  echo "* * * * * root $here/update.sh >/dev/null 2>&1" > /etc/cron.d/eullm-update
  chmod 644 /etc/cron.d/eullm-update
  trigger
  docker compose up -d
  echo "done: /admin › Aggiornamenti now updates this server"
  exit 0
fi

req=$dir/request.json
[ -f "$req" ] || exit 0
exec 9>"$dir/.lock"
flock -n 9 || exit 0

version=$(sed -n 's/.*"version": *"\([0-9]\{1,4\}\.[0-9]\{1,4\}\.[0-9]\{1,4\}\)".*/\1/p' "$req" | head -n 1)
rm -f "$req"
old=$(sed -n 's/^EULLM_VERSION=//p' .env | tail -n 1)
log=$dir/update.log
step=start

status() {  # state [percent]
  printf '{"state": "%s", "step": "%s", "percent": %s, "version": "%s", "from": "%s", "at": "%s"}\n' \
    "$1" "$step" "${2:-0}" "$version" "$old" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$dir/status.json.part"
  chmod 644 "$dir/status.json.part"
  mv "$dir/status.json.part" "$dir/status.json"
}
fail() {
  echo "failed during: $step" >> "$log"
  status failed
  exit 1
}

# The download of the images, with its percentage: compose tells the
# progress of each layer as JSON; the average of the layers goes from 10 to
# 80 on the console's bar. Other lines go to the log. A compose that does
# not know --progress json pulls the plain way.
pull() {
  rm -f "$dir/.pull-rc"
  { EULLM_VERSION=$version docker compose --progress json pull 2>&1; echo "RC $?"; } | awk '
    function field(k,   r) { if (match($0, "\"" k "\":\"[^\"]*\"")) { r = substr($0, RSTART, RLENGTH); sub("^\"" k "\":\"", "", r); sub("\"$", "", r); return r } return "" }
    /^RC [0-9]+$/ { print; next }
    /^\{/ {
      parent = field("parent_id"); text = field("text")
      if (field("status") == "Error") { print "LOG " $0; next }
      if (parent == "") next
      key = parent "/" field("id")
      if (!(key in pct)) { pct[key] = 0; n++ }
      if (text ~ /complete|Already exists/) pct[key] = 100
      else if (text == "Downloading" && match($0, /"percent":[0-9]+/)) pct[key] = substr($0, RSTART + 10, RLENGTH - 10) + 0
      sum = 0; for (k in pct) sum += pct[k]
      p = int(10 + 70 * sum / (n * 100))
      if (p > last) { print "PCT " p; last = p; fflush() }
      next
    }
    { print "LOG " $0 }' | while read -r kind rest; do
      case $kind in
        PCT) status running "$rest" ;;
        RC) echo "$rest" > "$dir/.pull-rc" ;;
        *) echo "$rest" >> "$log" ;;
      esac
    done
  [ "$(cat "$dir/.pull-rc" 2>/dev/null)" = 0 ] && return 0
  grep -q -- '--progress' "$log" && EULLM_VERSION=$version docker compose pull >> "$log" 2>&1
}

# The script of the version just installed takes this one's place (a new
# file: the shell running this one keeps reading the old), and with it the
# way it is started.
renew() {
  curl -fsSL "$REPO/v$version/deploy/server/update.sh" -o "$dir/update.sh.new" 2>> "$log" || return 0
  if [ "$(head -n 1 "$dir/update.sh.new")" = "#!/bin/sh" ] && sh -n "$dir/update.sh.new" \
     && ! cmp -s "$dir/update.sh.new" "$here/update.sh"; then
    chmod 755 "$dir/update.sh.new"
    mv "$dir/update.sh.new" "$here/update.sh"
    echo "update.sh renewed from v$version" >> "$log"
    "$here/update.sh" trigger >> "$log" 2>&1 || true
  fi
  rm -f "$dir/update.sh.new"
}

: > "$log"
chmod 644 "$log"
if [ -z "$version" ]; then step=request; echo "no valid version in the request" >> "$log"; fail; fi
echo "$(date -u) update from ${old:-latest} to $version" >> "$log"

step=backup; status running 2
./backup.sh >> "$log" 2>&1 || fail

# The images of a new release appear a few minutes after its tag: try for 10 minutes.
step=pull; status running 10
tries=0
until pull; do
  tries=$((tries + 1))
  [ "$tries" -lt 20 ] || fail
  sleep 30
done

step=restart; status running 80
if grep -q '^EULLM_VERSION=' .env; then
  sed -i "s/^EULLM_VERSION=.*/EULLM_VERSION=$version/" .env
else
  echo "EULLM_VERSION=$version" >> .env
fi
docker compose up -d >> "$log" 2>&1 || fail

step=check; status running 90
port=$(sed -n 's/^EDITOR_PORT=//p' .env | tail -n 1)
tries=0
until curl -fsS "http://127.0.0.1:${port:-8090}/api/health" >> "$log" 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -lt 36 ] || fail
  sleep 5
done
echo >> "$log"
docker compose ps >> "$log" 2>&1 || true
step=done; status done 100
renew
