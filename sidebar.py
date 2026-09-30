from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyledItemDelegate

# Stroke-style icons (24x24 grid). Each section gets its own color.
SIDEBAR_SECTIONS = [
    (
        "Compose",
        "#3B82F6",
        '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    ),
    (
        "Inbox",
        "#EC4899",
        '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/>'
        '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    ),
    (
        "Contacts",
        "#10B981",
        '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
        '<path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    ),
    (
        "Outbox",
        "#F59E0B",
        '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    ),
    (
        "Historique",
        "#14B8A6",
        '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    ),
    (
        "Devices",
        "#8B5CF6",
        '<rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>',
    ),
]

SIDEBAR_SECTION_NAMES = [section_name for section_name, _, _ in SIDEBAR_SECTIONS]

SEPARATOR_COLOR = QColor(128, 128, 128, 64)
SEPARATOR_INSET_LEFT_PIXELS = 14
SEPARATOR_INSET_RIGHT_PIXELS = 10
ICON_SIZE_PIXELS = 22
RENDER_SIZE_PIXELS = 66  # rendered large so the icon stays sharp on Retina screens

# Neutral translucent colors so the sidebar works in both light and dark mode.
SIDEBAR_STYLE_SHEET = """
QListWidget {
    border: none;
    border-right: 1px solid rgba(128, 128, 128, 0.35);
    outline: none;
    font-size: 15px;
    padding-top: 8px;
}
QListWidget::item {
    margin: 0px 10px 0px 14px;
    padding: 14px 10px 14px 12px;
    border-radius: 0px;
}
QListWidget::item:hover {
    background: rgba(128, 128, 128, 0.12);
}
QListWidget::item:selected {
    background: rgba(59, 130, 246, 0.16);
    color: palette(text);
    font-weight: 600;
}
"""


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


class SeparatorDelegate(QStyledItemDelegate):
    """Draws a thin line under every item except the last one."""

    def paint(self, painter, style_option, model_index):
        super().paint(painter, style_option, model_index)
        is_last_item = model_index.row() == model_index.model().rowCount() - 1
        if is_last_item:
            return
        line_y = style_option.rect.bottom()
        painter.save()
        painter.setPen(SEPARATOR_COLOR)
        painter.drawLine(
            style_option.rect.left() + SEPARATOR_INSET_LEFT_PIXELS,
            line_y,
            style_option.rect.right() - SEPARATOR_INSET_RIGHT_PIXELS,
            line_y,
        )
        painter.restore()


class Sidebar(QListWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(210)
        self.setIconSize(QSize(ICON_SIZE_PIXELS, ICON_SIZE_PIXELS))
        self.setStyleSheet(SIDEBAR_STYLE_SHEET)
        self.setFocusPolicy(Qt.NoFocus)
        self.setItemDelegate(SeparatorDelegate(self))
        for section_name, hex_color, svg_shapes in SIDEBAR_SECTIONS:
            self.addItem(QListWidgetItem(build_colored_icon(svg_shapes, hex_color), section_name))
