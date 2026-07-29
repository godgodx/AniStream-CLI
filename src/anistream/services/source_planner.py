from __future__ import annotations

import time
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

from anistream.models import Catalogue, EmbedCandidate, ResolvedMedia
from anistream.resolvers.base import hostname
from anistream.resolvers.registry import ResolverRegistry
from anistream.services.media_probe import RemoteMediaProbe
from anistream.services.source_health import SourceHealthTracker


ProgressCallback = Callable[[str], None]
PLAN_DEADLINE_SECONDS = 60.0
PLAYER_PREFLIGHT_DEADLINE_SECONDS = 30.0


@dataclass(slots=True)
class PreflightResult:
    episode: int
    candidate: EmbedCandidate
    media: ResolvedMedia | None
    valid: bool
    detail: str


@dataclass(slots=True)
class SourcePlan:
    primary_player: str | None
    routes: dict[int, list[EmbedCandidate]]
    cache: dict[tuple[int, str], ResolvedMedia] = field(default_factory=dict)
    preflight: list[PreflightResult] = field(default_factory=list)
    verified_episodes: tuple[int, ...] = ()
    missing_episodes: tuple[int, ...] = ()
    players_used: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing_episodes


class SourcePlanner:
    def __init__(
        self,
        resolvers: ResolverRegistry,
        probe: RemoteMediaProbe,
        max_workers: int = 6,
        source_health: SourceHealthTracker | None = None,
    ) -> None:
        self.resolvers = resolvers
        self.probe = probe
        self.max_workers = max(1, max_workers)
        self.source_health = source_health or SourceHealthTracker()

    def plan(
        self,
        catalogue: Catalogue,
        episode_numbers: list[int],
        progress: ProgressCallback | None = None,
    ) -> SourcePlan:
        selected = {episode.number: episode for episode in catalogue.episodes if episode.number in episode_numbers}
        if len(selected) != len(set(episode_numbers)):
            missing = sorted(set(episode_numbers) - set(selected))
            raise ValueError(f"Unknown episode numbers: {missing}")

        deadline = time.monotonic() + PLAN_DEADLINE_SECONDS
        players: list[str] = []
        for episode in selected.values():
            candidates = list(episode.candidates)
            ranking = self.source_health.rank_urls(
                [candidate.url for candidate in candidates]
            )
            for index in ranking:
                candidate = candidates[index]
                if candidate.player not in players:
                    players.append(candidate.player)

        cache: dict[tuple[int, str], ResolvedMedia] = {}
        records: list[PreflightResult] = []
        primary: str | None = None

        for player in players:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                records.extend(
                    PreflightResult(
                        number,
                        EmbedCandidate(player, ""),
                        None,
                        False,
                        "source planning deadline exceeded",
                    )
                    for number in selected
                )
                break
            candidates = {
                number: next((item for item in episode.candidates if item.player == player), None)
                for number, episode in selected.items()
            }
            missing = [number for number, candidate in candidates.items() if candidate is None]
            records.extend(
                PreflightResult(number, EmbedCandidate(player, ""), None, False, "episode missing from player")
                for number in missing
            )
            available = {number: candidate for number, candidate in candidates.items() if candidate is not None}
            if not available:
                continue
            if progress:
                coverage = (
                    f"{len(available)}/{len(selected)} selected episode(s)"
                    if missing
                    else f"{len(available)} episode(s)"
                )
                progress(f"Checking {player} across {coverage}...")
            current = self._check_player(
                available,
                timeout=min(remaining, PLAYER_PREFLIGHT_DEADLINE_SECONDS),
            )
            records.extend(current)
            for result in current:
                if result.valid and result.media:
                    cache[(result.episode, result.candidate.url)] = result.media
            if not missing and len(current) == len(selected) and all(item.valid for item in current):
                primary = player
                break

        routes: dict[int, list[EmbedCandidate]] = {}
        for number, episode in selected.items():
            ordered = list(episode.candidates)
            ranking = self.source_health.rank_urls(
                [candidate.url for candidate in ordered]
            )
            ordered = [ordered[index] for index in ranking]
            if primary:
                ordered.sort(key=lambda item: 0 if item.player == primary else 1)
            else:
                ordered.sort(key=lambda item: 0 if (number, item.url) in cache else 1)
            routes[number] = [candidate for candidate in ordered if self.resolvers.supports(candidate.url)]
        verified = tuple(
            sorted(
                number
                for number in selected
                if any((number, candidate.url) in cache for candidate in selected[number].candidates)
            )
        )
        missing = tuple(sorted(set(selected) - set(verified)))
        players_used = tuple(
            player
            for player in players
            if any(result.valid and result.candidate.player == player for result in records)
        )
        return SourcePlan(primary, routes, cache, records, verified, missing, players_used)

    def _check_player(
        self,
        candidates: dict[int, EmbedCandidate],
        *,
        timeout: float,
    ) -> list[PreflightResult]:
        results: list[PreflightResult] = []
        pool = ThreadPoolExecutor(max_workers=min(self.max_workers, len(candidates)))
        try:
            pending = {
                pool.submit(self._resolve_and_probe, number, candidate): (number, candidate)
                for number, candidate in candidates.items()
            }
            done, unfinished = wait(
                pending,
                timeout=max(0.001, timeout),
                return_when=ALL_COMPLETED,
            )
            for future in done:
                number, candidate = pending[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(PreflightResult(number, candidate, None, False, str(exc)))
            for future in unfinished:
                number, candidate = pending[future]
                future.cancel()
                results.append(
                    PreflightResult(
                        number,
                        candidate,
                        None,
                        False,
                        "source preflight deadline exceeded",
                    )
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        results.sort(key=lambda item: item.episode)
        return results

    def _resolve_and_probe(self, episode: int, candidate: EmbedCandidate) -> PreflightResult:
        resolver = self.resolvers.resolver_for(candidate.url)
        if resolver is None:
            return PreflightResult(episode, candidate, None, False, f"unsupported host: {hostname(candidate.url)}")
        started = time.monotonic()
        try:
            media = resolver.resolve(candidate.url)
            probe = self.probe.probe(media)
            latency = time.monotonic() - started
            self.source_health.bind(candidate.url, media.url)
            self.source_health.observe(
                candidate.url,
                latency_seconds=latency,
                success=probe.valid,
            )
            return PreflightResult(episode, candidate, media if probe.valid else None, probe.valid, probe.detail)
        except Exception as exc:
            self.source_health.observe(
                candidate.url,
                latency_seconds=time.monotonic() - started,
                success=False,
            )
            return PreflightResult(episode, candidate, None, False, str(exc))
