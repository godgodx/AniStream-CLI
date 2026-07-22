from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SearchResult:
    provider_id: str
    provider_name: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class CatalogueVariant:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class EmbedCandidate:
    player: str
    url: str


@dataclass(frozen=True, slots=True)
class Episode:
    number: int
    candidates: tuple[EmbedCandidate, ...]


@dataclass(frozen=True, slots=True)
class Catalogue:
    provider_id: str
    provider_name: str
    title: str
    url: str
    season: str
    language: str
    episodes: tuple[Episode, ...]


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    url: str
    embed_url: str
    resolver_name: str
    headers: Mapping[str, str] = field(default_factory=dict)
    kind: str = "unknown"


@dataclass(slots=True)
class SourceAttempt:
    player: str
    embed_host: str
    resolver: str | None = None
    success: bool = False
    detail: str = ""


@dataclass(slots=True)
class DownloadResult:
    episode: int
    success: bool
    output: Path | None = None
    source: str | None = None
    validation: str = "not run"
    attempts: list[SourceAttempt] = field(default_factory=list)
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class ProbeResult:
    valid: bool
    kind: str = "unknown"
    detail: str = ""
