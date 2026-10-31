"""Writes the Windows .ico (exe, shortcuts, installer) from the app's own SVG icon (common/app_icon.py).

Usage (from the repository root):  python packaging/windows/make_app_icon.py <output.ico>
The .ico holds PNG-compressed images (supported since Windows Vista)."""
import os
import struct
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from common.app_icon import render_application_icon_pixmap  # noqa: E402

ICO_SIZES_IN_PIXELS = (16, 24, 32, 48, 64, 128, 256)


def png_bytes_of_icon(size_in_pixels):
    png_data = QByteArray()
    buffer = QBuffer(png_data)
    buffer.open(QIODevice.WriteOnly)
    render_application_icon_pixmap(size_in_pixels).save(buffer, "PNG")
    buffer.close()
    return bytes(png_data)


def write_ico(output_path):
    images = [(size, png_bytes_of_icon(size)) for size in ICO_SIZES_IN_PIXELS]
    header = struct.pack("<HHH", 0, 1, len(images))
    directory_entries, image_data = b"", b""
    data_offset = len(header) + 16 * len(images)
    for size, png in images:
        size_byte = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        directory_entries += struct.pack("<BBBBHHII", size_byte, size_byte, 0, 0, 1, 32, len(png), data_offset + len(image_data))
        image_data += png
    Path(output_path).write_bytes(header + directory_entries + image_data)


if __name__ == "__main__":
    application = QGuiApplication(sys.argv[:1])
    write_ico(sys.argv[1])
    print(f"icon written to {sys.argv[1]}")
