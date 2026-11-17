#!/usr/bin/env sh
# Runs a hook script with the first Python 3.10+ found. TAUTOLOGY_PYTHON overrides the search.
# No Python: the hook does nothing (exit 0), so it never blocks an edit, a prompt or an answer.
for candidate in "$TAUTOLOGY_PYTHON" python3 python "py -3"; do
  [ -n "$candidate" ] || continue
  if $candidate -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
    exec $candidate "$@"
  fi
done
echo "tautology plugin: Python 3.10+ not found; set TAUTOLOGY_PYTHON" >&2
exit 0
