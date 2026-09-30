from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyledItemDelegate

from icons import build_colored_icon

# Stroke-style icons (24x24 grid). Each section gets its own color.
SIDEBAR_SECTIONS = [
    (
        "Composer",
        "#3B82F6",
        '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    ),
    (
        "Réception",
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
        "Boîte d'envoi",
        "#F59E0B",
        '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    ),
    (
        "Historique",
        "#14B8A6",
        '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    ),
    (
        "Appareils",
        "#8B5CF6",
        '<rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>',
    ),
    (
        "SIM",
        "#EF4444",
        '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8l6 6v10a2 2 0 0 1-2 2z"/>'
        '<rect x="8" y="12" width="8" height="6" rx="1"/>',
    ),
]

SIDEBAR_SECTION_NAMES = [section_name for section_name, _, _ in SIDEBAR_SECTIONS]
INBOX_SECTION_NAME = "Réception"

SEPARATOR_COLOR = QColor(128, 128, 128, 64)
SEPARATOR_INSET_LEFT_PIXELS = 14
SEPARATOR_INSET_RIGHT_PIXELS = 10
ICON_SIZE_PIXELS = 22

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

    def show_unread_inbox_count(self, unread_count):
        """'Réception (3)' in bold while some received messages are unread."""
        inbox_item = self.item(SIDEBAR_SECTION_NAMES.index(INBOX_SECTION_NAME))
        inbox_item.setText(f"{INBOX_SECTION_NAME} ({unread_count})" if unread_count else INBOX_SECTION_NAME)
        item_font = inbox_item.font()
        item_font.setBold(unread_count > 0)
        inbox_item.setFont(item_font)
