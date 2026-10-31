"""Which operating system the app runs on. The app supports macOS (the original Mac) and Windows; every place that
behaves differently asks here instead of testing `sys.platform` itself."""
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
