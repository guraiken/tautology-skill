#!/usr/bin/env sh
# Runs a hook script with the first Python 3.10+ found. TAUTOLOGY_PYTHON overrides the search.
# No Python: the hook does nothing (exit 0), so it never blocks an edit, a prompt or an answer.
# The name found is cached: probing spawns each candidate, and on Windows `python3` is a slow Store stub.
check='import sys; sys.exit(sys.version_info < (3, 10))'
if [ -n "$TAUTOLOGY_PYTHON" ]; then
  if [ "$TAUTOLOGY_PYTHON" = "py -3" ]; then
    if command -v py >/dev/null 2>&1 && py -3 -c "$check" >/dev/null 2>&1; then
      exec py -3 "$@"
    fi
  elif "$TAUTOLOGY_PYTHON" -c "$check" >/dev/null 2>&1; then
    exec "$TAUTOLOGY_PYTHON" "$@"
  fi
  echo "tautology plugin: TAUTOLOGY_PYTHON is not Python 3.10+; probing PATH" >&2
fi
cache="${XDG_CACHE_HOME:-$HOME/.cache}/tautology/python"
if [ -r "$cache" ]; then
  read -r cached < "$cache"
  case "$cached" in
    python3|python)
      if command -v "$cached" >/dev/null 2>&1; then
        exec "$cached" "$@"
      fi
      rm -f "$cache" 2>/dev/null
      ;;
    py)
      if command -v py >/dev/null 2>&1; then
        exec py -3 "$@"
      fi
      rm -f "$cache" 2>/dev/null
      ;;
    *) rm -f "$cache" 2>/dev/null ;;
  esac
fi
remember() { mkdir -p "${cache%/*}" 2>/dev/null && printf '%s\n' "$1" > "$cache" 2>/dev/null; }
for candidate in python3 python; do
  if "$candidate" -c "$check" >/dev/null 2>&1; then
    remember "$candidate"
    exec "$candidate" "$@"
  fi
done
if py -3 -c "$check" >/dev/null 2>&1; then
  remember py
  exec py -3 "$@"
fi
echo "tautology plugin: Python 3.10+ not found; set TAUTOLOGY_PYTHON" >&2
exit 0
