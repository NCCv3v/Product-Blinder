# Original Product Blinder sticker identity (v0.5)

This is the desktop application's icon, not a bot avatar. A slightly tilted peach test
clipboard, lime clip, sky inspection lens/check and lavender die-cut silhouette express
testing and inspection. The small sparkle is a secondary accent, not a claim of AI autonomy.

All SVG geometry and raster assets are original project artwork, licensed under the same
MIT license as the source. No logos, religious text, watermark or reference artwork copied.
The supplied attachment paths were attempted but were absent in this executor filesystem;
the detailed written style brief supplied the visual direction. The earlier light-dashboard
references are separate and were not used as icon artwork.

## Assets
`src/product_blinder/assets/icons`: app.svg plus chatbot, dashboard, run, tests, speed,
quality, consistency, settings, import, export, evidence and alert SVGs. Each uses a 128x128
viewBox, rounded near-black outlines, irregular pastel silhouette and white die-cut edge.
App PNGs: 1024, 256, 64 and 32 pixels, with transparency.
`reports/v0.5/icon-contact-sheet.png` is a labelled asset preview, NOT a UI screenshot.

## Native integration
Qt QIcon/QSvgRenderer, no webview or emoji. QApplication and MainWindow use the app icon;
the native sidebar replaces the prior P tile. Both named navigation destinations, KPI
headings and selected import/export/run/settings/evidence/cancel actions carry real icons.
Labels and tooltips remain; color is not the sole information channel. Icons do not flash.
Existing truthful progress animation and reduced-motion controls are unchanged.

Setuptools package data bundles SVG and raster assets in the wheel. The optional desktop
entry template assumes a normal installed product-blinder-desktop command and a locally
installed app PNG. The box preview entry points to its installed launcher; it is not a
standalone binary, and no Windows/macOS icon integration is claimed.

## Reproduce / test
Install desktop dependencies, then:
`QT_QPA_PLATFORM=offscreen python scripts/generate-icons.py`
Generator uses original geometric drawing instructions and native Qt rasterization only.
Tests verify every SVG with QSvgRenderer and actual 24/32/64 rendering, transparent PNG
dimensions/alpha, window identity and labelled native action icons. Readability at tiny
sizes still merits human review. MIT notice is included in the assets ZIP.
