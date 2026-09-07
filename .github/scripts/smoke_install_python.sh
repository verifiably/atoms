#!/usr/bin/env bash
# Install the wheel and the sdist independently into scratch environments and import the
# package from each. Installing the sdist is the half that proves it rebuilds; installing
# the wheel is the half that proves what consumers actually get.
set -euo pipefail

DIST="$1"
scratch=""
cleanup() {
  if [[ -n "$scratch" ]]; then
    rm -rf "$scratch"
  fi
}
trap cleanup EXIT

for artifact in "$DIST"/verifiably_atoms-*-py3-none-any.whl "$DIST"/verifiably_atoms-*.tar.gz; do
  scratch="$(mktemp -d)"
  uv venv "$scratch/venv" >/dev/null
  uv pip install --python "$scratch/venv/bin/python" "$artifact" >/dev/null
  "$scratch/venv/bin/python" - <<'EOF'
import atoms.core
import atoms.fs

assert atoms.core.__name__ == "atoms.core"
assert getattr(atoms, "__file__", None) is None, "atoms is not a namespace package"
EOF
  echo "smoke ok: $(basename "$artifact")"
  rm -rf "$scratch"
  scratch=""
done
echo "python install smoke ok"
