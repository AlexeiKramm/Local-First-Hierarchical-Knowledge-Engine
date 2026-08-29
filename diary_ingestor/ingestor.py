#!/usr/bin/env python3
"""
ingestor.py — Diary Ingestor Launcher
======================================
Run this file to start the Diary Ingestor application.

Usage:
    python ingestor.py
"""
import sys
import os

# Ensure the parent directory is on the path so the package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diary_ingestor.gui.app import IngestorApp


def main():
    app = IngestorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
