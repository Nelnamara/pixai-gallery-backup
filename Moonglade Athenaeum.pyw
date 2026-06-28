#!/usr/bin/env pythonw
"""Moonglade Athenaeum launcher.

Double-click this file to open the desktop app with NO terminal window
(.pyw is associated with pythonw.exe on Windows). It just runs pixai_gui.py from
this folder, so config.json and pixai_backup/ resolve normally.

Make a desktop / taskbar shortcut: right-click -> Send to -> Desktop (create
shortcut), then optionally set its icon to moonglade.ico.
"""
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)                    # so config.json / pixai_backup resolve here
sys.path.insert(0, here)
runpy.run_path(os.path.join(here, "pixai_gui.py"), run_name="__main__")
