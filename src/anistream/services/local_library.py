from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from anistream.models import Catalogue, Episode, MediaLanguage
from anistream.utils.paths import media_directory


_EPISODE_FILE = re.compile(r"^Episode\s+(\d+)\.mp4$", re.IGNORECASE)


def _positive_int(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class LocalLibraryEntry:
    title: str
    season: str
    language: str
    folder: Path
    episodes: tuple[int, ...]
    history: Mapping[str, Any] | None = None

    @property
    def status(self) -> str:
        if not self.history:
            return "not_started"
        return "completed" if self.history.get("status") == "completed" else "in_progress"

    @property
    def resume_episode(self) -> int:
        if not self.episodes:
            return 1
        if not self.history or self.status == "completed":
            return self.episodes[0]
        current = _positive_int(self.history.get("current_episode", self.episodes[0]))
        if current in self.episodes:
            return current
        return next((number for number in self.episodes if number >= current), self.episodes[-1])

    def catalogue(self) -> Catalogue:
        history = self.history or {}
        total = max(
            self.episodes[-1],
            _positive_int(history.get("total_episodes", self.episodes[-1])),
        )
        language_code = str(history.get("language_code") or "").strip()
        if not language_code:
            language_code = re.sub(r"[^a-z0-9]+", "-", self.language.casefold()).strip("-") or "local"
        identity = hashlib.sha256(
            f"{self.title}|{self.season}|{self.language}".casefold().encode("utf-8")
        ).hexdigest()[:24]
        return Catalogue(
            provider_id=str(history.get("provider_id") or "local"),
            provider_name=str(history.get("provider_name") or "Local library"),
            title=str(history.get("title") or self.title),
            url=str(history.get("catalogue_url") or f"local://library/{identity}"),
            season=str(history.get("season") or self.season),
            language=MediaLanguage(language_code, str(history.get("language") or self.language)),
            episodes=tuple(Episode(number, ()) for number in range(1, total + 1)),
        )


class LocalLibrary:
    def __init__(self, download_root: Path) -> None:
        self.download_root = download_root.resolve()

    def scan(self, history_entries: Iterable[Mapping[str, Any]]) -> list[LocalLibraryEntry]:
        if not self.download_root.is_dir():
            return []
        histories = tuple(dict(item) for item in history_entries)
        entries: list[LocalLibraryEntry] = []
        for title_dir in self._directories(self.download_root):
            for season_dir in self._directories(title_dir):
                for language_dir in self._directories(season_dir):
                    episodes = self._episodes(language_dir)
                    if not episodes:
                        continue
                    history = self._matching_history(language_dir, histories)
                    entries.append(
                        LocalLibraryEntry(
                            title=title_dir.name,
                            season=season_dir.name,
                            language=language_dir.name,
                            folder=language_dir,
                            episodes=episodes,
                            history=history,
                        )
                    )
        return sorted(
            entries,
            key=lambda item: (
                {"in_progress": 0, "not_started": 1, "completed": 2}[item.status],
                item.title.casefold(),
                item.season.casefold(),
                item.language.casefold(),
            ),
        )

    def _directories(self, parent: Path) -> list[Path]:
        try:
            children = list(parent.iterdir())
        except OSError:
            return []
        directories: list[Path] = []
        for child in children:
            try:
                resolved = child.resolve()
                resolved.relative_to(self.download_root)
                if child.is_dir():
                    directories.append(child)
            except (OSError, RuntimeError, ValueError):
                continue
        return sorted(directories, key=lambda path: path.name.casefold())

    @staticmethod
    def _episodes(folder: Path) -> tuple[int, ...]:
        numbers: set[int] = set()
        try:
            files = list(folder.iterdir())
        except OSError:
            return ()
        for path in files:
            try:
                if not path.is_file():
                    continue
            except (OSError, RuntimeError):
                continue
            match = _EPISODE_FILE.fullmatch(path.name)
            if match and int(match.group(1)) > 0:
                numbers.add(int(match.group(1)))
        return tuple(sorted(numbers))

    def _matching_history(
        self,
        folder: Path,
        histories: tuple[dict[str, Any], ...],
    ) -> dict[str, Any] | None:
        matches: list[dict[str, Any]] = []
        resolved_folder = folder.resolve()
        for item in histories:
            title = str(item.get("title") or "")
            season = str(item.get("season") or "")
            language = str(item.get("language") or "")
            if not title or not season or not language:
                continue
            candidate = media_directory(self.download_root, title, season, language)
            try:
                if candidate.resolve() == resolved_folder:
                    matches.append(item)
            except (OSError, RuntimeError):
                continue
        if not matches:
            return None
        return max(matches, key=lambda item: str(item.get("updated_at") or ""))
