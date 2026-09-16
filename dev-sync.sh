#!/bin/bash
# Development only: copy this working tree into the live plugin folder, validate it, and ask the shell
# to rescan its plugins. The plugin itself never runs this.
#
#   ./dev-sync.sh            copy, validate, rescan
#   RESTART=1 ./dev-sync.sh  copy, validate, restart the shell (a mounted bar widget keeps its old
#                            instance across a rescan, so BarWidget.qml edits need a restart)
#
# It copies rather than symlinks, because `omarchy plugin validate` refuses a plugin folder holding a
# symlink. Tests, lint stand-ins and development files stay behind. The plugin id is read from
# manifest.json, so the destination always matches the manifest.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID="$(/usr/bin/python3 -I -S -B -c 'import json, sys; print(json.load(open(sys.argv[1]))["id"])' "$SRC/manifest.json")"
if ! [[ "$ID" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || [[ "$ID" == *..* ]]; then
  echo "dev-sync: manifest id '$ID' is not a plain plugin id" >&2
  exit 2
fi
DEST="$HOME/.config/omarchy/plugins/$ID"

mkdir -p "$DEST"
rsync -a --delete \
  --exclude '.git/' --exclude 'tests/' --exclude 'lint/' --exclude 'docs/' \
  --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.qmllint.ini' --exclude '.gitignore' --exclude 'dev-sync.sh' \
  "$SRC/" "$DEST/"
chmod 0755 "$DEST/bin/ap4a"

omarchy plugin validate "$DEST"

if [ "${RESTART:-0}" = 1 ]; then
  omarchy-restart-shell >/dev/null 2>&1 &
else
  omarchy-shell shell rescanPlugins
fi
echo "dev-sync: $ID synced to $DEST"
