from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm as RichConfirm
from rich.prompt import IntPrompt as RichIntPrompt
from rich.prompt import Prompt as RichPrompt
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from anistream.models import Catalogue, CatalogueVariant, DownloadResult, SearchResult
from anistream.services.history import HistoryStore
from anistream.services.settings import SettingsStore


T = TypeVar("T")

_WORDMARK_GLYPHS = {
    "A": ("  #  ", " # # ", "#####", "#   #", "#   #"),
    "N": ("#   #", "##  #", "# # #", "#  ##", "#   #"),
    "I": ("#####", "  #  ", "  #  ", "  #  ", "#####"),
    "S": (" ####", "#    ", " ### ", "    #", "#### "),
    "T": ("#####", "  #  ", "  #  ", "  #  ", "  #  "),
    "R": ("#### ", "#   #", "#### ", "# #  ", "#  ##"),
    "E": ("#####", "#    ", "#### ", "#    ", "#####"),
    "M": ("#   #", "## ##", "# # #", "#   #", "#   #"),
}
WORDMARK = tuple(
    "  ".join(_WORDMARK_GLYPHS[letter][row] for letter in "ANISTREAM")
    for row in range(5)
)


class _CenteredValidationMixin:
    def on_validate_error(self, value: str, error: Exception) -> None:
        self.console.print(Align.center(Text.from_markup(str(error))))


class Prompt(_CenteredValidationMixin, RichPrompt):
    pass


class IntPrompt(_CenteredValidationMixin, RichIntPrompt):
    pass


class Confirm(_CenteredValidationMixin, RichConfirm):
    pass


def _non_negative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def format_watch_progress(item: Mapping[str, Any]) -> str:
    total = _non_negative_int(item.get("total_episodes", 0))
    current = max(1, _non_negative_int(item.get("current_episode", 1), 1))
    position = _non_negative_int(item.get("position", 0.0))
    completed = item.get("status") == "completed"
    media_type = item.get("media_type")

    if completed:
        if media_type == "movie" or total == 1:
            return "Watched"
        return f"All {total} episodes" if total else "Finished"

    progress = f"Episode {min(current, total)}/{total}" if total else f"Episode {current}"
    if position:
        hours, remainder = divmod(position, 3600)
        minutes, seconds = divmod(remainder, 60)
        clock = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        progress += f" · {clock}"
    return progress


def format_last_watched(value: object) -> str:
    try:
        timestamp = datetime.fromisoformat(str(value)).astimezone()
    except (TypeError, ValueError):
        return "Unknown"
    return timestamp.strftime("%b %d, %Y · %H:%M")


class CenteredStatus:
    def __init__(self, console: Console, message: str) -> None:
        self.console = console
        self.spinner = Spinner(
            "line",
            text=Text(message, style="bold bright_cyan"),
            style="bright_cyan",
        )
        self.live = Live(
            self.renderable,
            console=console,
            refresh_per_second=12.5,
            transient=True,
        )

    @property
    def renderable(self) -> Align:
        return Align.center(self.spinner)

    def update(self, message: str) -> None:
        self.spinner.update(text=Text(message, style="bold bright_cyan"))
        self.live.update(self.renderable, refresh=True)

    def __enter__(self) -> "CenteredStatus":
        self.live.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.live.stop()


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
        dynamic_environment = dict(os.environ)
        dynamic_environment.pop("COLUMNS", None)
        dynamic_environment.pop("LINES", None)
        self.console = Console(highlight=False, _environ=dynamic_environment)

    def header(self) -> None:
        if self.console.size.width >= 72:
            logo = Text("\n".join(WORDMARK), style="bold bright_cyan", justify="center")
            subtitle = Text(
                "D I S C O V E R   •   S T R E A M   •   D O W N L O A D",
                style="bold white",
                justify="center",
            )
            content = Group(logo, Text(""), subtitle)
            padding = (1, 3)
        else:
            logo = Text("A N I S T R E A M", style="bold bright_cyan", justify="center")
            subtitle = Text("DISCOVER  •  STREAM  •  DOWNLOAD", style="white", justify="center")
            content = Group(logo, subtitle)
            padding = (1, 2)

        banner = Panel.fit(
            content,
            title="[bold bright_cyan] ANISTREAM CLI [/bold bright_cyan]",
            title_align="center",
            border_style="bright_blue",
            padding=padding,
            box=box.ROUNDED,
        )
        self.console.print(Align.center(banner))
        self.console.print()
        self.console.print()

    def clear_screen(self) -> None:
        self.console.clear()

    def main_screen(self) -> None:
        self.clear_screen()
        self.header()

    def input_screen(self, title: str, description: str) -> None:
        self.clear_screen()
        content = Text(description, style="dim", justify="center")
        panel = Panel.fit(
            content,
            title=f"[bold bright_cyan]{escape(title)}[/]",
            border_style="bright_blue",
            padding=(1, 3),
            box=box.ROUNDED,
        )
        self.console.print(Align.center(panel))

    def pause(self, message: str = "Press Enter to return to the main menu") -> None:
        self.console.print()
        Prompt.ask(
            self._centered_prompt(message, ": "),
            console=self.console,
            default="",
            show_default=False,
            show_choices=False,
        )

    def status(self, message: str) -> CenteredStatus:
        return CenteredStatus(self.console, message)

    def _centered_prompt(self, label: str, suffix: str) -> Text:
        left_padding = max(0, (self.console.size.width - len(label) - len(suffix)) // 2)
        prompt = Text(" " * left_padding)
        prompt.append(label, style="bold")
        return prompt

    def ask(
        self,
        label: str,
        *,
        choices: Sequence[str] | None = None,
        default: str | None = None,
        password: bool = False,
    ) -> str:
        options = list(choices) if choices else None
        suffix = f" [{'/'.join(options)}]" if options else ""
        if default is not None:
            suffix += f" ({default})"
        suffix += ": "
        self.console.print()
        return Prompt.ask(
            self._centered_prompt(label, suffix),
            console=self.console,
            choices=options,
            default=default,
            password=password,
        )

    def ask_int(self, label: str, *, default: int) -> int:
        suffix = f" ({default}): "
        self.console.print()
        return IntPrompt.ask(
            self._centered_prompt(label, suffix),
            console=self.console,
            default=default,
        )

    def confirm(self, label: str, *, default: bool = True) -> bool:
        suffix = " [Y/n]: " if default else " [y/N]: "
        self.console.print()
        return Confirm.ask(
            self._centered_prompt(label, suffix),
            console=self.console,
            default=default,
        )

    def main_choice(self) -> str:
        entries = self.history.all()
        active = sum(item.get("status") != "completed" for item in entries)
        finished = len(entries) - active
        library_summary = (
            f"{active} in progress • {finished} completed"
            if entries
            else "Resume saved movies and series"
        )
        table = Table(box=None, show_header=False, padding=(0, 2), pad_edge=False)
        table.add_row("[bold bright_green]1[/]", "[bold]Continue Watching[/]", library_summary)
        table.add_row("[bold cyan]2[/]", "Link", "Open a supported catalogue URL")
        table.add_row("[bold cyan]3[/]", "Search", "Search across every enabled site")
        table.add_row("[bold cyan]4[/]", "Settings", "Change saved preferences")
        self.console.print(Align.center(table))

        return self.ask("Choose", choices=["1", "2", "3", "4", "q"], default="1")

    def choose_item(
        self,
        title: str,
        items: Sequence[T],
        labels: Iterable[str],
        *,
        clear_screen: bool = True,
    ) -> T | None:
        if clear_screen:
            self.clear_screen()
        else:
            self.console.print()
        table = Table(
            title=f"[bold bright_cyan]{title}[/]",
            title_justify="center",
            box=box.ROUNDED,
            show_header=False,
            padding=(0, 2),
        )
        for index, label in enumerate(labels, 1):
            table.add_row(f"[cyan]{index}[/]", label)
        table.add_row("[dim]0[/]", "Back")
        self.console.print(Align.center(table))
        choice = self.ask_int("Select", default=1)
        if choice == 0:
            return None
        if not 1 <= choice <= len(items):
            self.error("Invalid selection")
            return None
        return items[choice - 1]

    def choose_search_result(self, results: list[SearchResult]) -> SearchResult | None:
        labels = [f"[bold]{escape(item.title)}[/]  [dim][{escape(item.provider_name)}][/]" for item in results]
        return self.choose_item("Search results", results, labels)

    def choose_variant(self, variants: list[CatalogueVariant]) -> CatalogueVariant | None:
        return self.choose_item("Available versions", variants, (escape(item.name) for item in variants))

    def show_catalogue(self, catalogue: Catalogue, history: dict | None) -> None:
        details = Table.grid(padding=(0, 2))
        details.add_row("[dim]Site[/]", escape(catalogue.provider_name))
        details.add_row("[dim]Title[/]", f"[bold]{escape(catalogue.title)}[/]")
        details.add_row("[dim]Version[/]", f"{escape(catalogue.season)} • {escape(catalogue.language)}")
        details.add_row("[dim]Episodes[/]", str(len(catalogue.episodes)))
        if history:
            details.add_row("[dim]Watching[/]", format_watch_progress(history))
        self.console.print(
            Align.center(Panel.fit(details, title="Ready", border_style="bright_blue", box=box.ROUNDED))
        )

    def choose_history_entry(self, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        self.clear_screen()
        if not entries:
            self.console.print(
                Align.center(Panel.fit(
                    "Start watching a movie or series and it will appear here automatically.",
                    title="[bold]Your Library[/]",
                    border_style="bright_blue",
                    box=box.ROUNDED,
                ))
            )
            self.pause()
            return None

        active = sum(item.get("status") != "completed" for item in entries)
        finished = len(entries) - active
        table = Table(
            title=f"Your Library • {active} in progress • {finished} completed",
            box=box.ROUNDED,
            header_style="bold bright_cyan",
            expand=True,
        )
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Title", ratio=3, overflow="fold")
        table.add_column("Progress", ratio=2, overflow="fold")
        for index, item in enumerate(entries, 1):
            completed = item.get("status") == "completed"
            status = "[bold green]Completed[/]" if completed else "[bold yellow]In progress[/]"
            inferred_type = "series" if _non_negative_int(item.get("current_episode", 1), 1) > 1 else "title"
            media_type = str(item.get("media_type") or inferred_type).replace("_", " ").title()
            version = " • ".join(
                value for value in (str(item.get("season", "")), str(item.get("language", ""))) if value
            )
            provider = str(item.get("provider_name") or item.get("provider_id") or "Unknown")
            if not item.get("provider_name"):
                provider = provider.replace("_", " ").title()
            metadata = " • ".join(value for value in (provider, media_type, version) if value)
            table.add_row(
                str(index),
                status,
                (
                    f"[bold]{escape(str(item.get('title') or 'Untitled'))}[/]\n"
                    f"[dim]{escape(metadata)}[/]"
                ),
                (
                    f"[bold]{escape(format_watch_progress(item))}[/]\n"
                    f"[dim]{escape(format_last_watched(item.get('updated_at')))}[/]"
                ),
            )
        self.console.print(Align.center(table))
        self.console.print(Align.center(Text("Select an in-progress title to resume it immediately.", style="dim")))
        while True:
            choice = self.ask_int("Select (0 = Back)", default=1)
            if choice == 0:
                return None
            if 1 <= choice <= len(entries):
                return entries[choice - 1]
            self.error("Invalid selection")

    def completed_history_action(self, item: Mapping[str, Any]) -> str | None:
        restart_label = "Watch again" if item.get("media_type") == "movie" else "Restart from episode 1"
        return self.choose_item(
            escape(str(item.get("title") or "Completed title")),
            ["restart", "choose"],
            [f"[bold green]{restart_label}[/]", "Choose a specific episode"],
            clear_screen=False,
        )

    def choose_action(self) -> str | None:
        items = ["[bold green]Watch[/]  Stream without saving a media file", "[bold blue]Download[/]  Save verified MP4 files"]
        selected = self.choose_item(
            "What would you like to do?",
            ["watch", "download"],
            items,
            clear_screen=False,
        )
        return selected

    def episodes(self, maximum: int, *, single: bool = False, default: int | None = None) -> list[int] | None:
        self.clear_screen()
        description = (
            f"Choose one episode from 1 to {maximum}."
            if single
            else f"Choose episodes from 1 to {maximum} using lists, ranges, all, or latest."
        )
        self.console.print(
            Align.center(
                Panel.fit(
                    description,
                    title="[bold bright_cyan]Episode selection[/]",
                    border_style="bright_blue",
                    box=box.ROUNDED,
                    padding=(1, 2),
                )
            )
        )
        while True:
            hint = f"1-{maximum}" if single else f"1-{maximum}, comma lists, ranges, all, or latest"
            raw = self.ask(f"Episode ({hint})", default=str(default or 1))
            try:
                selected = parse_episode_selection(raw, maximum)
                if single and len(selected) != 1:
                    raise ValueError("Watch accepts one episode at a time")
                return selected
            except ValueError as exc:
                self.error(str(exc))
                if not self.confirm("Try again?", default=True):
                    return None

    def download_report(self, results: list[DownloadResult]) -> None:
        self.clear_screen()
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
        self.console.print(Align.center(table))
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
            self.clear_screen()
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
            self.console.print(Align.center(table))
            choice = self.ask("Setting to change", choices=[str(i) for i in range(10)], default="0")
            if choice == "0":
                return
            if choice == "1":
                self.settings.set("download_directory", self.ask("Download directory", default=str(data["download_directory"])))
            elif choice == "2":
                self.settings.set("download_mode", self.ask("Mode", choices=["sequential", "parallel"], default=data["download_mode"] or "sequential"))
            elif choice == "3":
                self.settings.set("parallel_downloads", self.ask_int("Maximum simultaneous episodes", default=int(data["parallel_downloads"])))
            elif choice in {"4", "5", "6"}:
                key = {"4": "ffmpeg_path", "5": "ffprobe_path", "6": "mpv_path"}[choice]
                value = self.ask("Executable path or command name (blank = auto-detect)", default=str(data[key] or ""))
                self.settings.set(key, value or None)
            elif choice == "7":
                self.settings.set("watch_display", self.ask("Display mode", choices=["window", "terminal"], default=data["watch_display"] or "window"))
            elif choice == "8":
                user_agent = self.ask("Browser User-Agent", default=str(provider.get("user_agent", "")))
                cookie = self.ask("cf_clearance cookie value", default=str(provider.get("cf_clearance", "")), password=True)
                self.settings.set_provider_settings("anime_sama", {"user_agent": user_agent, "cf_clearance": cookie})
            elif choice == "9" and self.confirm("Clear all watch progress and resume data?", default=False):
                self.history.clear()
                self.success("Watch history cleared")
                self.pause("Press Enter to continue")

    def _notification(self, marker: str, style: str, message: str) -> None:
        content = Text()
        content.append(marker, style=style)
        content.append(f" {message}")
        self.console.print(Align.center(content))

    def info(self, message: str) -> None:
        self._notification("•", "blue", message)

    def success(self, message: str) -> None:
        self._notification("+", "green", message)

    def warning(self, message: str) -> None:
        self._notification("!", "yellow", message)

    def error(self, message: str) -> None:
        self._notification("x", "red", message)
