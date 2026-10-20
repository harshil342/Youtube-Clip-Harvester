"""
Downloader and Clipper Module for LLM Clip Harvester.
Uses yt-dlp to slice timestamp ranges and FFmpeg for Premiere Pro edit-ready muxing and transcoding.

Features:
- Guaranteed 4K (2160p) & 1080p Stream Prioritization (bans low-res 360p/480p fallbacks)
- Strict Minimum Quality Filter (can reject/skip videos below 1080p if desired)
- Pre-flight Video Resolution & FPS Inspector
- Post-download FFprobe Verification of actual output dimensions (e.g. 3840x2160 @ 60fps)
- NVENC GPU-accelerated H.264 transcoding (with CPU libx264 fallback)
- Native Stream Copy (MKV/MP4)
- 1080p Native AVC1/AAC H.264
- Apple ProRes 422 (HQ) MOV
- Configurable handle padding (+0s, +1s, +2s, custom)
- Strict filename convention: {Index:02d}_{Sanitized_Beat_Label}_{VideoID}_{Start}-{End}.mp4
- Cancellation support & live stdout/stderr callback streaming
"""

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import yt_dlp
from yt_dlp.utils import download_range_func

from parser import (
    ParsedClip,
    format_seconds_for_filename,
    format_seconds_to_timestamp,
    sanitize_filename_label,
    extract_video_id,
)


def get_ffmpeg_path() -> Optional[str]:
    """Find ffmpeg executable path on system."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    common_paths = [
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin\ffmpeg.exe"),
        os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"),
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return None


def get_ffprobe_path() -> Optional[str]:
    """Find ffprobe executable path on system."""
    found = shutil.which("ffprobe")
    if found:
        return found
    common_paths = [
        r"C:\ProgramData\chocolatey\bin\ffprobe.exe",
        r"C:\ffmpeg\bin\ffprobe.exe",
        os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin\ffprobe.exe"),
        os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe"),
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return None


def get_node_runtime_path() -> Optional[str]:
    """Find node.exe or deno/bun to pass to yt-dlp's js_runtimes."""
    node_which = shutil.which("node")
    if node_which:
        return node_which
    common_node = [
        r"C:\Program Files\nodejs\node.exe",
        os.path.expanduser(r"~\AppData\Roaming\nvm\current\node.exe"),
        os.path.expanduser(r"~\scoop\apps\nodejs\current\node.exe"),
    ]
    for p in common_node:
        if os.path.exists(p):
            return p
    return None


def check_ffmpeg_installed() -> Tuple[bool, str]:
    """Verify that FFmpeg is installed and accessible."""
    ffmpeg_exe = get_ffmpeg_path()
    if not ffmpeg_exe:
        return False, "FFmpeg executable was not found in PATH or standard installation directories."
    try:
        res = subprocess.run([ffmpeg_exe, "-version"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            first_line = res.stdout.splitlines()[0] if res.stdout else "FFmpeg ready"
            return True, f"{first_line} ({ffmpeg_exe})"
        return False, f"FFmpeg returned non-zero code {res.returncode}"
    except Exception as e:
        return False, f"Error checking FFmpeg: {e}"


def check_nvenc_supported() -> bool:
    """Check if NVIDIA NVENC hardware encoder is functional on this system."""
    ffmpeg_exe = get_ffmpeg_path()
    if not ffmpeg_exe:
        return False
    try:
        cmd = [
            ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
            "-c:v", "h264_nvenc",
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        return res.returncode == 0
    except Exception:
        return False


def inspect_video_quality(
    url: str,
    node_exe: Optional[str] = None,
    cookie_file: Optional[str] = None
) -> Tuple[int, int, str]:
    """
    Inspect YouTube video metadata to determine true maximum available resolution and fps.
    Returns (max_height, max_fps, formatted_label), e.g. (2160, 60, "4K UHD (2160p60)")
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'remote_components': ['ejs:github'],
    }
    if node_exe:
        ydl_opts['js_runtimes'] = {'node': {'path': node_exe}}
    if cookie_file and os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
            video_fmts = [f for f in formats if f.get('vcodec') != 'none' and f.get('height')]
            if not video_fmts:
                return 0, 0, "Unknown"

            max_height = max(f.get('height', 0) for f in video_fmts)
            max_fps = max(f.get('fps', 0) or 0 for f in video_fmts if f.get('height') == max_height)

            if max_height >= 2160:
                label = f"4K UHD ({max_height}p@{int(max_fps)}fps)"
            elif max_height >= 1440:
                label = f"1440p QHD ({max_height}p@{int(max_fps)}fps)"
            elif max_height >= 1080:
                label = f"1080p FHD ({max_height}p@{int(max_fps)}fps)"
            elif max_height >= 720:
                label = f"720p HD ({max_height}p)"
            else:
                label = f"Low Res ({max_height}p)"

            return max_height, int(max_fps), label
    except Exception as e:
        return 0, 0, f"Error: {e}"


# Quality & Codec Profile Definitions
PROFILE_NVENC_H264 = "4K / 2160p Priority -> Edit-Ready H.264 MP4 (NVENC GPU Accelerated)"
PROFILE_STREAM_COPY = "4K / 2160p Priority (Native Stream Copy - MKV/MP4)"
PROFILE_1080P_H264 = "1080p Native H.264 MP4 (AVC1 + AAC)"
PROFILE_PRORES = "Apple ProRes 422 (HQ) 4K/1080p MOV (Mastering Grade)"

ALL_PROFILES = [
    PROFILE_NVENC_H264,
    PROFILE_STREAM_COPY,
    PROFILE_1080P_H264,
    PROFILE_PRORES
]


class BatchClipper:
    """Batch downloader and clipper for ParsedClip items with strict quality guarantees."""

    def __init__(
        self,
        output_dir: str,
        profile: str = PROFILE_NVENC_H264,
        padding_seconds: float = 1.0,
        min_quality_strict: bool = True,
        cookie_file: Optional[str] = None,
        browser_agent: Optional[object] = None,
        prefer_4k_search: bool = True,
        log_callback: Optional[Callable[[str], None]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        clip_status_callback: Optional[Callable[[int, str, Optional[str]], None]] = None,
        quality_callback: Optional[Callable[[int, str], None]] = None,
    ):
        self.output_dir = output_dir
        self.profile = profile
        self.padding_seconds = padding_seconds
        self.min_quality_strict = min_quality_strict
        self.cookie_file = cookie_file
        self.browser_agent = browser_agent
        self.prefer_4k_search = prefer_4k_search
        self.log_callback = log_callback or (lambda msg: None)
        self.progress_callback = progress_callback or (lambda pct, status: None)
        self.clip_status_callback = clip_status_callback or (lambda idx, status, out: None)
        self.quality_callback = quality_callback or (lambda idx, q_label: None)
        
        self.stop_requested = threading.Event()
        self.nvenc_available = check_nvenc_supported()
        self.ffmpeg_exe = get_ffmpeg_path() or "ffmpeg"
        self.ffprobe_exe = get_ffprobe_path() or "ffprobe"
        self.node_exe = get_node_runtime_path()

    def cancel(self):
        """Signal the batch process to cancel immediately."""
        self.stop_requested.set()
        self.log("[System] Cancellation requested by user.")

    def log(self, message: str):
        self.log_callback(message)

    def generate_filename(
        self,
        clip: ParsedClip,
        eff_start: float,
        eff_end: float,
        ext: str = "mp4"
    ) -> str:
        """
        Generate Premiere-sortable filename according to specification:
        {Index:02d}_{Sanitized_Beat_Label}_{VideoID}_{Start}-{End}.mp4
        Example: 01_Hook_WhiteVision_H_7QCxsk8hg_0m49s-0m55s.mp4
        """
        clean_beat = sanitize_filename_label(clip.beat_label)
        vid_id = clip.video_id or "UnknownID"
        start_fmt = format_seconds_for_filename(eff_start)
        end_fmt = format_seconds_for_filename(eff_end)
        filename = f"{clip.index:02d}_{clean_beat}_{vid_id}_{start_fmt}-{end_fmt}.{ext}"
        return filename

    def _build_ytdlp_opts(
        self,
        eff_start: float,
        eff_end: float,
        temp_tmpl: str,
        target_format: str,
        merge_format: Optional[str] = "mkv"
    ) -> Dict:
        """
        Construct yt-dlp options dictionary with strict high-resolution prioritization.
        Bans low-res formats and enforces remote challenge solving with Node.js.
        """
        opts = {
            'format': target_format,
            'format_sort': ['res:2160', 'res:1440', 'res:1080', 'fps', 'hdr:12', 'br'],
            'download_ranges': download_range_func(None, [(eff_start, eff_end)]),
            'force_keyframes_at_cuts': False,
            'outtmpl': temp_tmpl,
            'overwrites': True,
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'remote_components': ['ejs:github'],
        }
        if merge_format:
            opts['merge_output_format'] = merge_format

        # Add node js runtime if available
        if self.node_exe:
            opts['js_runtimes'] = {'node': {'path': self.node_exe}}

        # Add cookie file if available
        if self.cookie_file and os.path.exists(self.cookie_file):
            opts['cookiefile'] = self.cookie_file

        return opts

    def verify_output_file_dimensions(self, filepath: str) -> Tuple[int, int, str, float]:
        """
        Run ffprobe on the produced file to verify genuine output resolution, codec, and fps.
        Returns (width, height, codec_name, fps).
        """
        try:
            cmd = [
                self.ffprobe_exe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name,r_frame_rate",
                "-of", "default=noprint_wrappers=1",
                filepath
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            data = {}
            for line in res.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()

            width = int(data.get("width", 0))
            height = int(data.get("height", 0))
            codec = data.get("codec_name", "unknown")
            fps_str = data.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, den = fps_str.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            else:
                fps = float(fps_str) if fps_str else 30.0

            return width, height, codec, fps
        except Exception:
            return 0, 0, "unknown", 0.0

    def process_single_clip(self, clip: ParsedClip) -> bool:
        """Download, slice, and verify one clip item."""
        if self.stop_requested.is_set():
            clip.status = "Skipped"
            self.clip_status_callback(clip.index, "Skipped", None)
            return False

        # Step 1: Resolve search query via Playwright browser agent if needed
        if clip.is_search_query and not clip.resolved_url:
            if not self.browser_agent:
                clip.status = "Failed"
                clip.error_message = "Browser agent is disabled or unavailable for resolving search queries."
                self.log(f"[{clip.index:02d}] ERROR: {clip.error_message}")
                self.clip_status_callback(clip.index, "Failed", None)
                return False

            clip.status = "Searching"
            self.clip_status_callback(clip.index, "Searching", None)
            self.log(f"[{clip.index:02d}] Resolving search query via Playwright: '{clip.target}'...")
            
            try:
                found_url, title, vid_id = self.browser_agent.search_youtube_top_url(
                    clip.target,
                    prefer_4k=self.prefer_4k_search,
                    log_callback=self.log
                )
                if not found_url:
                    clip.status = "Failed"
                    clip.error_message = f"No YouTube video found for query: '{clip.target}'"
                    self.log(f"[{clip.index:02d}] ERROR: {clip.error_message}")
                    self.clip_status_callback(clip.index, "Failed", None)
                    return False
                
                clip.resolved_url = found_url
                clip.video_title = title
                clip.video_id = vid_id
                self.log(f"[{clip.index:02d}] Resolved to: '{title}' ({found_url})")
            except Exception as e:
                clip.status = "Failed"
                clip.error_message = f"Browser search error: {e}"
                self.log(f"[{clip.index:02d}] ERROR: {clip.error_message}")
                self.clip_status_callback(clip.index, "Failed", None)
                return False

        url = clip.resolved_url or clip.target
        eff_start, eff_end = clip.get_effective_range(self.padding_seconds)
        duration = eff_end - eff_start

        self.log(
            f"\n>>> [{clip.index:02d}] Pre-flight Quality Check: '{clip.beat_label}'\n"
            f"    Target URL: {url}\n"
            f"    Requested Slice: {format_seconds_to_timestamp(eff_start)} to {format_seconds_to_timestamp(eff_end)} "
            f"(Duration: {duration:.1f}s, Handle Padding: +{self.padding_seconds}s)"
        )

        clip.status = "Inspecting"
        self.clip_status_callback(clip.index, "Inspecting", None)

        # Step 2: Pre-flight stream quality check
        max_h, max_fps, q_label = inspect_video_quality(
            url, node_exe=self.node_exe, cookie_file=self.cookie_file
        )
        clip.quality_label = q_label
        self.quality_callback(clip.index, q_label)

        if max_h >= 2160:
            self.log(f"[{clip.index:02d}] [4K UHD DETECTED] Full {max_h}p@{max_fps}fps streams available.")
        elif max_h >= 1440:
            self.log(f"[{clip.index:02d}] [1440p QHD DETECTED] {max_h}p@{max_fps}fps streams available.")
        elif max_h >= 1080:
            self.log(f"[{clip.index:02d}] [1080p FHD DETECTED] {max_h}p@{max_fps}fps streams available.")
        else:
            self.log(f"[{clip.index:02d}] [Notice] Max creator resolution for this video is only {max_h}p.")

        # Strict Minimum Quality Filter
        if self.min_quality_strict and max_h > 0 and max_h < 1080:
            clip.status = "Failed"
            clip.error_message = f"Strict Quality Failed: Maximum resolution available is only {max_h}p (< 1080p threshold)."
            self.log(f"[{clip.index:02d}] REJECTED: Video does not meet 4K/1080p minimum requirement. Skipping low-res download.")
            self.clip_status_callback(clip.index, "Failed", None)
            return False

        clip.status = "Downloading"
        self.clip_status_callback(clip.index, "Downloading", None)

        # Temporary download path
        temp_base = os.path.join(self.output_dir, f"_temp_clip_{clip.index:02d}_{int(time.time())}")
        temp_tmpl = f"{temp_base}.%(ext)s"

        target_ext = "mov" if self.profile == PROFILE_PRORES else "mp4"
        final_filename = self.generate_filename(clip, eff_start, eff_end, target_ext)
        final_filepath = os.path.join(self.output_dir, final_filename)

        try:
            # Step 3: Strict Format Selection (NO solitary /best fallback)
            if self.profile == PROFILE_1080P_H264:
                format_selector = (
                    "bestvideo[height=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
                    "bestvideo[height=1080]+bestaudio/"
                    "bestvideo[height<=1080][height>=720]+bestaudio/"
                    "bestvideo+bestaudio"
                )
                merge_fmt = "mp4"
            else:
                # 4K / 1440p / Maximum available stream
                format_selector = (
                    "bestvideo[height>=2160]+bestaudio/"
                    "bestvideo[height>=1440]+bestaudio/"
                    "bestvideo[height>=1080]+bestaudio/"
                    "bestvideo[height>=720]+bestaudio/"
                    "bestvideo+bestaudio"
                )
                merge_fmt = "mkv"

            ydl_opts = self._build_ytdlp_opts(
                eff_start, eff_end, temp_tmpl, format_selector, merge_fmt
            )

            t0 = time.time()
            self.log(f"[{clip.index:02d}] Slicing and downloading high-bitrate stream range...")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not clip.video_id and info:
                    clip.video_id = info.get('id')
                if not clip.video_title and info:
                    clip.video_title = info.get('title')

            # Locate downloaded temporary file
            downloaded_temp = None
            for ext in ['mkv', 'mp4', 'webm', 'mov']:
                candidate = f"{temp_base}.{ext}"
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    downloaded_temp = candidate
                    break

            if not downloaded_temp:
                raise RuntimeError("yt-dlp completed but downloaded clip file was not found.")

            dl_time = time.time() - t0
            self.log(f"[{clip.index:02d}] Stream section downloaded in {dl_time:.2f}s.")

            # Step 4: Handle Transcoding / Remuxing to Final Edit-Ready Format
            clip.status = "Transcoding"
            self.clip_status_callback(clip.index, "Transcoding", None)

            if self.profile == PROFILE_STREAM_COPY:
                # Native stream copy (fastest)
                if downloaded_temp.endswith('.mp4'):
                    shutil.move(downloaded_temp, final_filepath)
                else:
                    self.log(f"[{clip.index:02d}] Remuxing streams into final container...")
                    cmd = [
                        self.ffmpeg_exe, "-y",
                        "-i", downloaded_temp,
                        "-c", "copy",
                        final_filepath
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode != 0:
                        mkv_final = os.path.splitext(final_filepath)[0] + ".mkv"
                        shutil.move(downloaded_temp, mkv_final)
                        final_filepath = mkv_final
                    else:
                        if os.path.exists(downloaded_temp):
                            os.remove(downloaded_temp)

            elif self.profile == PROFILE_1080P_H264:
                # 1080p native H.264
                if downloaded_temp.endswith('.mp4'):
                    shutil.move(downloaded_temp, final_filepath)
                else:
                    cmd = [
                        self.ffmpeg_exe, "-y",
                        "-i", downloaded_temp,
                        "-c", "copy",
                        final_filepath
                    ]
                    subprocess.run(cmd, capture_output=True, text=True)
                    if os.path.exists(downloaded_temp):
                        os.remove(downloaded_temp)

            elif self.profile == PROFILE_PRORES:
                # Apple ProRes 422 (HQ)
                self.log(f"[{clip.index:02d}] Transcoding to Apple ProRes 422 (HQ)...")
                cmd = [
                    self.ffmpeg_exe, "-y",
                    "-i", downloaded_temp,
                    "-c:v", "prores_ks", "-profile:v", "3",
                    "-vendor", "apl0", "-pix_fmt", "yuv422p10le",
                    "-c:a", "pcm_s16le",
                    final_filepath
                ]
                subprocess.run(cmd, capture_output=True, text=True)
                if os.path.exists(downloaded_temp):
                    os.remove(downloaded_temp)

            else:
                # Default: 4K / 2160p -> Edit-Ready H.264 MP4 (NVENC GPU Accelerated with CPU Fallback)
                transcoded = False
                if self.nvenc_available:
                    self.log(f"[{clip.index:02d}] Transcoding to Premiere-ready H.264 via GPU NVENC (cq 18)...")
                    cmd_nvenc = [
                        self.ffmpeg_exe, "-y",
                        "-i", downloaded_temp,
                        "-c:v", "h264_nvenc", "-cq", "18", "-preset", "p4",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "320k",
                        final_filepath
                    ]
                    res = subprocess.run(cmd_nvenc, capture_output=True, text=True)
                    if res.returncode == 0:
                        transcoded = True
                    else:
                        self.log(f"[{clip.index:02d}] NVENC notice: {res.stderr[-200:]}. Falling back to CPU libx264...")

                if not transcoded:
                    self.log(f"[{clip.index:02d}] Transcoding to Premiere-ready H.264 via CPU libx264 (CRF 18)...")
                    cmd_cpu = [
                        self.ffmpeg_exe, "-y",
                        "-i", downloaded_temp,
                        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "320k",
                        final_filepath
                    ]
                    res = subprocess.run(cmd_cpu, capture_output=True, text=True)
                    if res.returncode != 0:
                        raise RuntimeError(f"FFmpeg CPU transcode failed: {res.stderr[-300:]}")

                if os.path.exists(downloaded_temp):
                    os.remove(downloaded_temp)

            # Step 5: Post-Download FFprobe Verification of actual output dimensions
            if os.path.exists(final_filepath) and os.path.getsize(final_filepath) > 0:
                width, height, codec, fps = self.verify_output_file_dimensions(final_filepath)
                file_size_mb = os.path.getsize(final_filepath) / (1024 * 1024)

                if height >= 2160:
                    quality_badge = f"4K UHD ({width}x{height})"
                elif height >= 1440:
                    quality_badge = f"1440p QHD ({width}x{height})"
                elif height >= 1080:
                    quality_badge = f"1080p FHD ({width}x{height})"
                elif height >= 720:
                    quality_badge = f"720p HD ({width}x{height})"
                else:
                    quality_badge = f"{width}x{height}"

                clip.status = "Completed"
                clip.output_filepath = final_filepath
                clip.quality_label = quality_badge
                self.quality_callback(clip.index, quality_badge)

                self.log(
                    f"[{clip.index:02d}] [VERIFIED HIGH QUALITY OUTPUT]:\n"
                    f"    Resolution: {quality_badge} @ {fps:.0f}fps | Codec: {codec.upper()}\n"
                    f"    Saved File: {os.path.basename(final_filepath)} ({file_size_mb:.2f} MB)"
                )
                self.clip_status_callback(clip.index, "Completed", final_filepath)
                return True
            else:
                raise RuntimeError("Output file was not produced or is 0 bytes.")

        except Exception as e:
            clip.status = "Failed"
            clip.error_message = str(e)
            self.log(f"[{clip.index:02d}] ERROR: Failed processing clip '{clip.beat_label}': {e}")
            self.clip_status_callback(clip.index, "Failed", None)
            for ext in ['mkv', 'mp4', 'webm', 'mov']:
                candidate = f"{temp_base}.{ext}"
                if os.path.exists(candidate):
                    try:
                        os.remove(candidate)
                    except Exception:
                        pass
            return False

    def run_batch(self, clips: List[ParsedClip]):
        """Run batch processing across all passed clips."""
        total = len(clips)
        if total == 0:
            self.log("[System] No clips in queue to process.")
            self.progress_callback(1.0, "Queue empty")
            return

        self.log(
            f"==================================================\n"
            f"Starting High-Quality Batch Harvest: {total} clips\n"
            f"Output Directory: {self.output_dir}\n"
            f"Quality Profile: {self.profile}\n"
            f"Handle Padding: +{self.padding_seconds}s\n"
            f"Strict 4K/1080p Mode: {'Enabled (Reject < 1080p)' if self.min_quality_strict else 'Disabled (Best Available)'}\n"
            f"NVENC Hardware Acceleration: {'Active' if self.nvenc_available else 'CPU Fallback'}\n"
            f"=================================================="
        )

        completed_count = 0
        failed_count = 0

        for idx, clip in enumerate(clips, 1):
            if self.stop_requested.is_set():
                self.log("\n[System] Batch harvest stopped by user.")
                break

            pct = (idx - 1) / total
            self.progress_callback(pct, f"Processing clip {idx}/{total}: {clip.beat_label}")

            success = self.process_single_clip(clip)
            if success:
                completed_count += 1
            else:
                failed_count += 1

        self.progress_callback(1.0, "Batch complete")
        self.log(
            f"\n==================================================\n"
            f"Batch Finished! Completed: {completed_count}/{total}, "
            f"Failed: {failed_count}, "
            f"Cancelled/Skipped: {total - completed_count - failed_count}\n"
            f"Clips are ready in: {self.output_dir}\n"
            f"=================================================="
        )
