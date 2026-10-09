from PySide6.QtWidgets import QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from common.french_labels import format_message_count

PANEL_TEXT_MAXIMUM_HEIGHT_PIXELS = 130
TEXT_FRAME_PADDING_PIXELS = 14
EMPTY_SELECTION_HINT = "Sélectionnez un message pour voir son texte complet."


class MessageDetailPanel(QWidget):
    """Section below a message list showing the full text of the selected message."""

    def __init__(self):
        super().__init__()
        self.title_label = QLabel(EMPTY_SELECTION_HINT)
        self.title_label.setStyleSheet("font-weight: bold;")
        self.full_text_view = QPlainTextEdit()
        self.full_text_view.setReadOnly(True)
        self.full_text_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.full_text_view.setVisible(False)

        panel_layout = QVBoxLayout(self)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(self.title_label)
        panel_layout.addWidget(self.full_text_view)

    def show_selected_messages(self, selected_messages):
        """selected_messages: rows with recipient_name, phone_number and message_text."""
        if len(selected_messages) == 1:
            message = selected_messages[0]
            self.title_label.setText(f"{message.recipient_name or '—'} · {message.phone_number}")
            self.full_text_view.setPlainText(message.message_text)
            self.fit_text_view_height_to_content()
            self.full_text_view.setVisible(True)
            return
        self.full_text_view.clear()
        self.full_text_view.setVisible(False)
        if selected_messages:
            self.title_label.setText(f"{format_message_count(len(selected_messages))} sélectionnés")
        else:
            self.title_label.setText(EMPTY_SELECTION_HINT)

    def fit_text_view_height_to_content(self):
        """Just tall enough for the text (wrapped at the current width), never above the maximum."""
        self.full_text_view.document().setTextWidth(max(self.full_text_view.viewport().width(), 200))
        # a plain-text document reports its height in lines (wrapped ones included), not pixels
        line_count = int(self.full_text_view.document().size().height())
        content_height = line_count * self.full_text_view.fontMetrics().lineSpacing() + TEXT_FRAME_PADDING_PIXELS
        self.full_text_view.setFixedHeight(min(content_height, PANEL_TEXT_MAXIMUM_HEIGHT_PIXELS))
