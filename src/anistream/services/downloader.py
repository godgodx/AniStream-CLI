from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from anistream.models import Catalogue, DownloadResult, SourceAttempt
from anistream.resolvers.base import hostname
from anistream.resolvers.registry import ResolverRegistry
from anistream.services.media_probe import RemoteMediaProbe
from anistream.services.media_validator import MediaValidator
from anistream.services.source_planner import SourcePlan
from anistream.utils.paths import media_directory


EventCallback = Callable[[int, str], None]


class DownloadManager:
    def __init__(
        self,
        *,
        ffmpeg_path: str,
        validator: MediaValidator,
        resolvers: ResolverRegistry,
        probe: RemoteMediaProbe,
        download_root: Path,
        parallel_downloads: int = 3,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.validator = validator
        self.resolvers = resolvers
        self.probe = probe
        self.download_root = download_root
        self.parallel_downloads = max(1, parallel_downloads)

    def download(
        self,
        catalogue: Catalogue,
        episode_numbers: list[int],
        plan: SourcePlan,
        *,
        parallel: bool,
        event: EventCallback | None = None,
    ) -> list[DownloadResult]:
        if parallel and len(episode_numbers) > 1:
            results: list[DownloadResult] = []
            with ThreadPoolExecutor(max_workers=min(self.parallel_downloads, len(episode_numbers))) as pool:
                pending = {
                    pool.submit(self._download_episode, catalogue, number, plan, event): number
                    for number in episode_numbers
                }
                for future in as_completed(pending):
                    number = pending[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append(DownloadResult(number, False, validation=str(exc)))
            return sorted(results, key=lambda item: item.episode)
        return [self._download_episode(catalogue, number, plan, event) for number in episode_numbers]

    def _download_episode(
        self,
        catalogue: Catalogue,
        episode: int,
        plan: SourcePlan,
        event: EventCallback | None,
    ) -> DownloadResult:
        folder = media_directory(self.download_root, catalogue.title, catalogue.season, catalogue.language)
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"Episode {episode:03d}.mp4"
        existing = self.validator.validate(output) if output.exists() else None
        if existing and existing.valid:
            if event:
                event(episode, "already exists and passed verification")
            return DownloadResult(episode, True, output, validation=existing.detail, skipped=True)

        result = DownloadResult(episode, False, output)
        route = plan.routes.get(episode, [])
        for candidate in route:
            embed_host = hostname(candidate.url)
            attempt = SourceAttempt(candidate.player, embed_host)
            result.attempts.append(attempt)
            if event:
                event(episode, f"trying {candidate.player} ({embed_host})")
            try:
                media = plan.cache.get((episode, candidate.url))
                if media is None:
                    media = self.resolvers.resolve(candidate.url)
                    remote = self.probe.probe(media)
                    if not remote.valid:
                        raise RuntimeError(f"source preflight failed: {remote.detail}")
                attempt.resolver = media.resolver_name
                temp = output.with_name(f".{output.stem}.part.mp4")
                temp.unlink(missing_ok=True)
                ffmpeg_error = self._run_ffmpeg(media.url, dict(media.headers), temp)
                if ffmpeg_error:
                    temp.unlink(missing_ok=True)
                    raise RuntimeError(ffmpeg_error)
                validation = self.validator.validate(temp)
                if not validation.valid:
                    temp.unlink(missing_ok=True)
                    raise RuntimeError(f"media verification failed: {validation.detail}")
                os.replace(temp, output)
                attempt.success = True
                attempt.detail = validation.detail
                result.success = True
                result.source = embed_host
                result.validation = validation.detail
                if event:
                    event(episode, f"completed and verified via {embed_host}")
                return result
            except Exception as exc:
                attempt.detail = str(exc)
                if event:
                    event(episode, f"source failed; switching automatically ({exc})")
        result.validation = "all supported sources failed" if route else "no supported source was available"
        return result

    def _run_ffmpeg(self, media_url: str, headers: dict[str, str], output: Path) -> str | None:
        header_blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if value)
        command = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
        if header_blob:
            command.extend(["-headers", header_blob])
        command.extend(
            [
                "-i",
                media_url,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(output),
            ]
        )
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode == 0 and output.exists():
            return None
        lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
        return lines[-1] if lines else f"FFmpeg exited with code {completed.returncode}"
