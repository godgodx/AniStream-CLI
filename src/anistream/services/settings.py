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
    # An opt-out list keeps every current and future provider enabled by default.
    "disabled_providers": [],
    "providers": {
        "anime_sama": {"user_agent": "", "cf_clearance": ""},
    },
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
                        if key == "providers" and isinstance(value, dict):
                            for provider_id, provider_value in value.items():
                                if isinstance(provider_value, dict):
                                    data["providers"].setdefault(str(provider_id), {}).update(provider_value)
                        elif key == "anime_sama" and isinstance(value, dict):
                            # Migrate settings written before provider configuration was namespaced.
                            data["providers"]["anime_sama"].update(value)
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
        providers = self._data.get("providers", {})
        value = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    def set_provider_settings(self, provider_id: str, value: dict[str, Any]) -> None:
        normalized = provider_id.strip()
        if not normalized:
            raise ValueError("Provider id must not be empty")
        providers = self._data.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            self._data["providers"] = providers
        providers[normalized] = dict(value)
        self.save()

    def provider_enabled(self, provider_id: str) -> bool:
        normalized = provider_id.strip()
        if not normalized:
            return False
        disabled = self._data.get("disabled_providers", [])
        if not isinstance(disabled, list):
            return True
        return normalized not in {str(item) for item in disabled}

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        normalized = provider_id.strip()
        if not normalized:
            raise ValueError("Provider id must not be empty")
        disabled = self._data.get("disabled_providers", [])
        normalized_disabled = {
            str(item).strip()
            for item in disabled
            if isinstance(disabled, list) and str(item).strip()
        }
        if enabled:
            normalized_disabled.discard(normalized)
        else:
            normalized_disabled.add(normalized)
        self._data["disabled_providers"] = sorted(normalized_disabled)
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
        if located and not self._inside_project(Path(located)):
            return str(Path(located).resolve())
        for candidate in self._platform_executable_candidates(command):
            if candidate.is_file():
                return str(candidate.resolve())
        return None

    @staticmethod
    def _inside_project(path: Path) -> bool:
        try:
            path.resolve().relative_to(project_root().resolve())
        except ValueError:
            return False
        return True

    @staticmethod
    def _platform_executable_candidates(command: str) -> tuple[Path, ...]:
        if os.name != "nt" or command.lower().removesuffix(".exe") != "mpv":
            return ()

        candidates: list[Path] = []
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
