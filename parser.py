"""
Input Parser Module for LLM Clip Harvester.
Robust parsing of raw LLM outputs in various formats:
- Pipe-separated format: [Beat_Name] | [YouTube_URL_or_Search_Query] | [Timestamps]
- Markdown tables: | Beat | URL/Query | Timestamps |
- Bullet lists / Freeform text with URLs and timestamp ranges
- JSON structures with lists of clips or beats
- Search query fallback detection
- Support for multiple timestamp ranges per line (comma/semicolon separated)
- Support for en-dash (–), em-dash (—), hyphens (-), and "to" in timestamps
- Timestamp formats: SS, MM:SS, HH:MM:SS, with optional decimals
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ParsedClip:
    index: int
    beat_label: str
    target: str  # YouTube URL or Search Query
    is_search_query: bool
    start_seconds: float
    end_seconds: float
    start_str: str
    end_str: str
    original_line: str = ""
    resolved_url: Optional[str] = None
    video_id: Optional[str] = None
    video_title: Optional[str] = None
    quality_label: str = "Pending Check"
    status: str = "Ready"  # Ready, Searching, Slicing, Completed, Failed, Skipped
    error_message: Optional[str] = None
    output_filepath: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    def get_effective_range(self, padding_seconds: float = 0.0) -> Tuple[float, float]:
        """Return (start, end) with handle padding applied."""
        eff_start = max(0.0, self.start_seconds - padding_seconds)
        eff_end = self.end_seconds + padding_seconds
        return eff_start, eff_end


# Regex for YouTube URLs
YOUTUBE_URL_REGEX = re.compile(
    r'(https?://(?:www\.)?(?:youtube\.com/(?:watch\?(?:[^\s&]+&)*v=|embed/|v/|shorts/)|youtu\.be/)[a-zA-Z0-9_-]{11}(?:[^\s)]*)?)',
    re.IGNORECASE
)

# Regex for YouTube Video ID
YOUTUBE_ID_REGEX = re.compile(
    r'(?:v=|\/embed\/|\/v\/|\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})',
    re.IGNORECASE
)

# Regex to detect individual timestamp token (e.g. 01:23, 1:23:45, 45s, 45.5)
TIMESTAMP_TOKEN_REGEX = r'(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?s?'

# Regex to detect a timestamp range (e.g. 0:08-0:10, 01:20:00 – 01:20:30, 10s to 25s)
TIMESTAMP_RANGE_REGEX = re.compile(
    r'(\b(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?|\b\d+(?:\.\d+)?s?)\s*(?:-|–|—|to)\s*(\b(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?|\b\d+(?:\.\d+)?s?)',
    re.IGNORECASE
)


def parse_timestamp_to_seconds(ts_str: str) -> float:
    """
    Convert timestamp string to seconds.
    Supported:
      - "45" or "45s" -> 45.0
      - "1:30" or "01:30" -> 90.0
      - "1:02:15" -> 3735.0
      - "12.5" -> 12.5
    """
    cleaned = ts_str.strip().lower().rstrip('s')
    parts = cleaned.split(':')
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return 0.0
    return 0.0


def format_seconds_to_timestamp(seconds: float) -> str:
    """Convert float seconds to MM:SS or HH:MM:SS."""
    total_sec = int(round(seconds))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_seconds_for_filename(seconds: float) -> str:
    """Format seconds for Premiere sortable filename: e.g. 0m49s or 1h02m15s."""
    total_sec = int(round(seconds))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def sanitize_filename_label(label: str) -> str:
    """Sanitize beat label for clean Windows/Premiere filenames."""
    # Replace non-alphanumeric chars (except underscore and hyphen) with underscore
    cleaned = re.sub(r'[^\w\-]', '_', label.strip())
    # Collapse multiple consecutive underscores
    cleaned = re.sub(r'_+', '_', cleaned)
    # Strip leading/trailing underscores
    cleaned = cleaned.strip('_')
    return cleaned or "Clip"


def extract_video_id(url: str) -> Optional[str]:
    """Extract 11-char YouTube video ID from URL."""
    match = YOUTUBE_ID_REGEX.search(url)
    if match:
        return match.group(1)
    return None


def extract_all_timestamp_ranges(text: str) -> List[Tuple[float, float, str, str]]:
    """
    Extract all (start_sec, end_sec, start_str, end_str) from a text segment.
    Handles multiple comma/semicolon/newline-separated ranges.
    """
    results = []
    for match in TIMESTAMP_RANGE_REGEX.finditer(text):
        s_str = match.group(1).strip()
        e_str = match.group(2).strip()
        s_sec = parse_timestamp_to_seconds(s_str)
        e_sec = parse_timestamp_to_seconds(e_str)
        if e_sec > s_sec:
            results.append((s_sec, e_sec, s_str, e_str))
    return results


def parse_pipe_separated_line(line: str) -> Optional[Tuple[str, str, List[Tuple[float, float, str, str]]]]:
    """
    Try parsing a line in format:
    Beat_Name | URL_or_Query | Timestamps
    """
    parts = [p.strip() for p in line.split('|')]
    # Strip leading/trailing empty cells from markdown table syntax like | Col1 | Col2 | Col3 |
    if len(parts) >= 2 and parts[0] == "":
        parts.pop(0)
    if len(parts) >= 2 and parts[-1] == "":
        parts.pop(-1)

    if len(parts) >= 3:
        beat_name = parts[0]
        # Ignore markdown table separator lines like |---|---|---|
        if re.match(r'^[\-\:\s]+$', beat_name):
            return None
        target = parts[1]
        timestamp_part = " ".join(parts[2:])
        ranges = extract_all_timestamp_ranges(timestamp_part)
        if ranges:
            return beat_name, target, ranges
    return None


def parse_json_input(raw_text: str) -> Optional[List[ParsedClip]]:
    """Try parsing raw_text as JSON."""
    raw_text = raw_text.strip()
    if not ((raw_text.startswith('[') and raw_text.endswith(']')) or 
            (raw_text.startswith('{') and raw_text.endswith('}'))):
        return None

    try:
        data = json.loads(raw_text)
    except Exception:
        return None

    items = []
    if isinstance(data, dict):
        # Look for common container keys
        for key in ['clips', 'beats', 'data', 'items', 'timeline']:
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        if not items:
            items = [data]
    elif isinstance(data, list):
        items = data

    clips: List[ParsedClip] = []
    index_counter = 1

    for item in items:
        if not isinstance(item, dict):
            continue
        # Extract beat name
        beat = (
            item.get('beat') or item.get('beat_name') or item.get('name') or 
            item.get('label') or item.get('title') or f"Beat_{index_counter}"
        )
        # Extract URL or search query
        target = (
            item.get('url') or item.get('youtube_url') or item.get('link') or 
            item.get('query') or item.get('search_query') or ""
        )
        if not target:
            continue

        # Extract timestamps
        ranges = []
        raw_ts = item.get('timestamps') or item.get('timestamp') or item.get('ranges') or item.get('time')
        if isinstance(raw_ts, list):
            for t in raw_ts:
                ranges.extend(extract_all_timestamp_ranges(str(t)))
        elif isinstance(raw_ts, str):
            ranges.extend(extract_all_timestamp_ranges(raw_ts))
        elif 'start' in item and 'end' in item:
            s_sec = parse_timestamp_to_seconds(str(item['start']))
            e_sec = parse_timestamp_to_seconds(str(item['end']))
            if e_sec > s_sec:
                ranges.append((s_sec, e_sec, str(item['start']), str(item['end'])))

        target_url = target.strip()
        is_search = not bool(YOUTUBE_URL_REGEX.search(target_url))
        vid_id = extract_video_id(target_url) if not is_search else None

        for part_idx, (s_sec, e_sec, s_str, e_str) in enumerate(ranges, 1):
            sub_label = beat
            if len(ranges) > 1:
                sub_label = f"{beat}_part{part_idx}"
            clips.append(ParsedClip(
                index=index_counter,
                beat_label=sub_label,
                target=target_url,
                is_search_query=is_search,
                start_seconds=s_sec,
                end_seconds=e_sec,
                start_str=s_str,
                end_str=e_str,
                original_line=json.dumps(item),
                video_id=vid_id,
            ))
            index_counter += 1

    return clips if clips else None


def parse_llm_input(raw_text: str) -> List[ParsedClip]:
    """
    Main parser function that takes raw text or JSON and returns a list of ParsedClip items.
    """
    if not raw_text or not raw_text.strip():
        return []

    # 1. First attempt JSON parsing
    json_result = parse_json_input(raw_text)
    if json_result:
        return json_result

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    clips: List[ParsedClip] = []
    index_counter = 1

    # Pass 1: Line-by-line inspection (Pipe-separated or Markdown table or single-line bullets)
    i = 0
    while i < len(lines):
        line = lines[i]

        # Ignore markdown headers or pure separators
        if line.startswith('#') or re.match(r'^[=\-_]{3,}$', line):
            i += 1
            continue

        # Try pipe format first
        pipe_parsed = parse_pipe_separated_line(line)
        if pipe_parsed:
            beat_name, target, ranges = pipe_parsed
            target_clean = target.strip('`"\' ')
            is_search = not bool(YOUTUBE_URL_REGEX.search(target_clean))
            vid_id = extract_video_id(target_clean) if not is_search else None

            for part_idx, (s_sec, e_sec, s_str, e_str) in enumerate(ranges, 1):
                sub_label = beat_name
                if len(ranges) > 1:
                    sub_label = f"{beat_name}_p{part_idx}"
                clips.append(ParsedClip(
                    index=index_counter,
                    beat_label=sub_label,
                    target=target_clean,
                    is_search_query=is_search,
                    start_seconds=s_sec,
                    end_seconds=e_sec,
                    start_str=s_str,
                    end_str=e_str,
                    original_line=line,
                    video_id=vid_id
                ))
                index_counter += 1
            i += 1
            continue

        # Try Freeform / Markdown bullet point format
        # Check if current line contains a YouTube URL
        url_match = YOUTUBE_URL_REGEX.search(line)
        if url_match:
            yt_url = url_match.group(1).rstrip('.,;)]')
            # Extract beat label from preceding text if any (e.g. "- Beat 01 Intro: https://...")
            pre_url = line[:url_match.start()].strip()
            # Clean up bullet symbols (-, *, +, numbers)
            clean_beat = re.sub(r'^[\s\*\-\+\d\.\:\)]+', '', pre_url).strip()
            if not clean_beat or len(clean_beat) < 2:
                clean_beat = f"Beat_{index_counter}"

            # Check for timestamps on current line
            ranges = extract_all_timestamp_ranges(line)
            # If no timestamps on this line, check the next line or two (e.g. sub-bullets)
            if not ranges and i + 1 < len(lines):
                next_line = lines[i + 1]
                next_ranges = extract_all_timestamp_ranges(next_line)
                if next_ranges:
                    ranges = next_ranges
                    i += 1  # Consumed next line

            if ranges:
                vid_id = extract_video_id(yt_url)
                for part_idx, (s_sec, e_sec, s_str, e_str) in enumerate(ranges, 1):
                    sub_label = clean_beat
                    if len(ranges) > 1:
                        sub_label = f"{clean_beat}_p{part_idx}"
                    clips.append(ParsedClip(
                        index=index_counter,
                        beat_label=sub_label,
                        target=yt_url,
                        is_search_query=False,
                        start_seconds=s_sec,
                        end_seconds=e_sec,
                        start_str=s_str,
                        end_str=e_str,
                        original_line=line,
                        video_id=vid_id
                    ))
                    index_counter += 1
                i += 1
                continue

        # Check for freeform search query with timestamps on single line
        # e.g.: "01_Intro_Car | tokyo drift 4k scene | 0:10-0:20" or "Fast cars race - 0:15 to 0:30"
        ranges = extract_all_timestamp_ranges(line)
        if ranges:
            # Separate the timestamp part from the query/beat text
            # Remove the timestamp tokens from line
            cleaned_line = TIMESTAMP_RANGE_REGEX.sub('', line).strip()
            cleaned_line = re.sub(r'[\*\-\+\|]+', ' ', cleaned_line).strip()
            # If there are quotes, maybe it's a search query
            quotes = re.findall(r'["\']([^"\']+)["\']', cleaned_line)
            if quotes:
                query = quotes[0]
                beat_name = cleaned_line.replace(f'"{query}"', '').replace(f"'{query}'", '').strip() or f"Beat_{index_counter}"
            else:
                parts = [p.strip() for p in cleaned_line.split(':', 1)]
                if len(parts) == 2:
                    beat_name = parts[0]
                    query = parts[1]
                else:
                    beat_name = f"Beat_{index_counter}"
                    query = cleaned_line

            if query:
                for part_idx, (s_sec, e_sec, s_str, e_str) in enumerate(ranges, 1):
                    sub_label = beat_name
                    if len(ranges) > 1:
                        sub_label = f"{beat_name}_p{part_idx}"
                    clips.append(ParsedClip(
                        index=index_counter,
                        beat_label=sub_label,
                        target=query,
                        is_search_query=True,
                        start_seconds=s_sec,
                        end_seconds=e_sec,
                        start_str=s_str,
                        end_str=e_str,
                        original_line=line
                    ))
                    index_counter += 1

        i += 1

    return clips
