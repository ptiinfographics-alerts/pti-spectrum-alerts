#!/bin/zsh
# One real check from this Mac, using the tracker's own code: signs in to
# Spectrum, lists today's alerts and shows what it WOULD email. Sends
# nothing and saves nothing.
#
# The password is typed blind: not shown, not kept in your shell history.

PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1

printf "Spectrum username: "
read -r SPECTRUM_USER
printf "Spectrum password (nothing appears as you type): "
read -rs SPECTRUM_PASSWORD
echo
export SPECTRUM_USER SPECTRUM_PASSWORD

# A throwaway state file, so this preview never touches the real one.
TMP_STATE="$(mktemp -d)/state.json"
"$PROJECT/.venv/bin/python" - "$TMP_STATE" <<'EOF'
import pathlib, sys
from tracker import run
run.STATE = pathlib.Path(sys.argv[1])
sys.exit(run.run(loop_minutes=0, dry_run=True))
EOF
