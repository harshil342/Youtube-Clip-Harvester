"""
Modern CustomTkinter GUI for LLM Clip Harvester.
Production-ready, dark-themed two-panel dashboard layout:
- Left Panel: Raw LLM input box, sample loader, parsing trigger, output directory selector,
  handle padding control, quality/codec profile picker, strict 4K/1080p quality enforcement,
  headless browser toggles, system diagnostics badges.
- Right Panel: Interactive Queue Table (Treeview) with item selection/deletion,
  dedicated Verified Quality column (4K / 1440p / 1080p badges), pre-flight quality scanner,
  batch clipping start/stop controls, dual progress bars, and live scrolling console terminal.
- 100% Non-blocking: background threading for downloads, parsing, pre-flight scans, and browser actions.
"""

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

import customtkinter as ctk

from browser_agent import BrowserAgent, check_playwright_available
from downloader import (
    ALL_PROFILES,
    PROFILE_NVENC_H264,
    PROFILE_STREAM_COPY,
    PROFILE_1080P_H264,
    PROFILE_PRORES,
    BatchClipper,
    check_ffmpeg_installed,
    check_nvenc_supported,
    get_ffmpeg_path,
    get_node_runtime_path,
    inspect_video_quality,
)
from parser import (
    ParsedClip,
    format_seconds_to_timestamp,
    parse_llm_input,
)

# Sample LLM output for instant testing
SAMPLE_LLM_TEXT = """# Video Edit Beat Sheet - Cyberpunk Action Scene

01_Intro_Hook | https://www.youtube.com/watch?v=aqz-KE-bpKQ | 0:05-0:08, 0:12-0:15
02_Hero_Entry | https://www.youtube.com/watch?v=H_7QCxsk8hg | 0:49-0:55
03_Cinematic_Drift | "blender tears of steel 4k" | 0:20-0:25

| Beat Label | Target Video / Search Query | Timestamps |
| --- | --- | --- |
| 04_City_Overlook | https://youtu.be/aqz-KE-bpKQ | 01:05 – 01:10 |
| 05_SciFi_Action | "open movie project blender 4k" | 0:40 to 0:46 |
"""


class ClipHarvesterApp(ctk.CTk):
    """Main application window for LLM Clip Harvester."""

    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("LLM Clip Harvester - Automated Video Editor Suite")
        self.geometry("1420x900")
        self.minsize(1150, 720)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # State Variables
        self.parsed_clips: List[ParsedClip] = []
        self.active_clipper: Optional[BatchClipper] = None
        self.is_processing = False
        self.browser_agent = BrowserAgent(headless=True)
        self.cookie_file_path: Optional[str] = None
        self.is_scanning_qualities = False

        # Default output folder: User's Videos/LLM_Clips or Desktop/LLM_Clips
        user_videos = os.path.join(os.path.expanduser("~"), "Videos", "LLM_Clips")
        if not os.path.exists(user_videos):
            try:
                os.makedirs(user_videos, exist_ok=True)
            except Exception:
                user_videos = os.path.join(os.path.expanduser("~"), "LLM_Clips")
                os.makedirs(user_videos, exist_ok=True)
        self.output_dir_var = tk.StringVar(value=user_videos)

        # Settings variables
        self.padding_var = tk.StringVar(value="+1s (Standard)")
        self.profile_var = tk.StringVar(value=PROFILE_NVENC_H264)
        self.strict_quality_var = tk.BooleanVar(value=True)  # Reject < 1080p
        self.use_browser_var = tk.BooleanVar(value=True)
        self.prefer_4k_search_var = tk.BooleanVar(value=True)

        # Setup Layout
        self._setup_styles()
        self._build_header()
        self._build_main_panels()
        self._build_status_bar()

        # Run initial background dependency verification
        self.after(200, self._run_system_diagnostics)

    def _setup_styles(self):
        """Configure ttk styles for Dark Mode Treeview."""
        style = ttk.Style()
        style.theme_use("clam")

        # Treeview styling
        style.configure(
            "Custom.Treeview",
            background="#1e1e24",
            foreground="#e2e8f0",
            fieldbackground="#1e1e24",
            rowheight=30,
            font=("Segoe UI", 10),
            borderwidth=0,
        )
        style.configure(
            "Custom.Treeview.Heading",
            background="#2b2b36",
            foreground="#94a3b8",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padding=6,
        )
        style.map(
            "Custom.Treeview",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )

    def _build_header(self):
        """Top banner with app branding and quick actions."""
        self.header_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#18181b", height=60)
        self.header_frame.pack(side="top", fill="x")

        # Title & Badge
        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=20, pady=10)

        app_title = ctk.CTkLabel(
            title_box,
            text="LLM Clip Harvester",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#f8fafc",
        )
        app_title.pack(side="left", padx=(0, 10))

        pro_badge = ctk.CTkLabel(
            title_box,
            text="PRO 4K/UHD",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            fg_color="#9333ea",
            text_color="#ffffff",
            corner_radius=6,
            padx=8,
            pady=2,
        )
        pro_badge.pack(side="left")

        subtitle = ctk.CTkLabel(
            self.header_frame,
            text="Guaranteed 4K & 1080p Beat-by-Beat Timestamp Slicer for Premiere Pro & After Effects",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#64748b",
        )
        subtitle.pack(side="left", padx=10)

        # Quick action: Open Output Folder
        self.open_folder_btn = ctk.CTkButton(
            self.header_frame,
            text="📂 Open Output Folder",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.open_output_folder,
            width=160,
            height=32,
        )
        self.open_folder_btn.pack(side="right", padx=20, pady=14)

    def _build_main_panels(self):
        """Split into Left Panel (Input/Controls) and Right Panel (Queue/Logs)."""
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(fill="both", expand=True, padx=16, pady=(12, 6))

        # Grid configuration: Left panel 430px, Right panel fills remainder
        self.main_container.grid_columnconfigure(0, weight=0, minsize=430)
        self.main_container.grid_columnconfigure(1, weight=1)
        self.main_container.grid_rowconfigure(0, weight=1)

        # ==================== LEFT PANEL ====================
        self.left_panel = ctk.CTkScrollableFrame(
            self.main_container,
            fg_color="#18181b",
            corner_radius=10,
            width=430,
        )
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)

        # 1. Raw Text Input Section
        input_header_box = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        input_header_box.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(
            input_header_box,
            text="📝 LLM Raw Output Input",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f1f5f9",
        ).pack(side="left")

        sample_btn = ctk.CTkButton(
            input_header_box,
            text="Load Sample",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#3b82f6",
            height=24,
            width=85,
            command=self.load_sample_input,
        )
        sample_btn.pack(side="right")

        self.input_textbox = ctk.CTkTextbox(
            self.left_panel,
            height=160,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#09090b",
            border_color="#27272a",
            border_width=1,
            corner_radius=8,
        )
        self.input_textbox.pack(fill="x", padx=12, pady=4)
        self.input_textbox.insert(
            "1.0",
            "# Paste raw LLM text, Pipe-format, Markdown tables, or JSON here.\n"
            "# Example:\n"
            "# 01_Hook | https://www.youtube.com/watch?v=aqz-KE-bpKQ | 0:08-0:10, 0:49-0:55\n"
        )

        # Parse & Clear Buttons
        btn_row = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(6, 12))

        self.parse_btn = ctk.CTkButton(
            btn_row,
            text="⚡ Parse & Preview Queue",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            height=36,
            command=self.parse_input_to_queue,
        )
        self.parse_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        clear_input_btn = ctk.CTkButton(
            btn_row,
            text="Clear",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#ef4444",
            width=65,
            height=36,
            command=lambda: self.input_textbox.delete("1.0", "end"),
        )
        clear_input_btn.pack(side="right")

        # 2. Output Folder Picker Section
        ctk.CTkLabel(
            self.left_panel,
            text="📁 Output Directory",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f1f5f9",
        ).pack(anchor="w", padx=12, pady=(4, 4))

        folder_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        folder_frame.pack(fill="x", padx=12, pady=(0, 12))

        self.folder_entry = ctk.CTkEntry(
            folder_frame,
            textvariable=self.output_dir_var,
            font=ctk.CTkFont(size=11),
            fg_color="#09090b",
            border_color="#27272a",
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        browse_btn = ctk.CTkButton(
            folder_frame,
            text="Browse...",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            width=80,
            command=self.browse_output_folder,
        )
        browse_btn.pack(side="right")

        # 3. Handle Padding (Transitions) Section
        padding_header = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        padding_header.pack(fill="x", padx=12, pady=(4, 4))

        ctk.CTkLabel(
            padding_header,
            text="⏱️ Handle Padding (Cut Handles)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f1f5f9",
        ).pack(side="left")

        ctk.CTkLabel(
            padding_header,
            text="(for video transitions)",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
        ).pack(side="left", padx=6)

        padding_options = [
            "+0s (Exact Frame Cut)",
            "+1s (Standard)",
            "+2s (Extended Handles)",
            "+3s (Long Padding)",
            "+5s (B-Roll Cushion)",
        ]
        self.padding_dropdown = ctk.CTkOptionMenu(
            self.left_panel,
            values=padding_options,
            variable=self.padding_var,
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            button_color="#3f3f46",
            button_hover_color="#52525b",
            dropdown_fg_color="#18181b",
        )
        self.padding_dropdown.pack(fill="x", padx=12, pady=(0, 12))

        # 4. Quality & Codec Selector
        ctk.CTkLabel(
            self.left_panel,
            text="🎬 Quality & Codec Profile",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f1f5f9",
        ).pack(anchor="w", padx=12, pady=(4, 4))

        self.profile_dropdown = ctk.CTkOptionMenu(
            self.left_panel,
            values=ALL_PROFILES,
            variable=self.profile_var,
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            button_color="#3f3f46",
            button_hover_color="#52525b",
            dropdown_fg_color="#18181b",
        )
        self.profile_dropdown.pack(fill="x", padx=12, pady=(0, 6))

        # 5. Strict 4K / 1080p Quality Enforcement Checkbox
        self.strict_quality_chk = ctk.CTkCheckBox(
            self.left_panel,
            text="Strict 4K / 1080p Only (Reject < 1080p footage)",
            variable=self.strict_quality_var,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#facc15",
            checkbox_width=20,
            checkbox_height=20,
        )
        self.strict_quality_chk.pack(anchor="w", padx=14, pady=(2, 2))

        ctk.CTkLabel(
            self.left_panel,
            text="Refuses low-res 360p/480p streams. Enforces editor-ready resolution.",
            font=ctk.CTkFont(size=10),
            text_color="#64748b",
        ).pack(anchor="w", padx=38, pady=(0, 10))

        # 6. Headless Browser & Search Options
        ctk.CTkLabel(
            self.left_panel,
            text="🌐 Browser Automation & Search Fallback",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f1f5f9",
        ).pack(anchor="w", padx=12, pady=(4, 4))

        self.browser_chk = ctk.CTkCheckBox(
            self.left_panel,
            text="Use Headless Chrome for Search Fallback & Bot Bypass",
            variable=self.use_browser_var,
            font=ctk.CTkFont(size=11),
            checkbox_width=20,
            checkbox_height=20,
        )
        self.browser_chk.pack(anchor="w", padx=14, pady=3)

        self.prefer_4k_chk = ctk.CTkCheckBox(
            self.left_panel,
            text="Prefer 4K Filter in YouTube Search (sp=EgJwAQ%253D%253D)",
            variable=self.prefer_4k_search_var,
            font=ctk.CTkFont(size=11),
            checkbox_width=20,
            checkbox_height=20,
        )
        self.prefer_4k_chk.pack(anchor="w", padx=14, pady=(3, 12))

        # 7. System Diagnostics Box
        diag_box = ctk.CTkFrame(self.left_panel, fg_color="#09090b", corner_radius=8)
        diag_box.pack(fill="x", padx=12, pady=(4, 12))

        ctk.CTkLabel(
            diag_box,
            text="HARDWARE & ENGINE STATUS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#64748b",
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self.ffmpeg_badge = ctk.CTkLabel(
            diag_box,
            text="FFmpeg: Checking...",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color="#94a3b8",
        )
        self.ffmpeg_badge.pack(anchor="w", padx=10, pady=1)

        self.nvenc_badge = ctk.CTkLabel(
            diag_box,
            text="NVENC Acceleration: Checking...",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color="#94a3b8",
        )
        self.nvenc_badge.pack(anchor="w", padx=10, pady=1)

        self.playwright_badge = ctk.CTkLabel(
            diag_box,
            text="Playwright Chromium: Checking...",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color="#94a3b8",
        )
        self.playwright_badge.pack(anchor="w", padx=10, pady=(1, 8))

        # ==================== RIGHT PANEL ====================
        self.right_panel = ctk.CTkFrame(self.main_container, fg_color="#18181b", corner_radius=10)
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)

        # Top Bar of Right Panel: Queue Title & Action Buttons
        queue_top = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        queue_top.pack(fill="x", padx=14, pady=(12, 6))

        self.queue_count_label = ctk.CTkLabel(
            queue_top,
            text="📋 Clip Harvesting Queue (0 items)",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f8fafc",
        )
        self.queue_count_label.pack(side="left")

        # Table Control Buttons
        del_btn = ctk.CTkButton(
            queue_top,
            text="Delete Selected",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#ef4444",
            height=28,
            width=110,
            command=self.delete_selected_queue_items,
        )
        del_btn.pack(side="right", padx=(4, 0))

        clear_q_btn = ctk.CTkButton(
            queue_top,
            text="Clear Queue",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#52525b",
            height=28,
            width=90,
            command=self.clear_queue,
        )
        clear_q_btn.pack(side="right", padx=(4, 0))

        self.scan_qualities_btn = ctk.CTkButton(
            queue_top,
            text="🔍 Scan Stream Qualities",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#9333ea",
            height=28,
            width=150,
            command=self.scan_all_qualities_in_background,
        )
        self.scan_qualities_btn.pack(side="right", padx=(4, 0))

        # Interactive Queue Table (Treeview inside a frame with scrollbars)
        table_frame = ctk.CTkFrame(self.right_panel, fg_color="#1e1e24", corner_radius=8)
        table_frame.pack(fill="both", expand=True, padx=14, pady=4)

        columns = ("index", "beat", "target", "slice", "effective", "duration", "quality", "status")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            style="Custom.Treeview",
            selectmode="extended",
        )

        self.tree.heading("index", text="#")
        self.tree.heading("beat", text="Beat Label")
        self.tree.heading("target", text="YouTube URL / Search Query")
        self.tree.heading("slice", text="Original Slice")
        self.tree.heading("effective", text="Effective Cut (+Handle)")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("quality", text="Verified Quality")
        self.tree.heading("status", text="Status")

        self.tree.column("index", width=40, anchor="center")
        self.tree.column("beat", width=145, anchor="w")
        self.tree.column("target", width=240, anchor="w")
        self.tree.column("slice", width=95, anchor="center")
        self.tree.column("effective", width=130, anchor="center")
        self.tree.column("duration", width=65, anchor="center")
        self.tree.column("quality", width=155, anchor="center")
        self.tree.column("status", width=95, anchor="center")

        # Treeview scrollbars
        tree_y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        tree_x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_y_scroll.set, xscrollcommand=tree_x_scroll.set)

        tree_y_scroll.pack(side="right", fill="y")
        tree_x_scroll.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)

        # Status row tag configurations
        self.tree.tag_configure("Ready", foreground="#38bdf8")
        self.tree.tag_configure("Searching", foreground="#c084fc")
        self.tree.tag_configure("Inspecting", foreground="#a855f7")
        self.tree.tag_configure("Downloading", foreground="#facc15")
        self.tree.tag_configure("Transcoding", foreground="#fb923c")
        self.tree.tag_configure("Completed", foreground="#4ade80")
        self.tree.tag_configure("Failed", foreground="#f87171")
        self.tree.tag_configure("Skipped", foreground="#94a3b8")

        # Double-click to open completed file
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # Batch Execution Bar
        batch_bar = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        batch_bar.pack(fill="x", padx=14, pady=(8, 4))

        self.start_batch_btn = ctk.CTkButton(
            batch_bar,
            text="🚀 START HIGH-QUALITY BATCH CLIPPING",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#16a34a",
            hover_color="#15803d",
            height=42,
            command=self.start_batch_clipping,
        )
        self.start_batch_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_batch_btn = ctk.CTkButton(
            batch_bar,
            text="⏹️ STOP / CANCEL",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#dc2626",
            hover_color="#b91c1c",
            height=42,
            width=160,
            state="disabled",
            command=self.cancel_batch_clipping,
        )
        self.stop_batch_btn.pack(side="right")

        # Overall Batch Progress Bar
        self.progress_bar = ctk.CTkProgressBar(
            self.right_panel,
            height=10,
            fg_color="#27272a",
            progress_color="#3b82f6",
        )
        self.progress_bar.pack(fill="x", padx=14, pady=(4, 2))
        self.progress_bar.set(0.0)

        self.progress_label = ctk.CTkLabel(
            self.right_panel,
            text="Ready. Paste LLM output and click 'Parse & Preview Queue' to start.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        )
        self.progress_label.pack(anchor="w", padx=14, pady=(0, 6))

        # Live Terminal Console Header
        term_header = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        term_header.pack(fill="x", padx=14, pady=(6, 2))

        ctk.CTkLabel(
            term_header,
            text="💻 LIVE PROCESS CONSOLE LOG",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#64748b",
        ).pack(side="left")

        clear_log_btn = ctk.CTkButton(
            term_header,
            text="Clear Log",
            font=ctk.CTkFont(size=10),
            fg_color="#27272a",
            hover_color="#3f3f46",
            height=20,
            width=65,
            command=lambda: self.log_textbox.delete("1.0", "end"),
        )
        clear_log_btn.pack(side="right")

        # Live Terminal Console Textbox
        self.log_textbox = ctk.CTkTextbox(
            self.right_panel,
            height=140,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#09090b",
            border_color="#27272a",
            border_width=1,
            corner_radius=8,
        )
        self.log_textbox.pack(fill="x", padx=14, pady=(0, 12))

    def _build_status_bar(self):
        """Bottom status bar with quick system info."""
        self.status_bar = ctk.CTkFrame(self, height=26, corner_radius=0, fg_color="#09090b")
        self.status_bar.pack(side="bottom", fill="x")

        self.status_bar_label = ctk.CTkLabel(
            self.status_bar,
            text="LLM Clip Harvester Ready.",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
        )
        self.status_bar_label.pack(side="left", padx=16)

    # ==================== LOGGING & THREAD-SAFE UI DISPATCH ====================

    def log(self, message: str):
        """Thread-safe append to terminal log."""
        timestamp = time.strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        self.after(0, self._append_log_text, formatted)

    def _append_log_text(self, text: str):
        self.log_textbox.insert("end", text)
        self.log_textbox.see("end")

    def _update_progress_ui(self, fraction: float, status_text: str):
        """Thread-safe progress bar and label update."""
        self.after(0, self._apply_progress, fraction, status_text)

    def _apply_progress(self, fraction: float, status_text: str):
        self.progress_bar.set(fraction)
        self.progress_label.configure(text=status_text)
        self.status_bar_label.configure(text=status_text)

    def _update_clip_row_status(self, clip_index: int, new_status: str, out_path: Optional[str]):
        """Thread-safe update of specific row status in the Treeview."""
        self.after(0, self._apply_row_status, clip_index, new_status, out_path)

    def _apply_row_status(self, clip_index: int, new_status: str, out_path: Optional[str]):
        for item_id in self.tree.get_children():
            vals = list(self.tree.item(item_id, "values"))
            if vals and int(vals[0]) == clip_index:
                vals[7] = new_status
                self.tree.item(item_id, values=vals, tags=(new_status,))
                break

    def _update_clip_row_quality(self, clip_index: int, new_quality: str):
        """Thread-safe update of specific row quality badge in the Treeview."""
        self.after(0, self._apply_row_quality, clip_index, new_quality)

    def _apply_row_quality(self, clip_index: int, new_quality: str):
        for item_id in self.tree.get_children():
            vals = list(self.tree.item(item_id, "values"))
            if vals and int(vals[0]) == clip_index:
                vals[6] = new_quality
                self.tree.item(item_id, values=vals)
                break

    # ==================== ACTIONS & EVENT HANDLERS ====================

    def _run_system_diagnostics(self):
        """Check FFmpeg, NVENC, Node.js, and Playwright in background on startup."""
        def check_thread():
            # FFmpeg Check
            ff_ok, ff_msg = check_ffmpeg_installed()
            if ff_ok:
                ff_text = f"✓ FFmpeg Ready ({os.path.basename(get_ffmpeg_path() or 'ffmpeg')})"
                ff_color = "#4ade80"
            else:
                ff_text = "✗ FFmpeg Not Found in PATH!"
                ff_color = "#f87171"

            # NVENC Check
            nv_ok = check_nvenc_supported()
            if nv_ok:
                nv_text = "✓ NVIDIA NVENC Hardware Active"
                nv_color = "#4ade80"
            else:
                nv_text = "⚠ NVENC Inactive (CPU libx264 Fallback)"
                nv_color = "#facc15"

            # Playwright Check
            pw_ok, pw_msg = check_playwright_available()
            if pw_ok:
                pw_text = "✓ Playwright Chromium Ready"
                pw_color = "#4ade80"
            else:
                pw_text = "⚠ Playwright Missing (Run: playwright install)"
                pw_color = "#facc15"

            def update_badges():
                self.ffmpeg_badge.configure(text=ff_text, text_color=ff_color)
                self.nvenc_badge.configure(text=nv_text, text_color=nv_color)
                self.playwright_badge.configure(text=pw_text, text_color=pw_color)
                self.log(f"[Diagnostics] System check complete: FFmpeg={ff_ok}, NVENC={nv_ok}, Playwright={pw_ok}")

            self.after(0, update_badges)

        threading.Thread(target=check_thread, daemon=True).start()

    def load_sample_input(self):
        """Populate the input box with realistic sample LLM text."""
        self.input_textbox.delete("1.0", "end")
        self.input_textbox.insert("1.0", SAMPLE_LLM_TEXT)
        self.log("[System] Loaded sample LLM beat sheet.")

    def browse_output_folder(self):
        """Open native folder picker."""
        selected = filedialog.askdirectory(
            title="Select Output Folder for Harvested Clips",
            initialdir=self.output_dir_var.get(),
        )
        if selected:
            self.output_dir_var.set(selected)
            self.log(f"[Config] Output directory changed to: {selected}")

    def open_output_folder(self):
        """Reveal output folder in Windows Explorer."""
        folder = self.output_dir_var.get()
        if os.path.exists(folder):
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", folder])
        else:
            messagebox.showwarning("Folder Not Found", f"Folder does not exist:\n{folder}")

    def _get_padding_seconds(self) -> float:
        """Parse numeric padding seconds from padding dropdown string."""
        raw = self.padding_var.get()
        if "+0s" in raw:
            return 0.0
        elif "+1s" in raw:
            return 1.0
        elif "+2s" in raw:
            return 2.0
        elif "+3s" in raw:
            return 3.0
        elif "+5s" in raw:
            return 5.0
        return 1.0

    def parse_input_to_queue(self):
        """Parse raw text in input textbox and populate the interactive Queue Table."""
        raw_text = self.input_textbox.get("1.0", "end").strip()
        if not raw_text:
            messagebox.showinfo("Input Required", "Please paste LLM output or click 'Load Sample'.")
            return

        self.log("[Parser] Analyzing raw input text...")
        clips = parse_llm_input(raw_text)
        if not clips:
            messagebox.showwarning(
                "No Clips Found",
                "Could not identify any YouTube URLs or timestamp ranges.\n"
                "Please verify the format or try 'Load Sample'."
            )
            self.log("[Parser] Warning: No clips or timestamp ranges detected.")
            return

        self.parsed_clips = clips
        self._refresh_treeview()
        self.log(f"[Parser] Successfully parsed {len(clips)} clip task(s) into queue.")
        self.progress_label.configure(
            text=f"Queue populated with {len(clips)} clips. Starting background quality scan..."
        )

        # Trigger automatic pre-flight resolution scan
        self.scan_all_qualities_in_background()

    def scan_all_qualities_in_background(self):
        """Inspect video streams in background to show true available resolutions (4K / 1080p)."""
        if self.is_scanning_qualities or not self.parsed_clips:
            return

        def scanner():
            self.is_scanning_qualities = True
            self.log("[Quality Scanner] Inspecting video resolutions across queued items...")
            node_exe = get_node_runtime_path()
            
            # Cache URL quality lookups
            url_cache: Dict[str, str] = {}

            for clip in self.parsed_clips:
                if clip.is_search_query:
                    # Search query can be scanned after resolution
                    self._update_clip_row_quality(clip.index, "Search Query")
                    continue

                url = clip.target
                if url in url_cache:
                    q_label = url_cache[url]
                else:
                    self._update_clip_row_quality(clip.index, "Inspecting...")
                    _, _, q_label = inspect_video_quality(url, node_exe=node_exe)
                    url_cache[url] = q_label
                    self.log(f"[Quality Scanner] [{clip.index:02d}] {clip.beat_label} -> {q_label}")

                clip.quality_label = q_label
                self._update_clip_row_quality(clip.index, q_label)

            self.is_scanning_qualities = False
            self.log("[Quality Scanner] Resolution scan complete.")

        threading.Thread(target=scanner, daemon=True).start()

    def _refresh_treeview(self):
        """Redraw all items in the Treeview with current padding calculation."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        padding = self._get_padding_seconds()

        for clip in self.parsed_clips:
            eff_s, eff_e = clip.get_effective_range(padding)
            orig_slice = f"{clip.start_str} - {clip.end_str}"
            eff_slice = f"{format_seconds_to_timestamp(eff_s)} - {format_seconds_to_timestamp(eff_e)}"
            if padding > 0:
                eff_slice += f" (+{padding:g}s)"
            dur_str = f"{clip.duration + (padding * 2):.1f}s"
            target_display = f"🔍 {clip.target}" if clip.is_search_query else clip.target

            self.tree.insert(
                "",
                "end",
                values=(
                    f"{clip.index:02d}",
                    clip.beat_label,
                    target_display,
                    orig_slice,
                    eff_slice,
                    dur_str,
                    clip.quality_label,
                    clip.status,
                ),
                tags=(clip.status,),
            )

        self.queue_count_label.configure(
            text=f"📋 Clip Harvesting Queue ({len(self.parsed_clips)} items)"
        )

    def delete_selected_queue_items(self):
        """Remove selected items from Treeview and internal parsed list."""
        selected_ids = self.tree.selection()
        if not selected_ids:
            return

        indices_to_remove = set()
        for item_id in selected_ids:
            vals = self.tree.item(item_id, "values")
            if vals:
                indices_to_remove.add(int(vals[0]))
            self.tree.delete(item_id)

        self.parsed_clips = [c for c in self.parsed_clips if c.index not in indices_to_remove]
        # Re-index remaining clips
        for idx, clip in enumerate(self.parsed_clips, 1):
            clip.index = idx
        self._refresh_treeview()
        self.log(f"[Queue] Removed {len(indices_to_remove)} item(s) from queue.")

    def clear_queue(self):
        """Wipe all items from queue."""
        if not self.parsed_clips:
            return
        if messagebox.askyesno("Clear Queue", "Clear all items from the current queue?"):
            self.parsed_clips.clear()
            self._refresh_treeview()
            self.log("[Queue] Queue cleared.")

    def _on_tree_double_click(self, event):
        """Open completed video file on double-clicking a row."""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        vals = self.tree.item(item_id, "values")
        if not vals:
            return
        idx = int(vals[0])
        for clip in self.parsed_clips:
            if clip.index == idx:
                if clip.output_filepath and os.path.exists(clip.output_filepath):
                    if sys.platform == "win32":
                        os.startfile(clip.output_filepath)
                    else:
                        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", clip.output_filepath])
                else:
                    self.log(f"[{clip.index:02d}] Target: {clip.target}")
                break

    # ==================== BATCH EXECUTION ====================

    def start_batch_clipping(self):
        """Begin batch downloading and slicing in background thread."""
        if self.is_processing:
            return

        if not self.parsed_clips:
            messagebox.showinfo("Queue Empty", "Please parse clips into the queue before starting.")
            return

        output_dir = self.output_dir_var.get().strip()
        if not output_dir:
            messagebox.showwarning("Output Directory Missing", "Please select an output folder.")
            return

        os.makedirs(output_dir, exist_ok=True)

        self.is_processing = True
        self.start_batch_btn.configure(state="disabled")
        self.stop_batch_btn.configure(state="normal")
        self.parse_btn.configure(state="disabled")

        padding = self._get_padding_seconds()
        profile = self.profile_var.get()
        strict_quality = self.strict_quality_var.get()
        use_browser = self.use_browser_var.get()
        prefer_4k = self.prefer_4k_search_var.get()

        # Update Treeview with effective cuts before starting
        self._refresh_treeview()

        def worker():
            agent_for_clipper = self.browser_agent if use_browser else None

            # Optional cookie extraction for bot challenges
            cookie_path = None
            if use_browser:
                try:
                    self.log("[Session] Verifying browser session & extracting cookies for yt-dlp...")
                    cookie_path = self.browser_agent.export_session_cookies_file(log_callback=self.log)
                except Exception as e:
                    self.log(f"[Session] Notice: Cookie export skipped: {e}")

            self.active_clipper = BatchClipper(
                output_dir=output_dir,
                profile=profile,
                padding_seconds=padding,
                min_quality_strict=strict_quality,
                cookie_file=cookie_path,
                browser_agent=agent_for_clipper,
                prefer_4k_search=prefer_4k,
                log_callback=self.log,
                progress_callback=self._update_progress_ui,
                clip_status_callback=self._update_clip_row_status,
                quality_callback=self._update_clip_row_quality,
            )

            try:
                self.active_clipper.run_batch(self.parsed_clips)
            finally:
                def on_done():
                    self.is_processing = False
                    self.start_batch_btn.configure(state="normal")
                    self.stop_batch_btn.configure(state="disabled")
                    self.parse_btn.configure(state="normal")
                    self.active_clipper = None
                    messagebox.showinfo(
                        "Batch Finished",
                        f"Harvest complete!\nClips saved to:\n{output_dir}"
                    )

                self.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def cancel_batch_clipping(self):
        """Signal running batch to halt."""
        if self.active_clipper:
            self.active_clipper.cancel()
            self.stop_batch_btn.configure(state="disabled")
            self.progress_label.configure(text="Cancelling batch... waiting for current clip to finish.")


def launch_gui():
    """Application entry point."""
    app = ClipHarvesterApp()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()
