from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence, TypeVar

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from anistream.models import Catalogue, CatalogueVariant, DownloadResult, SearchResult
from anistream.services.history import HistoryStore
from anistream.services.settings import SettingsStore


T = TypeVar("T")


def parse_episode_selection(value: str, maximum: int) -> list[int]:
    text = value.strip().lower()
    if text == "all":
        return list(range(1, maximum + 1))
    if text == "latest":
        return [maximum]
    selected: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            raw_start, raw_end = token.split("-", 1)
            start, end = int(raw_start), int(raw_end)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    invalid = sorted(number for number in selected if number < 1 or number > maximum)
    if invalid:
        raise ValueError(f"episodes outside 1-{maximum}: {', '.join(map(str, invalid))}")
    if not selected:
        raise ValueError("no episodes selected")
    return sorted(selected)


class Cli:
    def __init__(self, settings: SettingsStore, history: HistoryStore) -> None:
        self.settings = settings
        self.history = history
        self.console = Console(highlight=False)

    def header(self) -> None:
        title = Text("ANISTREAM", style="bold bright_cyan")
        subtitle = Text("Discover  •  Stream  •  Download", style="dim")
        self.console.print(Panel(Text.assemble(title, "\n", subtitle), border_style="cyan", padding=(0, 2)))

    def main_choice(self) -> str:
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_row("[bold cyan]1[/]", "Link", "Open a supported catalogue URL")
        table.add_row("[bold cyan]2[/]", "Search", "Search across every enabled site")
        table.add_row("[bold cyan]3[/]", "Settings", "Change saved preferences")
        self.console.print(table)
        return Prompt.ask("[bold]Choose[/]", choices=["1", "2", "3", "q"], default="1")

    def choose_item(self, title: str, items: Sequence[T], labels: Iterable[str]) -> T | None:
        self.console.print(f"\n[bold bright_cyan]{title}[/]")
        table = Table(box=box.SIMPLE, show_header=False)
        for index, label in enumerate(labels, 1):
            table.add_row(f"[cyan]{index}[/]", label)
        table.add_row("[dim]0[/]", "Back")
        self.console.print(table)
        choice = IntPrompt.ask("Select", default=1)
        if choice == 0:
            return None
        if not 1 <= choice <= len(items):
            self.error("Invalid selection")
            return None
        return items[choice - 1]

    def choose_search_result(self, results: list[SearchResult]) -> SearchResult | None:
        labels = [f"[bold]{item.title}[/]  [dim][{item.provider_name}][/]" for item in results]
        return self.choose_item("Search results", results, labels)

    def choose_variant(self, variants: list[CatalogueVariant]) -> CatalogueVariant | None:
        return self.choose_item("Available versions", variants, (item.name for item in variants))

    def show_catalogue(self, catalogue: Catalogue, history: dict | None) -> None:
        details = Table.grid(padding=(0, 2))
        details.add_row("[dim]Site[/]", catalogue.provider_name)
        details.add_row("[dim]Title[/]", f"[bold]{catalogue.title}[/]")
        details.add_row("[dim]Version[/]", f"{catalogue.season} • {catalogue.language}")
        details.add_row("[dim]Episodes[/]", str(len(catalogue.episodes)))
        if history:
            details.add_row(
                "[dim]Watching[/]",
                f"Episode {history.get('current_episode', 1)} • {history.get('status', 'in_progress').replace('_', ' ')}",
            )
        self.console.print(Panel(details, title="Ready", border_style="bright_blue"))

    def choose_action(self) -> str | None:
        items = ["[bold green]Watch[/]  Stream without saving a media file", "[bold blue]Download[/]  Save verified MP4 files"]
        selected = self.choose_item("What would you like to do?", ["watch", "download"], items)
        return selected

    def episodes(self, maximum: int, *, single: bool = False, default: int | None = None) -> list[int] | None:
        while True:
            hint = f"1-{maximum}" if single else f"1-{maximum}, comma lists, ranges, all, or latest"
            raw = Prompt.ask(f"Episode ({hint})", default=str(default or 1))
            try:
                selected = parse_episode_selection(raw, maximum)
                if single and len(selected) != 1:
                    raise ValueError("Watch accepts one episode at a time")
                return selected
            except ValueError as exc:
                self.error(str(exc))
                if not Confirm.ask("Try again?", default=True):
                    return None

    def download_report(self, results: list[DownloadResult]) -> None:
        table = Table(title="Download report", box=box.ROUNDED)
        table.add_column("Episode", justify="right")
        table.add_column("Result")
        table.add_column("Source")
        table.add_column("Attempts", justify="right")
        table.add_column("Verification")
        for result in results:
            if result.skipped:
                status = "[yellow]Skipped[/]"
            elif result.success:
                status = "[green]Downloaded[/]"
            else:
                status = "[red]Failed[/]"
            table.add_row(
                str(result.episode),
                status,
                result.source or "—",
                str(len(result.attempts)),
                result.validation,
            )
        self.console.print(table)
        succeeded = sum(item.success for item in results)
        failed = len(results) - succeeded
        fallback_count = sum(1 for item in results if item.success and len(item.attempts) > 1)
        summary = f"{succeeded}/{len(results)} successful"
        if fallback_count:
            summary += f" • automatic fallback recovered {fallback_count} episode(s)"
        if failed:
            self.warning(summary + f" • {failed} failed")
        else:
            self.success(summary)

    def settings_menu(self) -> None:
        while True:
            data = self.settings.as_dict()
            provider = data["anime_sama"]
            cookie_state = "configured" if provider.get("cf_clearance") else "not configured"
            table = Table(title="Settings", box=box.ROUNDED)
            table.add_column("#", style="cyan", justify="right")
            table.add_column("Setting")
            table.add_column("Current value", overflow="fold")
            rows = [
                ("1", "Download directory", str(data["download_directory"])),
                ("2", "Download mode", str(data["download_mode"] or "ask on first download")),
                ("3", "Parallel downloads", str(data["parallel_downloads"])),
                ("4", "FFmpeg path", str(data["ffmpeg_path"] or "auto-detect")),
                ("5", "FFprobe path", str(data["ffprobe_path"] or "auto-detect")),
                ("6", "mpv path", str(data["mpv_path"] or "auto-detect")),
                ("7", "Watch display", str(data["watch_display"] or "ask on first watch")),
                ("8", "Anime-Sama session", cookie_state),
                ("9", "Watch history", f"{len(self.history.all())} title(s)"),
            ]
            for row in rows:
                table.add_row(*row)
            self.console.print(table)
            choice = Prompt.ask("Setting to change", choices=[str(i) for i in range(10)], default="0")
            if choice == "0":
                return
            if choice == "1":
                self.settings.set("download_directory", Prompt.ask("Download directory", default=str(data["download_directory"])))
            elif choice == "2":
                self.settings.set("download_mode", Prompt.ask("Mode", choices=["sequential", "parallel"], default=data["download_mode"] or "sequential"))
            elif choice == "3":
                self.settings.set("parallel_downloads", IntPrompt.ask("Maximum simultaneous episodes", default=int(data["parallel_downloads"])))
            elif choice in {"4", "5", "6"}:
                key = {"4": "ffmpeg_path", "5": "ffprobe_path", "6": "mpv_path"}[choice]
                value = Prompt.ask("Executable path or command name (blank = auto-detect)", default=str(data[key] or ""))
                self.settings.set(key, value or None)
            elif choice == "7":
                self.settings.set("watch_display", Prompt.ask("Display mode", choices=["window", "terminal"], default=data["watch_display"] or "window"))
            elif choice == "8":
                user_agent = Prompt.ask("Browser User-Agent", default=str(provider.get("user_agent", "")))
                cookie = Prompt.ask("cf_clearance cookie value", default=str(provider.get("cf_clearance", "")), password=True)
                self.settings.set_provider_settings("anime_sama", {"user_agent": user_agent, "cf_clearance": cookie})
            elif choice == "9" and Confirm.ask("Clear all watch progress and resume data?", default=False):
                self.history.clear()
                self.success("Watch history cleared")

    def info(self, message: str) -> None:
        self.console.print(f"[blue]•[/] {message}")

    def success(self, message: str) -> None:
        self.console.print(f"[green]+[/] {message}")

    def warning(self, message: str) -> None:
        self.console.print(f"[yellow]![/] {message}")

    def error(self, message: str) -> None:
        self.console.print(f"[red]x[/] {message}")
