"""Vector icons drawn from SVG shapes (24x24 grid, stroke style), tinted with any color."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

RENDER_SIZE_PIXELS = 66  # rendered large so the icon stays sharp on Retina screens

EDIT_ICON_SHAPES = '<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>'
TRASH_ICON_SHAPES = (
    '<polyline points="3 6 5 6 21 6"/>'
    '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
    '<path d="M10 11v6"/><path d="M14 11v6"/>'
    '<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
)


def build_colored_icon(svg_shapes, hex_color):
    svg_document = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{hex_color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{svg_shapes}</svg>"
    )
    icon_pixmap = QPixmap(RENDER_SIZE_PIXELS, RENDER_SIZE_PIXELS)
    icon_pixmap.fill(Qt.transparent)
    painter = QPainter(icon_pixmap)
    QSvgRenderer(QByteArray(svg_document.encode())).render(painter)
    painter.end()
    colored_icon = QIcon()
    # Qt would otherwise tint the icon when selected/hovered and wash out its color.
    for icon_mode in (QIcon.Normal, QIcon.Active, QIcon.Selected):
        colored_icon.addPixmap(icon_pixmap, icon_mode)
    return colored_icon
