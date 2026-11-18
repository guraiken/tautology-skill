#!/usr/bin/env sh
# Runs a hook script with the first Python 3.10+ found. TAUTOLOGY_PYTHON overrides the search.
# No Python: the hook does nothing (exit 0), so it never blocks an edit, a prompt or an answer.
check='import sys; sys.exit(sys.version_info < (3, 10))'
if [ -n "$TAUTOLOGY_PYTHON" ] && "$TAUTOLOGY_PYTHON" -c "$check" >/dev/null 2>&1; then
  exec "$TAUTOLOGY_PYTHON" "$@"
fi
for candidate in python3 python; do
  if "$candidate" -c "$check" >/dev/null 2>&1; then
    exec "$candidate" "$@"
  fi
done
if py -3 -c "$check" >/dev/null 2>&1; then
  exec py -3 "$@"
fi
echo "tautology plugin: Python 3.10+ not found; set TAUTOLOGY_PYTHON" >&2
exit 0
