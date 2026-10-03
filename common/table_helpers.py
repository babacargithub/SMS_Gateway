"""Small helpers shared by the tables that show a position number (N°) in front of each row."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from common.french_labels import format_number

POSITION_COLUMN_TITLE = "N°"
POSITION_COLUMN_WIDTH_PIXELS = 70


def build_position_item(position_in_list):
    position_item = QTableWidgetItem(format_number(position_in_list))
    position_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return position_item
