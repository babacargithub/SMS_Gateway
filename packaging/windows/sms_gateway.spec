# PyInstaller recipe for the Windows build (one folder, no console). Run through build_windows_installer.ps1.
# SPECPATH is the folder of this file (set by PyInstaller).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

repository_root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
build_assets_folder = os.path.join(SPECPATH, "build_assets")

analysis = Analysis(
    [os.path.join(repository_root, "app.py")],
    pathex=[repository_root],
    datas=[
        (os.path.join(repository_root, "devices", "sounds", "*.wav"), os.path.join("devices", "sounds")),
        *collect_data_files("libusb_package"),   # libusb-1.0.dll (pyusb loads it through libusb_package)
    ],
    binaries=collect_dynamic_libs("libusb_package"),
    hiddenimports=["usb.backend.libusb1", "libusb_package"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore"],
    noarchive=False,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SMS Gateway",
    icon=os.path.join(build_assets_folder, "sms_gateway.ico"),
    console=False,
    upx=False,
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    name="SMS Gateway",
    upx=False,
)
