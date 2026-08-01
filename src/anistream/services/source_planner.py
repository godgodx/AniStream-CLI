from __future__ import annotations

import time
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, ThreadPoolExecutor, wait
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
WATCH_PREPARATION_DEADLINE_SECONDS = 40.0
WATCH_CANDIDATE_DEADLINE_SECONDS = 20.0
# Watch only needs the first verified source. Three concurrent candidates keep
# startup responsive without multiplying resolver and probe memory use.
WATCH_SOURCE_LIMIT = 3


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

    def plan_watch(
        self,
        catalogue: Catalogue,
        episode_number: int,
        progress: ProgressCallback | None = None,
    ) -> SourcePlan:
        """Race a bounded set of sources for one episode and keep the winner."""

        episode = next(
            (item for item in catalogue.episodes if item.number == episode_number),
            None,
        )
        if episode is None:
            raise ValueError(f"Unknown episode number: {episode_number}")

        candidates = list(episode.candidates)
        ranking = self.source_health.rank_urls(
            [candidate.url for candidate in candidates]
        )
        ordered = [
            candidates[index]
            for index in ranking
            if self.resolvers.supports(candidates[index].url)
        ]
        if not ordered:
            return SourcePlan(
                None,
                {episode_number: []},
                missing_episodes=(episode_number,),
            )
        if progress:
            progress(
                f"Checking up to {min(WATCH_SOURCE_LIMIT, len(ordered))} "
                "stream sources in parallel..."
            )

        deadline = time.monotonic() + WATCH_PREPARATION_DEADLINE_SECONDS
        records: list[PreflightResult] = []
        winner: PreflightResult | None = None
        pool = ThreadPoolExecutor(
            max_workers=min(WATCH_SOURCE_LIMIT, len(ordered))
        )
        pending = {
            pool.submit(
                self._resolve_and_probe,
                episode_number,
                candidate,
                WATCH_CANDIDATE_DEADLINE_SECONDS,
            ): candidate
            for candidate in ordered
        }
        try:
            while pending and winner is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, _ = wait(
                    tuple(pending),
                    timeout=max(0.001, remaining),
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    break
                # When multiple candidates finish in the same scheduler tick,
                # retain the health-ranked order as the deterministic tie-break.
                completed = sorted(
                    done,
                    key=lambda future: ordered.index(pending[future]),
                )
                for future in completed:
                    candidate = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = PreflightResult(
                            episode_number,
                            candidate,
                            None,
                            False,
                            str(exc),
                        )
                    records.append(result)
                    if winner is None and result.valid and result.media is not None:
                        winner = result

            detail = (
                "source race cancelled after another source succeeded"
                if winner is not None
                else "source planning deadline exceeded"
            )
            for future, candidate in pending.items():
                future.cancel()
                records.append(
                    PreflightResult(
                        episode_number,
                        candidate,
                        None,
                        False,
                        detail,
                    )
                )
        finally:
            # Running resolver calls are bounded by the HTTP layer and cannot
            # be interrupted safely; queued calls are cancelled immediately.
            pool.shutdown(wait=False, cancel_futures=True)

        cache: dict[tuple[int, str], ResolvedMedia] = {}
        if winner is None or winner.media is None:
            return SourcePlan(
                None,
                {episode_number: ordered},
                preflight=records,
                missing_episodes=(episode_number,),
            )

        cache[(episode_number, winner.candidate.url)] = winner.media
        route = [winner.candidate]
        route.extend(candidate for candidate in ordered if candidate != winner.candidate)
        return SourcePlan(
            winner.candidate.player,
            {episode_number: route},
            cache=cache,
            preflight=records,
            verified_episodes=(episode_number,),
            players_used=(winner.candidate.player,),
        )

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

    def _resolve_and_probe(
        self,
        episode: int,
        candidate: EmbedCandidate,
        deadline_seconds: float | None = None,
    ) -> PreflightResult:
        resolver = self.resolvers.resolver_for(candidate.url)
        if resolver is None:
            return PreflightResult(episode, candidate, None, False, f"unsupported host: {hostname(candidate.url)}")
        started = time.monotonic()
        try:
            media = resolver.resolve(candidate.url)
            probe = self.probe.probe(media)
            latency = time.monotonic() - started
            if deadline_seconds is not None and latency > deadline_seconds:
                self.source_health.observe(
                    candidate.url,
                    latency_seconds=latency,
                    success=False,
                )
                return PreflightResult(
                    episode,
                    candidate,
                    None,
                    False,
                    "source candidate deadline exceeded",
                )
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
