from __future__ import annotations

import sys

from anistream.cli import Cli
from anistream.errors import AniStreamError, ProviderError, ToolNotFoundError
from anistream.models import Catalogue, SearchResult
from anistream.providers import ProviderRegistry, default_providers
from anistream.resolvers import ResolverRegistry, default_resolvers
from anistream.services.downloader import DownloadManager
from anistream.services.history import HistoryStore
from anistream.services.media_probe import RemoteMediaProbe
from anistream.services.media_validator import MediaValidator
from anistream.services.player import PlaybackService
from anistream.services.settings import SettingsStore
from anistream.services.source_planner import SourcePlanner
from anistream.utils.http import DEFAULT_USER_AGENT, HttpClient


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
        self.providers = ProviderRegistry(default_providers(self.http))
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
                if choice == "4":
                    self.cli.settings_menu()
                    continue
                selected = self._from_link() if choice == "2" else self._from_search()
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

    def _continue_watching(self) -> None:
        entry = self.cli.choose_history_entry(self.history.all())
        if entry is None:
            return
        provider = self.providers.get(str(entry.get("provider_id", "")))
        if provider is None:
            self.cli.error("This title belongs to a provider that is no longer enabled.")
            self.cli.pause()
            return

        with self.cli.status(f"Refreshing {provider.name}..."):
            catalogue = provider.catalogue(str(entry["catalogue_url"]))
        current = self._sync_history(catalogue)
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

        with self.cli.status("Finding the first source with 100% working links..."):
            plan = self.planner.plan(catalogue, selected, progress=self.cli.info)
        if plan.primary_player:
            self.cli.success(f"Selected {plan.primary_player}: every selected episode passed preflight")
        else:
            self.cli.warning("No single player passed 100%; using the best verified source per episode")
        manager = DownloadManager(
            ffmpeg_path=ffmpeg,
            validator=MediaValidator(ffprobe),
            resolvers=self.resolvers,
            probe=self.probe,
            download_root=self.settings.download_directory(),
            parallel_downloads=int(self.settings.get("parallel_downloads", 3)),
        )
        results = manager.download(
            catalogue,
            selected,
            plan,
            parallel=mode == "parallel",
            event=lambda episode, message: self.cli.info(f"Episode {episode}: {message}"),
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

    def _watch(self, catalogue: Catalogue, *, start_episode: int | None = None) -> None:
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
        if start_episode is None:
            selection = self.cli.episodes(len(catalogue.episodes), single=True, default=default)
            if not selection:
                return
            number = selection[0]
        else:
            number = min(len(catalogue.episodes), max(1, int(start_episode)))
        while 1 <= number <= len(catalogue.episodes):
            episode = catalogue.episodes[number - 1]
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
                finished = player.play(catalogue, episode, plan, self.cli.info)
            except ToolNotFoundError as exc:
                self.cli.error(str(exc))
                self.cli.pause()
                return
            if not finished:
                self.cli.info(f"Episode {number} progress saved")
                self.cli.pause()
                return
            self.cli.success(f"Episode {number} marked as watched")
            if number >= len(catalogue.episodes) or not self.cli.confirm(
                f"Play episode {number + 1} now?", default=True
            ):
                self.cli.pause()
                return
            number += 1


def main() -> int:
    try:
        return Application().run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
