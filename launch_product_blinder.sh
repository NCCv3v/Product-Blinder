#!/bin/sh
# Native Qt desktop program. No browser or local server.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$ROOT/.venv/bin/product-blinder-desktop" "$@"
