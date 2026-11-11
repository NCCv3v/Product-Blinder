#!/usr/bin/env python3
"""Reproducible original MIT sticker icons; no external artwork or generation service."""

from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QFont
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QRectF, Qt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/product_blinder/assets/icons"
OUT.mkdir(parents=True, exist_ok=True)
(ROOT / "reports/v0.5").mkdir(parents=True, exist_ok=True)
INK = "#24252B"
COLORS = ["#DDF29B", "#A8D9F4", "#C7B6EE", "#FFD0AE", "#FFE58A", "#F4B9D4"]
SHAPES = {
    "chatbot": '<path fill="#FFFFFF" d="M29 31Q28 25 39 25H88Q99 25 99 37V75Q99 86 86 86H59L39 102V85Q27 85 27 74Z"/><path d="M43 47H81M43 62H70"/>',
    "dashboard": '<path fill="#FFFFFF" d="M27 27H101V99H27Z"/><path fill="#A8D9F4" d="M39 76H49V88H39Z"/><path fill="#C7B6EE" d="M59 58H69V88H59Z"/><path fill="#FFD0AE" d="M79 40H89V88H79Z"/><path d="M38 40L48 35"/>',
    "run": '<path fill="#FFFFFF" d="M47 29Q42 26 42 35V96Q42 104 50 99L97 69Q104 64 95 59Z"/>',
    "tests": '<path fill="#FFFFFF" d="M35 24H96V103H30V30Z"/><path fill="#FFE58A" d="M49 16H77V34H49Z"/><path d="M42 52L47 58L55 45M64 53H82M42 78L47 84L55 71M64 79H82"/>',
    "speed": '<circle cx="64" cy="71" r="34" fill="#FFFFFF"/><path fill="#FFD0AE" d="M53 16H75V28H53Z"/><path d="M64 28V36M89 40L98 31M64 49V71L79 80"/>',
    "quality": '<path fill="#FFFFFF" d="M64 23Q83 35 100 35V64Q100 91 64 108Q28 91 28 64V35Q46 35 64 23Z"/><path d="M45 65L59 79L84 51"/>',
    "consistency": '<path d="M34 53Q46 25 75 31Q93 35 99 53M99 53L97 31M99 53L79 52M94 77Q82 105 53 99Q35 95 29 77M29 77L31 99M29 77L49 78"/><circle cx="64" cy="64" r="12" fill="#FFFFFF"/>',
    "settings": '<path d="M30 38H99M30 65H99M30 91H99"/><rect x="43" y="28" width="17" height="20" rx="6" fill="#FFFFFF"/><rect x="76" y="55" width="17" height="20" rx="6" fill="#FFFFFF"/><rect x="43" y="81" width="17" height="20" rx="6" fill="#FFFFFF"/>',
    "import": '<path fill="#FFFFFF" d="M27 69H45L52 81H79L86 69H102V103H27Z"/><path d="M64 22V65M48 50L64 66L80 50"/>',
    "export": '<path fill="#FFFFFF" d="M27 69H45L52 81H79L86 69H102V103H27Z"/><path d="M64 61V20M48 36L64 20L80 36"/>',
    "evidence": '<circle cx="56" cy="55" r="29" fill="#FFFFFF"/><path d="M78 78L105 105" stroke-width="12"/><path d="M43 55H69M56 42V68"/>',
    "alert": '<path fill="#FFFFFF" d="M56 28Q64 14 72 28L108 94Q114 106 101 106H26Q13 106 20 94Z"/><path d="M64 47V73"/><circle cx="64" cy="89" r="3" fill="#24252B" stroke="none"/>',
}
for i, (name, shape) in enumerate(SHAPES.items()):
    color = COLORS[i % len(COLORS)]
    tilt = -3 if i % 2 == 0 else 3
    backdrop = f'<path d="M21 16Q45 5 72 12Q105 9 115 35Q124 62 112 92Q103 119 70 117Q36 123 17 102Q5 84 10 55Q6 29 21 16Z" fill="{color}" stroke="white" stroke-width="10"/>'
    # White outer die-cut edge; rounded dark drawing remains readable at 24px.
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128"><g stroke="{INK}" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" fill="none">{backdrop}<g transform="rotate({tilt} 64 64)">{shape}</g></g></svg>'
    (OUT / f"{name}.svg").write_text(svg)
app = f'''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
<g stroke-linecap="round" stroke-linejoin="round">
<path d="M24 16Q8 18 10 43L14 98Q15 116 36 117L91 114Q115 116 121 98L120 61Q122 37 102 28L77 11Q54 1 24 16Z" fill="#C7B6EE" stroke="#FFFFFF" stroke-width="8"/>
<g transform="rotate(-7 57 61)" stroke="{INK}" stroke-width="5.7">
<rect x="27" y="20" width="62" height="87" rx="10" fill="#FFD0AE"/>
<rect x="44" y="12" width="29" height="20" rx="6" fill="#DDF29B"/>
<path d="M41 49H72M41 64H61M41 79H55" fill="none"/>
</g>
<path d="M101 101L116 116" stroke="{INK}" stroke-width="13"/><path d="M101 101L116 116" stroke="#F4B9D4" stroke-width="5"/>
<circle cx="85" cy="82" r="25" fill="#A8D9F4" stroke="{INK}" stroke-width="6"/>
<path d="M73 82L82 90L98 72" stroke="{INK}" stroke-width="6" fill="none"/>
<path d="M108 21V31M103 26H113" stroke="{INK}" stroke-width="4"/>
</g></svg>'''
(OUT / "app.svg").write_text(app)

# Render authentic assets, not a pretend application screenshot.

qt = QApplication.instance() or QApplication([])


def render(path, size):
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(path)).render(painter)
    painter.end()
    return image


for size in [1024, 256, 64, 32]:
    render(OUT / "app.svg", size).save(str(OUT / f"app-{size}.png"))
sheet = QImage(1200, 900, QImage.Format_ARGB32_Premultiplied)
sheet.fill(QColor("#F6F7F9"))
p = QPainter(sheet)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(QColor(INK))
p.setFont(QFont("DejaVu Sans", 23, QFont.Bold))
p.drawText(42, 52, "Product Blinder · Original sticker icon assets")
p.setFont(QFont("DejaVu Sans", 12))
p.drawText(42, 84, "ASSET CONTACT SHEET — not an application screenshot · MIT original artwork")
names = ["app"] + list(SHAPES)
for i, name in enumerate(names):
    x = 44 + (i % 5) * 232
    y = 120 + (i // 5) * 226
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(x, y, 210, 204, 18, 18)
    QSvgRenderer(str(OUT / f"{name}.svg")).render(p, QRectF(x + 45, y + 14, 120, 120))
    p.setPen(QColor(INK))
    p.setFont(QFont("DejaVu Sans", 13, QFont.Bold))
    p.drawText(QRectF(x, y + 145, 210, 25), Qt.AlignCenter, name.replace("app", "Product Blinder"))
    # Real small-size raster samples alongside the full art.
    for j, size in enumerate([24, 32]):
        p.drawImage(x + 70 + j * 46, y + 176, render(OUT / f"{name}.svg", size))
p.end()
sheet.save(str(ROOT / "reports/v0.5/icon-contact-sheet.png"))
print("Created", len(names), "SVG assets and four transparent app rasters")
