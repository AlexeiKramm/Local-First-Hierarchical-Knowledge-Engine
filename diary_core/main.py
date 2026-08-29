"""
main.py
=======
Entry point for the Diary Analysis System.
Launches the main Tkinter GUI application.
"""

import sys
import os

# Ensure the project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diary_core.gui.app import DiaryAnalyzerApp

if __name__ == "__main__":
    app = DiaryAnalyzerApp()
    app.mainloop()
