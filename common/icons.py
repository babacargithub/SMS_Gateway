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

PLAY_ICON_SHAPES = '<polygon points="5 3 19 12 5 21 5 3"/>'
PAUSE_ICON_SHAPES = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>'
STOP_ICON_SHAPES = '<rect x="4" y="4" width="16" height="16" rx="2" ry="2"/>'
CLOCK_ICON_SHAPES = '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>'
REFRESH_ICON_SHAPES = (
    '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>'
    '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>'
)
HOURGLASS_ICON_SHAPES = (  # the "figure eight" timeout icon: a message in flight
    '<path d="M6 2h12M6 22h12M7 2v4.5a5 5 0 0 0 2 4l3 1.5-3 1.5a5 5 0 0 0-2 4V22'
    'M17 2v4.5a5 5 0 0 1-2 4L12 12l3 1.5a5 5 0 0 1 2 4V22"/>'
)
CHECK_CIRCLE_ICON_SHAPES = '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'
X_CIRCLE_ICON_SHAPES = (
    '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
)
HELP_CIRCLE_ICON_SHAPES = (
    '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
    '<line x1="12" y1="17" x2="12.01" y2="17"/>'
)
REPLY_ICON_SHAPES = '<polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>'
