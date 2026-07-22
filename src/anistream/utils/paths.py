from __future__ import annotations

import re
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_user_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def safe_component(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    return cleaned or fallback


def media_directory(download_root: Path, title: str, season: str, language: str) -> Path:
    return download_root / safe_component(title) / safe_component(season) / safe_component(language)
