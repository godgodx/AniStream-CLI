from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    detail: str
    duration: float = 0.0
    codec: str = "unknown"
    resolution: str = "unknown"


class MediaValidator:
    allowed_formats = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
    rejected_codecs = {"png", "apng", "mjpeg", "gif", "bmp"}

    def __init__(self, ffprobe_path: str) -> None:
        self.ffprobe_path = ffprobe_path

    def validate(self, path: Path) -> ValidationResult:
        if path.suffix.lower() != ".mp4":
            return ValidationResult(False, "output extension is not .mp4")
        if not path.exists() or path.stat().st_size < 1024:
            return ValidationResult(False, "output file is missing or too small")
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_type,codec_name,width,height,duration",
            "-of",
            "json",
            str(path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "ffprobe failed"
            return ValidationResult(False, detail)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ValidationResult(False, "ffprobe returned invalid JSON")

        format_info = payload.get("format", {})
        formats = {item.strip() for item in str(format_info.get("format_name", "")).split(",")}
        if not formats.intersection(self.allowed_formats):
            return ValidationResult(False, f"unexpected container: {','.join(sorted(formats)) or 'unknown'}")
        video = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), None)
        if not video:
            return ValidationResult(False, "no video stream")
        codec = str(video.get("codec_name", "unknown"))
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        if codec.lower() in self.rejected_codecs or width < 16 or height < 16:
            return ValidationResult(False, f"invalid video stream: {codec} {width}x{height}", codec=codec)
        try:
            duration = float(format_info.get("duration") or video.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            return ValidationResult(False, "media duration is missing or zero", duration, codec, f"{width}x{height}")
        if duration < 10:
            return ValidationResult(False, f"implausible duration: {duration:.1f}s", duration, codec, f"{width}x{height}")
        detail = f"MP4, {codec}, {width}x{height}"
        detail += f", {duration / 60:.1f} min"
        return ValidationResult(True, detail, duration, codec, f"{width}x{height}")
