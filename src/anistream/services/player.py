from __future__ import annotations

import hashlib
import os
import re
import subprocess
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from anistream.errors import PlaybackError, ResolverError, ToolNotFoundError
from anistream.models import Catalogue, Episode, ResolvedMedia
from anistream.resolvers.registry import ResolverRegistry
from anistream.services.history import HistoryStore
from anistream.services.media_probe import RemoteMediaProbe
from anistream.services.source_planner import SourcePlan
from anistream.utils.paths import data_dir


class _WindowsKillOnCloseJob:
    """Keep a child process tied to this Python process on Windows."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        pass

    _ExtendedLimitInformation._fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.handle: int | None = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        information = self._ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle))
        if not assigned:
            kernel32.CloseHandle(handle)
            return
        self.handle = handle

    @property
    def active(self) -> bool:
        return self.handle is not None

    def close(self) -> None:
        if self.handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = None


class PlaybackService:
    def __init__(
        self,
        *,
        mpv_path: str | None,
        display_mode: str,
        history: HistoryStore,
        resolvers: ResolverRegistry,
        probe: RemoteMediaProbe,
    ) -> None:
        self.mpv_path = mpv_path
        self.display_mode = display_mode
        self.history = history
        self.resolvers = resolvers
        self.probe = probe

    @staticmethod
    def terminal_video_supported() -> bool:
        if os.name != "nt":
            return True
        markers = " ".join(
            filter(
                None,
                (
                    os.environ.get("MSYSTEM"),
                    os.environ.get("TERM_PROGRAM"),
                    os.environ.get("MINTTY_SHORTCUT"),
                ),
            )
        ).lower()
        return "mintty" in markers or bool(os.environ.get("MSYSTEM"))

    def play(
        self,
        catalogue: Catalogue,
        episode: Episode,
        plan: SourcePlan | None = None,
        status: Callable[[str], None] | None = None,
        preferred_media: ResolvedMedia | None = None,
    ) -> bool:
        if not self.mpv_path:
            raise ToolNotFoundError("mpv was not found; set its path in Settings before using Watch")
        media = preferred_media or self._pick_media(episode, plan, status)
        state = self.history.get(catalogue.provider_id, catalogue.url) or {}
        start = float(state.get("position", 0.0)) if int(state.get("current_episode", 0) or 0) == episode.number else 0.0
        watch_dir = self._watch_later_dir(catalogue, episode.number)
        watch_dir.mkdir(parents=True, exist_ok=True)
        resume_snapshot = self._watch_later_snapshot(watch_dir)

        command = [
            self.mpv_path,
            "--really-quiet",
            "--no-config",
            "--load-scripts=no",
            "--ytdl=no",
            "--load-unsafe-playlists=no",
            "--no-use-filedir-conf",
            "--save-position-on-quit",
            f"--watch-later-dir={watch_dir}",
            "--watch-later-options=start",
            "--write-filename-in-watch-later-config",
            "--cache-on-disk=no",
            (
                f"--force-media-title={catalogue.title} - {catalogue.season} - "
                f"{catalogue.language.label} - Episode {episode.number}"
            ),
        ]
        if start > 0:
            command.append(f"--start={start:.3f}")
        user_agent = media.headers.get("User-Agent")
        referer = media.headers.get("Referer")
        origin = media.headers.get("Origin")
        if user_agent:
            command.append(f"--user-agent={user_agent}")
        if referer:
            command.append(f"--referrer={referer}")
        if origin:
            command.append(f"--http-header-fields=Origin: {origin}")
        if self.display_mode == "terminal":
            command.extend(["--vo=tct", "--profile=sw-fast"])
        command.append(media.url)

        if status:
            if media.kind == "local":
                status(f"Playing downloaded episode {episode.number}...")
            else:
                status(f"Streaming episode {episode.number} with {media.resolver_name}...")
        try:
            process = subprocess.Popen(command, shell=False)
        except OSError as exc:
            raise PlaybackError(f"mpv could not start: {exc}") from exc
        job = _WindowsKillOnCloseJob(process)
        if os.name == "nt" and not job.active:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Could not attach mpv to the AniStream process guard")
        try:
            return_code = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            job.close()
        position = self._resume_position(watch_dir, resume_snapshot)
        finished = return_code == 0 and position is None
        saved_position = position
        if return_code != 0 and saved_position is None and start > 0:
            saved_position = start
        self.history.update(
            provider_id=catalogue.provider_id,
            provider_name=catalogue.provider_name,
            catalogue_url=catalogue.url,
            title=catalogue.title,
            season=catalogue.season,
            language=catalogue.language.label,
            language_code=catalogue.language.code,
            episode=episode.number,
            total_episodes=len(catalogue.episodes),
            position=saved_position or 0.0,
            duration=0.0,
            completed=finished,
        )
        if return_code != 0:
            raise PlaybackError(
                f"mpv stopped unexpectedly with exit code {return_code}. "
                "Your progress was preserved and no automatic source switch was attempted."
            )
        return finished

    def _pick_media(
        self,
        episode: Episode,
        plan: SourcePlan | None,
        status: Callable[[str], None] | None,
    ) -> ResolvedMedia:
        route = plan.routes.get(episode.number, []) if plan else list(episode.candidates)
        errors: list[str] = []
        for candidate in route:
            try:
                media = plan.cache.get((episode.number, candidate.url)) if plan else None
                if media is None:
                    media = self.resolvers.resolve(candidate.url)
                    probe = self.probe.probe(media)
                    if not probe.valid:
                        raise ResolverError(probe.detail)
                return media
            except Exception as exc:
                errors.append(f"{candidate.player}: {exc}")
                if status:
                    status(f"{candidate.player} failed; trying the next source...")
        detail = "; ".join(errors) if errors else "no playable sources are available"
        raise ResolverError("all sources failed: " + detail)

    @staticmethod
    def _watch_later_dir(catalogue: Catalogue, episode: int) -> Path:
        identity = f"{catalogue.provider_id}|{catalogue.url}|{episode}".encode("utf-8")
        return data_dir() / "mpv_state" / hashlib.sha256(identity).hexdigest()[:24]

    @staticmethod
    def _watch_later_snapshot(directory: Path) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        for path in directory.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                snapshot[path] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
        return snapshot

    @staticmethod
    def _resume_position(
        directory: Path,
        previous: dict[Path, tuple[int, int]] | None = None,
    ) -> float | None:
        files: list[Path] = []
        for path in directory.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if previous is not None and previous.get(path) == (stat.st_mtime_ns, stat.st_size):
                continue
            files.append(path)
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files:
            try:
                match = re.search(r"(?m)^start=([0-9.]+)\s*$", path.read_text(encoding="utf-8", errors="ignore"))
                if match:
                    return float(match.group(1))
            except (OSError, ValueError):
                continue
        return None
