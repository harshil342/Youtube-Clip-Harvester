# 🎬 LLM Clip Harvester

**Production-ready desktop application for video editors** that turns raw LLM beat sheets into Premiere Pro & After Effects edit-ready clips. Slices only specific timestamp ranges in maximum available resolution (4K / 1440p / 1080p) without downloading full videos, and names them sequentially by beat.

---

## ⚡ Key Features

- **Guaranteed 4K & 1080p Stream Prioritization:** Enforces full separate DASH streams (`bestvideo+bestaudio`) and completely bans legacy pre-muxed low-resolution fallbacks (like 360p or 480p).
- **Strict Quality Enforcement Mode:** Checkbox in the GUI (`Strict 4K / 1080p Only`) that automatically inspects creator resolutions and rejects/skips any videos that do not meet the 1080p/4K threshold, ensuring only pristine footage enters your project.
- **Pre-Flight Stream Quality Scanner:** Displays verified resolution and frame rates (e.g., `4K UHD (2160p@60fps)`, `1080p FHD`) in the Queue Table *before* starting the batch.
- **Post-Download FFprobe Verification:** Automatically probes every produced file with `ffprobe` to verify exact pixel dimensions (e.g. `3840x2160`) and logs the verified quality.
- **Hardware-Accelerated Premiere Transcoding:**
  - `4K / 2160p Priority -> Edit-Ready H.264 MP4 (NVENC GPU Accelerated)`: Slices highest bitrate streams and transcodes with NVIDIA NVENC (`h264_nvenc -cq 18 -c:a aac`) so 4K VP9/AV1 files play smoothly in Premiere Pro timelines without offline proxies.
  - Automatic graceful fallback to CPU `libx264 (CRF 18)` if NVENC is unavailable.
  - `4K / 2160p Priority (Native Stream Copy - MKV/MP4)`: Instant stream copy without re-encoding.
  - `1080p Native H.264 MP4 (AVC1 + AAC)`: Native AVC1/AAC direct slice.
  - `Apple ProRes 422 (HQ) 4K/1080p MOV`: Mastering/broadcast grade.
- **Headless Browser Search Fallback & Bot Bypass (`playwright`):**
  - If a pasted line has a search query (e.g. `"tokyo drift 4k scene"`) instead of a URL, headless Chromium automatically searches YouTube with the 4K filter (`sp=EgJwAQ%253D%253D`) and grabs the top matching video.
  - Automatically exports fresh session cookies to bypass consent and bot challenges.
- **Configurable Handle Padding:** Add `+0s`, `+1s`, `+2s`, `+3s`, or `+5s` handles before and after cuts for seamless video transition editing.
- **Premiere-Sortable File Naming:**
  `{Index:02d}_{Sanitized_Beat_Label}_{VideoID}_{Start}-{End}.mp4`  
  *(e.g., `01_Hook_WhiteVision_H_7QCxsk8hg_0m49s-0m55s.mp4`)*
- **Non-blocking CustomTkinter UI:** Responsive dark-mode dashboard with interactive queue table, row deletion, dual progress bars, and live console logging.

---

## 📁 Project Architecture

```
Youtube automated clips extractor automation/
├── main.py             # Entry point with dependency checks & Windows High-DPI scaling
├── gui.py              # CustomTkinter Dark Mode dashboard with interactive queue & live terminal
├── downloader.py       # yt-dlp section downloader, FFmpeg NVENC/libx264 transcoder & quality inspector
├── browser_agent.py    # Playwright headless Chromium agent for YouTube 4K search & cookie bypass
├── parser.py           # Smart regex & JSON parser for LLM outputs
├── requirements.txt    # Python dependencies
└── README.md           # Documentation
```

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

> **Note on FFmpeg:** Ensure `ffmpeg` and `ffprobe` are installed and available in your PATH.  
> On Windows with Chocolatey: `choco install ffmpeg`  
> On Windows with Winget: `winget install Gyan.FFmpeg`

### 2. Launch the Application
```bash
python main.py
```

---

## 📝 Supported LLM Input Formats

The parser automatically detects and handles multiple formats:

### 1. Pipe-Separated Format (Default)
```text
01_Intro_Hook | https://www.youtube.com/watch?v=aqz-KE-bpKQ | 0:08-0:10, 0:49-0:55, 1:11-1:15
02_Car_Chase | "fast and furious tokyo drift 4k" | 1:15-1:30
```

### 2. Markdown Tables
```markdown
| Beat Label | Target Video / Search Query | Timestamps |
| --- | --- | --- |
| 01_Hero_Entrance | https://www.youtube.com/watch?v=H_7QCxsk8hg | 0:10–0:25 |
| 02_Cyberpunk_City | "blender tears of steel 4k" | 01:05 to 01:20 |
```

### 3. Freeform Markdown Bullet Lists
```markdown
* 01_Intro: https://www.youtube.com/watch?v=aqz-KE-bpKQ
  * 0:15-0:30, 0:45-0:55
- Beat 02 Explosion: https://youtu.be/H_7QCxsk8hg (1:10 - 1:25)
```

### 4. JSON Format
```json
[
  {
    "beat": "01_Intro_Hook",
    "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
    "timestamps": ["0:08-0:10", "0:49-0:55"]
  },
  {
    "beat": "02_Drift_Scene",
    "query": "tokyo drift 4k scene",
    "timestamps": ["1:15-1:30"]
  }
]
```

---

## ⚙️ Technical Details

### Handle Padding
When video editing, having extra frames before and after a clip is critical for creating dissolves, cross-fades, and motion transitions. Selecting `+1s` automatically calculates:
- `Effective Start = max(0.0, Start - 1.0s)`
- `Effective End = End + 1.0s`

### Hardware Acceleration (NVENC)
4K YouTube streams are encoded in VP9 or AV1, which cause timeline lag in Adobe Premiere Pro on Windows. The default profile automatically re-encodes the downloaded slice to high-bitrate H.264 using NVIDIA NVENC (`-c:v h264_nvenc -cq 18 -preset p4 -c:a aac -b:a 320k`). If an NVIDIA GPU is not detected, the app automatically falls back to CPU `libx264`.
