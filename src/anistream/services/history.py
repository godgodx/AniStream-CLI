from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anistream.utils.paths import data_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "watch_history.json"
        self._data = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
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
        catalogue_url: str,
        title: str,
        season: str,
        language: str,
        episode: int,
        position: float,
        duration: float,
        completed: bool,
    ) -> None:
        key = self.key(provider_id, catalogue_url)
        previous = self._data.get(key, {})
        seen = {int(number) for number in previous.get("seen_episodes", []) if str(number).isdigit()}
        if completed:
            seen.add(episode)
        self._data[key] = {
            "provider_id": provider_id,
            "catalogue_url": catalogue_url,
            "title": title,
            "season": season,
            "language": language,
            "current_episode": episode + 1 if completed else episode,
            "position": 0.0 if completed else max(0.0, position),
            "duration": max(0.0, duration),
            "status": "completed" if completed else "in_progress",
            "seen_episodes": sorted(seen),
            "updated_at": _now(),
        }
        self._save()

    def all(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(value) for value in self._data.values()),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )

    def clear(self) -> None:
        self._data = {}
        self._save()
        shutil.rmtree(data_dir() / "mpv_state", ignore_errors=True)
