from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anistream.utils.paths import data_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "watch_history.json"
        self._data = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(key): value for key, value in data.items() if isinstance(value, dict)}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def key(self, provider_id: str, catalogue_url: str) -> str:
        return f"{provider_id}:{catalogue_url.rstrip('/').lower()}"

    def get(self, provider_id: str, catalogue_url: str) -> dict[str, Any] | None:
        item = self._data.get(self.key(provider_id, catalogue_url))
        return dict(item) if item else None

    def update(
        self,
        *,
        provider_id: str,
        provider_name: str,
        catalogue_url: str,
        title: str,
        season: str,
        language: str,
        episode: int,
        total_episodes: int,
        position: float,
        duration: float,
        completed: bool,
    ) -> None:
        key = self.key(provider_id, catalogue_url)
        previous = self._data.get(key, {})
        seen = {int(number) for number in previous.get("seen_episodes", []) if str(number).isdigit()}
        total = _positive_int(total_episodes)
        current = min(total, _positive_int(episode))
        if previous.get("status") == "completed":
            seen = set()
        if completed:
            seen.add(current)
        title_completed = completed and current == total
        if completed and not title_completed:
            current = min(total, current + 1)
        self._data[key] = {
            "provider_id": provider_id,
            "provider_name": provider_name,
            "catalogue_url": catalogue_url,
            "title": title,
            "season": season,
            "language": language,
            "media_type": "movie" if total == 1 else "series",
            "total_episodes": total,
            "current_episode": current,
            "position": 0.0 if completed else max(0.0, position),
            "duration": max(0.0, duration),
            "status": "completed" if title_completed else "in_progress",
            "seen_episodes": sorted(seen),
            "updated_at": _now(),
        }
        self._save()

    def sync_catalogue(
        self,
        *,
        provider_id: str,
        provider_name: str,
        catalogue_url: str,
        title: str,
        season: str,
        language: str,
        total_episodes: int,
    ) -> dict[str, Any] | None:
        """Refresh catalogue metadata without changing its last-watched time."""
        key = self.key(provider_id, catalogue_url)
        previous = self._data.get(key)
        if previous is None:
            return None

        total = _positive_int(total_episodes)
        seen = sorted(
            {
                int(number)
                for number in previous.get("seen_episodes", [])
                if str(number).isdigit() and 1 <= int(number) <= total
            }
        )
        current = min(total, _positive_int(previous.get("current_episode", 1)))
        refreshed = dict(previous)
        refreshed.update(
            {
                "provider_id": provider_id,
                "provider_name": provider_name,
                "catalogue_url": catalogue_url,
                "title": title,
                "season": season,
                "language": language,
                "media_type": "movie" if total == 1 else "series",
                "total_episodes": total,
                "current_episode": current,
                "status": "completed" if total in seen else "in_progress",
                "seen_episodes": seen,
            }
        )
        if refreshed != previous:
            self._data[key] = refreshed
            self._save()
        return dict(refreshed)

    def all(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(value) for value in self._data.values() if value.get("catalogue_url")),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )

    def clear(self) -> None:
        self._data = {}
        self._save()
        shutil.rmtree(data_dir() / "mpv_state", ignore_errors=True)
