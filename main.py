"""
LLM Clip Harvester - Desktop Application Entry Point.
Production-ready YouTube timestamp slicer and batch video extractor for video editors.
Performs startup dependency checks for FFmpeg and Playwright Chromium,
enables high-DPI scaling on Windows, and launches the CustomTkinter GUI.
"""

import os
import sys
import subprocess
import tkinter as tk
from tkinter import messagebox

from browser_agent import check_playwright_available
from downloader import check_ffmpeg_installed, check_nvenc_supported, get_node_runtime_path
from gui import launch_gui


def enable_windows_high_dpi():
    """Enable high-DPI awareness on Windows for crystal-clear fonts and UI."""
    if sys.platform == "win32":
        try:
            import ctypes
            # Per-monitor DPI awareness (Windows 8.1+)
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                # Fallback for Windows Vista/7/8
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def run_startup_dependency_checks() -> bool:
    """
    Check if FFmpeg and Playwright Chromium are available.
    Shows informative dialog if anything critical is missing.
    """
    warnings = []
    actions = []

    # 1. FFmpeg Check
    ff_installed, ff_details = check_ffmpeg_installed()
    if not ff_installed:
        warnings.append("• FFmpeg is missing or not found in system PATH.")
        actions.append("Install FFmpeg using: winget install Gyan.FFmpeg (or choco install ffmpeg)")

    # 2. Playwright Check
    pw_ready, pw_details = check_playwright_available()
    if not pw_ready:
        warnings.append(f"• Playwright Chromium is not ready: {pw_details}")
        actions.append("Install Chromium browser: python -m playwright install chromium")

    # If critical dependencies are missing, alert the user
    if warnings:
        root = tk.Tk()
        root.withdraw()
        
        warn_msg = (
            "LLM Clip Harvester detected missing dependencies:\n\n"
            + "\n".join(warnings)
            + "\n\nRecommended Fix:\n"
            + "\n".join(actions)
            + "\n\nWould you like to continue anyway?"
        )
        
        proceed = messagebox.askyesno(
            "Dependency Notice - LLM Clip Harvester",
            warn_msg,
            icon="warning"
        )
        root.destroy()
        return proceed

    return True


def main():
    """Main entry point."""
    print("=" * 60)
    print("LLM Clip Harvester - Starting Up...")
    print("=" * 60)

    # High DPI Scaling
    enable_windows_high_dpi()

    # Startup verification
    can_proceed = run_startup_dependency_checks()
    if not can_proceed:
        print("[System] Application aborted by user due to missing dependencies.")
        sys.exit(1)

    print("[System] Dependencies verified. Launching GUI...")
    launch_gui()


if __name__ == "__main__":
    main()
