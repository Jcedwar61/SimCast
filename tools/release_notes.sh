#!/usr/bin/env bash
# The release notes for SimCast VER from README.md, on stdout:
#   tools/release_notes.sh 1.20          the "What's new in 1.20" section
#   tools/release_notes.sh 1.20 1.18     every "What's new" from 1.20 down to, not including, 1.18 (a version that
#                                        was never released on its own is in the next release's notes), each
#                                        under its heading when there's more than one
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VER="${1:?usage: release_notes.sh VER [PREVIOUS]}"; PREV="${2:-}"
H="### What's new in"
STOP=""; [ -n "$PREV" ] && STOP="$H $PREV"
notes="$(awk -v top="$H $VER" -v stop="$STOP" '
  $0 == top { on = 1; if (stop == "") next }
  !on { next }
  stop != "" && $0 == stop { exit }
  /^### / && $0 != top && (stop == "" || $0 !~ /^### What.s new in /) { exit }
  { print }
' "$REPO/README.md" | tr -d '\r')"
[ -n "$notes" ] || { echo "README.md has no \"$H $VER\" section" >&2; exit 1; }
# just the one version: no heading, as before
if [ "$(grep -c "^$H " <<<"$notes")" -le 1 ]; then notes="$(sed "1{/^$H /d}" <<<"$notes")"; fi
printf '%s\n' "$notes"
