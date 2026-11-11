#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
PYINSTALLER="$ROOT/.venv/bin/pyinstaller"
OUT=${1:-"$ROOT/build/linux-dist"}
WORK=${2:-"$ROOT/build/pyinstaller"}
mkdir -p "$WORK"
"$PYINSTALLER" --noconfirm --clean --onedir --name ProductBlinder-v0.6 \
 --paths src --distpath "$OUT" --workpath "$WORK/work" --specpath "$WORK" \
 --collect-data product_blinder --copy-metadata httpx --copy-metadata pydantic \
 --copy-metadata jsonschema --copy-metadata product-blinder \
 --hidden-import matplotlib.backends.backend_qtagg \
 --exclude-module streamlit --exclude-module pandas --exclude-module altair \
 --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 \
 --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtWebEngineCore \
 --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtPdf \
 --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick \
 --exclude-module tkinter --exclude-module scipy --exclude-module IPython \
 src/product_blinder/native_entry.py
cp README.md LICENSE ATTRIBUTION.md "$OUT/ProductBlinder-v0.6/"
