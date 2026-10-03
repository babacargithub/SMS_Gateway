"""The application icon (Dock, window): a speech bubble sending out signal waves on a blue squircle."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 1024 x 1024 canvas; the squircle leaves the ~100 px margin macOS expects around Dock icons.
APPLICATION_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="background" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3B9DFF"/>
      <stop offset="1" stop-color="#1757D6"/>
    </linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#background)"/>
  <path d="M300 330 h300 a70 70 0 0 1 70 70 v190 a70 70 0 0 1 -70 70 h-170 l-110 95 v-95 h-20
           a70 70 0 0 1 -70 -70 v-190 a70 70 0 0 1 70 -70 z" fill="#FFFFFF"/>
  <circle cx="365" cy="495" r="36" fill="#1757D6"/>
  <circle cx="450" cy="495" r="36" fill="#1757D6"/>
  <circle cx="535" cy="495" r="36" fill="#1757D6"/>
  <g fill="none" stroke="#FFFFFF" stroke-width="42" stroke-linecap="round">
    <path d="M725 405 a130 130 0 0 1 0 180"/>
    <path d="M795 340 a230 230 0 0 1 0 310"/>
  </g>
</svg>
"""

ICON_SIZES_IN_PIXELS = (16, 32, 64, 128, 256, 512, 1024)


def render_application_icon_pixmap(size_in_pixels):
    image = QImage(size_in_pixels, size_in_pixels, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(APPLICATION_ICON_SVG.encode("utf-8"))).render(painter)
    painter.end()
    return QPixmap.fromImage(image)


def build_application_icon():
    application_icon = QIcon()
    for size_in_pixels in ICON_SIZES_IN_PIXELS:
        application_icon.addPixmap(render_application_icon_pixmap(size_in_pixels))
    return application_icon
