from __future__ import annotations

import sys
from pathlib import Path

from anistream.cli import Cli
from anistream.errors import AniStreamError, PlaybackError, ProviderError, ResolverError, ToolNotFoundError
from anistream.models import Catalogue, Episode, MediaLanguage, ResolvedMedia, SearchResult
from anistream.providers import ProviderRegistry, default_providers
from anistream.resolvers import ResolverRegistry, default_resolvers
from anistream.services.downloader import DownloadManager
from anistream.services.history import HistoryStore
from anistream.services.local_library import LocalLibrary
from anistream.services.media_probe import RemoteMediaProbe
from anistream.services.media_validator import MediaValidator
from anistream.services.player import PlaybackService
from anistream.services.settings import SettingsStore
from anistream.services.source_planner import SourcePlanner
from anistream.utils.http import DEFAULT_USER_AGENT, HttpClient
from anistream.utils.paths import media_directory


class Application:
    def __init__(self) -> None:
        self.settings = SettingsStore()
        self.history = HistoryStore()
        provider_settings = self.settings.provider_settings("anime_sama")
        cookie = provider_settings.get("cf_clearance", "")
        cookie_header = f"cf_clearance={cookie}" if cookie else ""
        self.http = HttpClient(
            provider_settings.get("user_agent") or DEFAULT_USER_AGENT,
            cookie_header,
            cookie_hosts={"anime-sama.to", "www.anime-sama.to"},
        )
        self.available_providers = tuple(default_providers(self.http))
        self._refresh_providers()
        self.resolvers = ResolverRegistry(default_resolvers(self.http))
        self.probe = RemoteMediaProbe(self.http)
        self.planner = SourcePlanner(self.resolvers, self.probe)
        self.cli = Cli(self.settings, self.history)

    def run(self) -> int:
        while True:
            try:
                self.cli.main_screen()
                choice = self.cli.main_choice()
                if choice == "q":
                    return 0
                if choice == "1":
                    self._continue_watching()
                    continue
                if choice == "2":
                    selected = self._from_search()
                elif choice == "3":
                    self._local_library()
                    continue
                elif choice == "4":
                    selected = self._from_link()
                elif choice == "5":
                    self.cli.settings_menu(
                        tuple((provider.id, provider.name) for provider in self.available_providers)
                    )
                    self._refresh_providers()
                    continue
                else:
                    continue
                if selected is None:
                    continue
                provider, url = selected
                self._open(provider, url)
            except KeyboardInterrupt:
                self.cli.warning("Cancelled")
                self.cli.pause()
            except AniStreamError as exc:
                self.cli.error(str(exc))
                self.cli.pause()
            except Exception as exc:
                self.cli.error(f"Unexpected error: {exc}")
                self.cli.pause()

    def _from_link(self):
        self.cli.input_screen(
            "Open a link",
            "Paste a catalogue URL from any enabled site.",
        )
        url = self.cli.ask("Paste a catalogue link").strip()
        provider = self.providers.detect(url)
        if provider is None:
            disabled = next(
                (item for item in self.available_providers if item.matches(url)),
                None,
            )
            if disabled is not None:
                self.cli.error(
                    f"{disabled.name} is disabled. Enable it in Settings > Sources to open this link."
                )
                self.cli.pause()
                return None
            self.cli.error(f"Unsupported site. Enabled sites: {self.providers.names()}")
            self.cli.pause()
            return None
        self.cli.success(f"Detected {provider.name}")
        return provider, url

    def _from_search(self):
        self.cli.input_screen(
            "Search",
            "Search every enabled site by title.",
        )
        query = self.cli.ask("Search title").strip()
        if not query:
            return None
        if not self.providers.providers:
            self.cli.warning("No sources are enabled. Enable at least one in Settings > Sources.")
            self.cli.pause()
            return None
        with self.cli.status("Searching every enabled site..."):
            results, errors = self.providers.search(query)
        for error in errors:
            self.cli.warning(error)
        if not results:
            self.cli.warning("No results found")
            self.cli.pause()
            return None
        if errors:
            self.cli.pause("Press Enter to view the available results")
        result = self.cli.choose_search_result(results)
        if result is None:
            return None
        provider = next(item for item in self.providers.providers if item.id == result.provider_id)
        return provider, result.url

    def _refresh_providers(self) -> None:
        self.providers = ProviderRegistry(
            [
                provider
                for provider in self.available_providers
                if self.settings.provider_enabled(provider.id)
            ]
        )

    def _local_library(self) -> None:
        with self.cli.status("Scanning the local library..."):
            entries = LocalLibrary(self.settings.download_directory()).scan(self.history.all())
        entry = self.cli.choose_local_entry(entries)
        if entry is None:
            return
        catalogue = entry.catalogue()
        current = self.history.get(catalogue.provider_id, catalogue.url)
        self.cli.clear_screen()
        self.cli.show_catalogue(catalogue, current)
        if entry.status == "in_progress":
            self.cli.info(
                f"Resuming {catalogue.title} from downloaded episode {entry.resume_episode}..."
            )
        elif entry.status == "completed":
            self.cli.info(
                f"Replaying {catalogue.title} from downloaded episode {entry.resume_episode}..."
            )
        else:
            self.cli.info(
                f"Starting {catalogue.title} from downloaded episode {entry.resume_episode}..."
            )
        self._watch(
            catalogue,
            start_episode=entry.resume_episode,
            episode_sequence=entry.episodes,
        )

    def _continue_watching(self) -> None:
        entry = self.cli.choose_history_entry(self.history.all())
        if entry is None:
            return
        provider = self.providers.get(str(entry.get("provider_id", "")))
        catalogue = None
        offline = False
        if provider is None:
            catalogue = self._offline_catalogue(entry)
            if catalogue is None:
                self.cli.error(
                    "This title belongs to a provider that is no longer enabled, "
                    "and its current episode is not available locally."
                )
                self.cli.pause()
                return
            offline = True
            self.cli.warning("The original provider is disabled; continuing from the local download.")
        else:
            try:
                with self.cli.status(f"Refreshing {provider.name}..."):
                    catalogue = provider.catalogue(str(entry["catalogue_url"]))
            except Exception as exc:
                catalogue = self._offline_catalogue(entry)
                if catalogue is None:
                    raise
                offline = True
                self.cli.warning(
                    f"{provider.name} is unavailable ({exc}); continuing from the local download."
                )

        current = dict(entry) if offline else self._sync_history(catalogue)
        if current is None:
            self.cli.warning("This title is no longer present in your watch history.")
            self.cli.pause()
            return
        self.cli.clear_screen()
        self.cli.show_catalogue(catalogue, current)

        if current.get("status") == "completed":
            action = self.cli.completed_history_action(current)
            if action == "restart":
                self._watch(catalogue, start_episode=1)
            elif action == "choose":
                self._watch(catalogue)
            return

        episode = int(current.get("current_episode", 1) or 1)
        self.cli.info(f"Resuming {catalogue.title} at episode {episode}...")
        self._watch(catalogue, start_episode=episode)

    def _offline_catalogue(self, entry: dict) -> Catalogue | None:
        try:
            current_episode = max(1, int(entry.get("current_episode", 1) or 1))
            total_episodes = max(current_episode, int(entry.get("total_episodes", current_episode) or current_episode))
        except (TypeError, ValueError):
            return None
        language_label = str(entry.get("language") or "Unknown")
        language_code = str(entry.get("language_code") or language_label.casefold().replace(" ", "-") or "unknown")
        catalogue = Catalogue(
            provider_id=str(entry.get("provider_id") or "local"),
            provider_name=str(entry.get("provider_name") or "Local library"),
            title=str(entry.get("title") or "Unknown title"),
            url=str(entry.get("catalogue_url") or "local://library"),
            season=str(entry.get("season") or "Unknown season"),
            language=MediaLanguage(language_code, language_label),
            episodes=tuple(Episode(number, ()) for number in range(1, total_episodes + 1)),
        )
        return catalogue if self._local_episode_path(catalogue, current_episode).is_file() else None

    def _local_episode_path(self, catalogue: Catalogue, episode: int) -> Path:
        folder = media_directory(
            self.settings.download_directory(),
            catalogue.title,
            catalogue.season,
            catalogue.language.label,
        )
        return folder / f"Episode {episode:03d}.mp4"

    def _local_episode_media(self, catalogue: Catalogue, episode: int) -> ResolvedMedia | None:
        path = self._local_episode_path(catalogue, episode)
        if not path.is_file():
            return None
        has_online_fallback = (
            1 <= episode <= len(catalogue.episodes)
            and bool(catalogue.episodes[episode - 1].candidates)
        )
        fallback = "using online sources" if has_online_fallback else "no online fallback is available"
        ffprobe = self.settings.executable("ffprobe_path", "ffprobe")
        if not ffprobe:
            self.cli.warning(
                f"Downloaded episode {episode} was found, but FFprobe is unavailable; {fallback}."
            )
            return None
        try:
            validation = MediaValidator(ffprobe).validate(path)
        except Exception as exc:
            self.cli.warning(
                f"Downloaded episode {episode} could not be verified ({exc}); {fallback}."
            )
            return None
        if not validation.valid:
            self.cli.warning(
                f"Downloaded episode {episode} failed MP4 validation ({validation.detail}); {fallback}."
            )
            return None
        self.cli.success(f"Using verified local file for episode {episode}")
        resolved = path.resolve()
        return ResolvedMedia(
            url=str(resolved),
            embed_url=str(resolved),
            resolver_name="Local file",
            kind="local",
        )

    def _open(self, provider, url: str) -> None:
        with self.cli.status(f"Loading {provider.name}..."):
            variants = provider.variants(url)
        variant = variants[0] if len(variants) == 1 else self.cli.choose_variant(variants)
        if variant is None:
            return
        with self.cli.status("Loading episodes..."):
            catalogue = provider.catalogue(variant.url)
        current = self._sync_history(catalogue)
        self.cli.clear_screen()
        self.cli.show_catalogue(catalogue, current)
        action = self.cli.choose_action()
        if action == "download":
            self._download(catalogue)
        elif action == "watch":
            self._watch(catalogue)

    def _sync_history(self, catalogue: Catalogue) -> dict | None:
        return self.history.sync_catalogue(
            provider_id=catalogue.provider_id,
            provider_name=catalogue.provider_name,
            catalogue_url=catalogue.url,
            title=catalogue.title,
            season=catalogue.season,
            language=catalogue.language.label,
            language_code=catalogue.language.code,
            total_episodes=len(catalogue.episodes),
        )

    def _download(self, catalogue: Catalogue) -> None:
        selected = self.cli.episodes(len(catalogue.episodes))
        if not selected:
            return
        ffmpeg, ffprobe = self._download_tools()
        if not ffmpeg or not ffprobe:
            return
        mode = self.settings.get("download_mode")
        if mode not in {"sequential", "parallel"}:
            mode = self.cli.ask(
                "Download episodes sequentially or in parallel? This choice is saved",
                choices=["sequential", "parallel"],
                default="sequential",
            )
            self.settings.set("download_mode", mode)

        with self.cli.status("Verifying episode coverage across available players..."):
            plan = self.planner.plan(catalogue, selected, progress=self.cli.info)
        if plan.primary_player:
            self.cli.success(f"Selected {plan.primary_player}: every selected episode passed preflight")
        elif plan.complete:
            player_count = len(plan.players_used)
            noun = "player" if player_count == 1 else "players"
            self.cli.success(
                f"Rebuilt complete episode coverage using {player_count} verified {noun}"
            )
        else:
            self.cli.warning(
                f"Verified {len(plan.verified_episodes)}/{len(selected)} selected episodes across every player"
            )
            if not self.cli.confirm_incomplete_download(selected, plan.missing_episodes):
                self.cli.info("Download cancelled before any transfer started")
                self.cli.pause()
                return
        manager = DownloadManager(
            ffmpeg_path=ffmpeg,
            validator=MediaValidator(ffprobe),
            resolvers=self.resolvers,
            probe=self.probe,
            download_root=self.settings.download_directory(),
            parallel_downloads=int(self.settings.get("parallel_downloads", 3)),
        )
        with self.cli.download_progress(selected) as progress:
            results = manager.download(
                catalogue,
                selected,
                plan,
                parallel=mode == "parallel",
                progress=progress.update,
            )
        self.cli.download_report(results)
        self.cli.pause()

    def _download_tools(self) -> tuple[str | None, str | None]:
        ffmpeg = self.settings.executable("ffmpeg_path", "ffmpeg")
        ffprobe = self.settings.executable("ffprobe_path", "ffprobe")
        if not ffmpeg:
            self.cli.error("FFmpeg was not found. Install it or set its executable path in Settings.")
            self.cli.pause()
            return None, None
        if not ffprobe:
            self.cli.error("FFprobe was not found. It is required to prove that every output is a real MP4.")
            self.cli.pause()
            return None, None
        if not self.settings.get("ffmpeg_path"):
            if self.cli.confirm(f"Use detected FFmpeg at {ffmpeg}? This choice is saved", default=True):
                self.settings.set("ffmpeg_path", ffmpeg)
            else:
                return None, None
        if not self.settings.get("ffprobe_path"):
            self.settings.set("ffprobe_path", ffprobe)
        return ffmpeg, ffprobe

    def _watch(
        self,
        catalogue: Catalogue,
        *,
        start_episode: int | None = None,
        episode_sequence: tuple[int, ...] | None = None,
    ) -> None:
        display = self.settings.get("watch_display")
        if display not in {"window", "terminal"}:
            self.cli.info("Terminal video uses low-resolution Unicode rendering and is not supported by every terminal.")
            display = self.cli.ask(
                "Watch in a normal player window or render inside the terminal? This choice is saved",
                choices=["window", "terminal"],
                default="window",
            )
            self.settings.set("watch_display", display)
        mpv = self.settings.executable("mpv_path", "mpv")
        if not mpv:
            self.cli.error("mpv was not found. Install mpv or set mpv.exe in Settings; Watch does not save a video file.")
            self.cli.pause()
            return
        if not self.settings.get("mpv_path"):
            self.settings.set("mpv_path", mpv)
        if display == "terminal" and not PlaybackService.terminal_video_supported():
            self.cli.warning(
                "This Windows terminal cannot display mpv's Unicode video output reliably; switching to window mode."
            )
            display = "window"
            self.settings.set("watch_display", display)
        current = self.history.get(catalogue.provider_id, catalogue.url) or {}
        default = int(current.get("current_episode", 1) or 1)
        if default > len(catalogue.episodes):
            default = len(catalogue.episodes)
        play_order = (
            tuple(sorted({number for number in episode_sequence if 1 <= number <= len(catalogue.episodes)}))
            if episode_sequence is not None
            else tuple(range(1, len(catalogue.episodes) + 1))
        )
        if not play_order:
            self.cli.error("No playable local episodes are available for this title.")
            self.cli.pause()
            return
        if start_episode is None:
            selection = self.cli.episodes(len(catalogue.episodes), single=True, default=default)
            if not selection:
                return
            number = selection[0]
        else:
            number = min(len(catalogue.episodes), max(1, int(start_episode)))
        if number not in play_order:
            number = next((candidate for candidate in play_order if candidate >= number), play_order[-1])
        order_index = play_order.index(number)
        while order_index < len(play_order):
            number = play_order[order_index]
            episode = catalogue.episodes[number - 1]
            local_media = self._local_episode_media(catalogue, number)
            plan = None
            if local_media is None:
                with self.cli.status("Checking stream sources..."):
                    plan = self.planner.plan(catalogue, [number])
            player = PlaybackService(
                mpv_path=mpv,
                display_mode=display,
                history=self.history,
                resolvers=self.resolvers,
                probe=self.probe,
            )
            try:
                finished = player.play(
                    catalogue,
                    episode,
                    plan,
                    self.cli.info,
                    preferred_media=local_media,
                )
            except ToolNotFoundError as exc:
                self.cli.error(str(exc))
                self.cli.pause()
                return
            except (PlaybackError, ResolverError) as exc:
                self.cli.error(f"Episode {number} could not be played: {exc}")
                self.cli.info("Your watch progress is safe. You can retry this episode later.")
                self.cli.pause()
                return
            if not finished:
                self.cli.info(f"Episode {number} progress saved")
                self.cli.pause()
                return
            self.cli.success(f"Episode {number} marked as watched")
            next_number = play_order[order_index + 1] if order_index + 1 < len(play_order) else None
            if next_number is None or not self.cli.confirm(
                f"Play episode {next_number} now?", default=True
            ):
                self.cli.pause()
                return
            order_index += 1


def main() -> int:
    try:
        return Application().run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
