from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    episode: int
    state: str
    detail: str = ""
    percent: float | None = None
    downloaded_bytes: int | None = None
    bytes_per_second: float | None = None
    eta_seconds: float | None = None


ProgressCallback = Callable[[DownloadProgress], None]
TransferCallback = Callable[[float | None, int, float, float | None], None]


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
        progress: ProgressCallback | None = None,
    ) -> list[DownloadResult]:
        if parallel and len(episode_numbers) > 1:
            results: list[DownloadResult] = []
            with ThreadPoolExecutor(max_workers=min(self.parallel_downloads, len(episode_numbers))) as pool:
                pending = {
                    pool.submit(self._download_episode, catalogue, number, plan, event, progress): number
                    for number in episode_numbers
                }
                for future in as_completed(pending):
                    number = pending[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        self._emit(progress, number, "failed", str(exc))
                        results.append(DownloadResult(number, False, validation=str(exc)))
            return sorted(results, key=lambda item: item.episode)
        return [self._download_episode(catalogue, number, plan, event, progress) for number in episode_numbers]

    def _download_episode(
        self,
        catalogue: Catalogue,
        episode: int,
        plan: SourcePlan,
        event: EventCallback | None,
        progress: ProgressCallback | None,
    ) -> DownloadResult:
        self._emit(progress, episode, "preparing", "Preparing destination")
        folder = media_directory(self.download_root, catalogue.title, catalogue.season, catalogue.language.label)
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"Episode {episode:03d}.mp4"
        existing = self.validator.validate(output) if output.exists() else None
        if existing and existing.valid:
            if event:
                event(episode, "already exists and passed verification")
            self._emit(
                progress,
                episode,
                "skipped",
                "Already verified",
                percent=100.0,
                downloaded_bytes=output.stat().st_size,
                eta_seconds=0.0,
            )
            return DownloadResult(episode, True, output, validation=existing.detail, skipped=True)

        result = DownloadResult(episode, False, output)
        route = plan.routes.get(episode, [])
        for candidate in route:
            embed_host = hostname(candidate.url)
            attempt = SourceAttempt(candidate.player, embed_host)
            result.attempts.append(attempt)
            if event:
                event(episode, f"trying {candidate.player} ({embed_host})")
            self._emit(progress, episode, "resolving", f"{candidate.player} · {embed_host}")
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
                source_detail = f"{candidate.player} · {embed_host}"

                def transfer(
                    percent: float | None,
                    downloaded_bytes: int,
                    bytes_per_second: float,
                    eta_seconds: float | None,
                ) -> None:
                    self._emit(
                        progress,
                        episode,
                        "downloading",
                        source_detail,
                        percent=percent,
                        downloaded_bytes=downloaded_bytes,
                        bytes_per_second=bytes_per_second,
                        eta_seconds=eta_seconds,
                    )

                ffmpeg_error = self._run_ffmpeg(media.url, dict(media.headers), temp, transfer)
                if ffmpeg_error:
                    temp.unlink(missing_ok=True)
                    raise RuntimeError(ffmpeg_error)
                self._emit(progress, episode, "verifying", "Checking MP4 integrity", percent=100.0, eta_seconds=0.0)
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
                self._emit(progress, episode, "completed", f"Verified · {embed_host}", percent=100.0, eta_seconds=0.0)
                return result
            except Exception as exc:
                attempt.detail = str(exc)
                if event:
                    event(episode, f"source failed; switching automatically ({exc})")
                self._emit(progress, episode, "retrying", f"{candidate.player} failed · trying next source")
        result.validation = "all supported sources failed" if route else "no supported source was available"
        self._emit(progress, episode, "failed", result.validation)
        return result

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        episode: int,
        state: str,
        detail: str = "",
        *,
        percent: float | None = None,
        downloaded_bytes: int | None = None,
        bytes_per_second: float | None = None,
        eta_seconds: float | None = None,
    ) -> None:
        if callback:
            callback(
                DownloadProgress(
                    episode,
                    state,
                    detail,
                    percent,
                    downloaded_bytes,
                    bytes_per_second,
                    eta_seconds,
                )
            )

    def _run_ffmpeg(
        self,
        media_url: str,
        headers: dict[str, str],
        output: Path,
        transfer: TransferCallback | None = None,
    ) -> str | None:
        header_blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if value)
        duration = self.validator.probe_duration(media_url, headers)
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-stats_period",
            "0.5",
            "-nostats",
            "-y",
        ]
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
        started = time.monotonic()
        last_size = 0
        if transfer:
            transfer(0.0 if duration > 0 else None, 0, 0.0, None)
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )
        except OSError as exc:
            return str(exc)

        values: dict[str, str] = {}
        diagnostics: list[str] = []
        progress_keys = {
            "frame",
            "fps",
            "stream_0_0_q",
            "bitrate",
            "total_size",
            "out_time_us",
            "out_time_ms",
            "out_time",
            "dup_frames",
            "drop_frames",
            "speed",
            "progress",
        }
        if process.stdout is not None:
            for raw_line in process.stdout:
                key, separator, value = raw_line.strip().partition("=")
                if not separator or key not in progress_keys:
                    if raw_line.strip():
                        diagnostics.append(raw_line.strip())
                    continue
                values[key] = value
                if key != "progress":
                    continue
                out_time = self._progress_seconds(values)
                total_size = self._progress_integer(values.get("total_size"))
                elapsed = max(0.001, time.monotonic() - started)
                bytes_per_second = max(0.0, total_size / elapsed)
                percent = min(100.0, out_time * 100.0 / duration) if duration > 0 else None
                media_rate = out_time / elapsed
                eta = max(0.0, (duration - out_time) / media_rate) if duration > 0 and media_rate > 0 else None
                if transfer:
                    transfer(percent, total_size, bytes_per_second, eta)
                last_size = total_size
                values.clear()

        return_code = process.wait()
        if return_code == 0 and output.exists():
            if transfer:
                elapsed = max(0.001, time.monotonic() - started)
                final_size = max(last_size, output.stat().st_size)
                transfer(
                    100.0 if duration > 0 else None,
                    final_size,
                    final_size / elapsed,
                    0.0,
                )
            return None
        return diagnostics[-1] if diagnostics else f"FFmpeg exited with code {return_code}"

    @staticmethod
    def _progress_integer(value: str | None) -> int:
        try:
            return max(0, int(value or 0))
        except ValueError:
            return 0

    @classmethod
    def _progress_seconds(cls, values: dict[str, str]) -> float:
        microseconds = cls._progress_integer(values.get("out_time_us") or values.get("out_time_ms"))
        if microseconds:
            return microseconds / 1_000_000
        raw = values.get("out_time", "")
        try:
            hours, minutes, seconds = raw.split(":")
            return max(0.0, int(hours) * 3600 + int(minutes) * 60 + float(seconds))
        except (TypeError, ValueError):
            return 0.0
