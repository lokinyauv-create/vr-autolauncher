# -*- coding: utf-8 -*-
"""
VR Auto Launcher entry point (kept for the Windows Startup shortcut / PyInstaller).
Windows: pip install pystray pillow
Linux:   see README (or just run ./vr-autolauncher)
"""

from vr_autolauncher.app import main

if __name__ == "__main__":
    main()
