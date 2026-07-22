from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from anistream.utils.paths import data_dir, project_root, resolve_user_path


DEFAULTS: dict[str, Any] = {
    "download_directory": "downloads",
    "download_mode": None,
    "parallel_downloads": 3,
    "ffmpeg_path": None,
    "ffprobe_path": None,
    "mpv_path": None,
    "watch_display": None,
    "anime_sama": {"user_agent": "", "cf_clearance": ""},
}


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "settings.json"
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        data = deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                incoming = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(incoming, dict):
                    for key, value in incoming.items():
                        if key == "anime_sama" and isinstance(value, dict):
                            data[key].update(value)
                        elif key in data:
                            data[key] = value
            except (OSError, json.JSONDecodeError):
                pass
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown setting: {key}")
        self._data[key] = value
        self.save()

    def provider_settings(self, provider_id: str) -> dict[str, Any]:
        value = self._data.get(provider_id, {})
        return dict(value) if isinstance(value, dict) else {}

    def set_provider_settings(self, provider_id: str, value: dict[str, Any]) -> None:
        if provider_id not in DEFAULTS:
            raise KeyError(f"Unknown provider: {provider_id}")
        self._data[provider_id] = value
        self.save()

    def download_directory(self) -> Path:
        return resolve_user_path(str(self._data["download_directory"]))

    def executable(self, key: str, command: str) -> str | None:
        configured = self._data.get(key)
        if configured:
            candidate = Path(str(configured)).expanduser()
            if candidate.exists():
                return str(candidate.resolve())
            located = shutil.which(str(configured))
            if located:
                return located
        located = shutil.which(command)
        if located:
            return located
        for candidate in self._platform_executable_candidates(command):
            if candidate.is_file():
                return str(candidate.resolve())
        return None

    @staticmethod
    def _platform_executable_candidates(command: str) -> tuple[Path, ...]:
        if os.name != "nt" or command.lower().removesuffix(".exe") != "mpv":
            return ()

        candidates = [
            project_root() / "mpv.exe",
            project_root() / "tools" / "mpv" / "mpv.exe",
        ]
        program_files = os.environ.get("ProgramFiles")
        local_app_data = os.environ.get("LOCALAPPDATA")
        user_profile = os.environ.get("USERPROFILE")
        chocolatey = os.environ.get("ChocolateyInstall")
        if program_files:
            candidates.extend(
                [
                    Path(program_files) / "MPV Player" / "mpv.exe",
                    Path(program_files) / "mpv" / "mpv.exe",
                ]
            )
        if local_app_data:
            candidates.extend(
                [
                    Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "mpv.exe",
                    Path(local_app_data) / "Programs" / "mpv" / "mpv.exe",
                ]
            )
        if user_profile:
            candidates.append(Path(user_profile) / "scoop" / "apps" / "mpv" / "current" / "mpv.exe")
        if chocolatey:
            candidates.append(Path(chocolatey) / "bin" / "mpv.exe")
        return tuple(candidates)

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)
