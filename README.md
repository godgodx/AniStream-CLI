<div align="center">

# AniStream CLI

**Discover, stream, and download anime, movies, and series from one resilient interactive terminal application.**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Providers: 2](https://img.shields.io/badge/providers-2-7c3aed)](#supported-providers)

</div>

AniStream CLI is a provider-driven media client with source preflight checks, automatic embed fallback, verified MP4 downloads, and persistent watch progress. Its core is deliberately separated from site-specific extraction logic so additional providers and video hosts can be added without redesigning the application.

> [!IMPORTANT]
> AniStream CLI does not host, mirror, or redistribute media. It only processes links exposed by configured third-party providers. You are responsible for complying with the provider's terms and the laws applicable in your location.

## Features

- **Personal watch library** — resume in-progress movies and series directly from the main menu, with clear completion and episode status.
- **Automatic provider detection** — pasted URLs are accepted only when a registered provider supports them.
- **Multi-provider search** — search every enabled catalogue concurrently and keep each result clearly attributed to its source site.
- **Source preflight planning** — select the first embed that works for every requested episode, or the best verified route per episode.
- **Automatic download failover** — if resolution, probing, FFmpeg, or output validation fails, retry the missing episode through the next supported source.
- **Live transfer progress** — follow every episode with a centered percentage bar, transferred size, download speed, ETA, and current failover state.
- **Verified output** — temporary files are promoted only after FFprobe confirms a playable MP4 container with video.
- **Sequential or parallel batches** — choose once, then change the saved preference later in Settings.
- **Watch mode with mpv** — stream without creating a download, resume interrupted playback, track completed episodes, and offer the next episode.
- **Clear completion reports** — see successful, skipped, and failed episodes together with the source used and validation result.
- **Private local state** — settings, watch history, playback state, and downloads remain ignored by Git.

## Supported providers

| Provider | Link detection | Search | Watch | Download |
| --- | :---: | :---: | :---: | :---: |
| [Anime-Sama](https://anime-sama.to/) | Yes | Yes | Yes | Yes |
| [French Stream](https://french-stream.one/) | Yes | Yes | Yes | Yes |

Anime-Sama exposes provider-native variants such as VF and VOSTFR. French Stream currently exposes movie and series variants including French/VF, VOSTFR, TrueFrench/VFF, VFQ, and VO/VOSTENG when the selected title supplies them.

The resolver layer currently recognizes direct media plus embeds served through Embed4me, Sendvid, Sibnet, Vidmoly, Vidzy, OneUpload, Uqload, Smoothpre, Movearnpre, Mivalyo, and Dingtezuni. Third-party host availability can change without notice.

## Requirements

| Dependency | Purpose | Required for |
| --- | --- | --- |
| [Python 3.10+](https://www.python.org/downloads/) | Application runtime | Everything |
| [FFmpeg and FFprobe](https://ffmpeg.org/download.html) | Media transfer, remuxing, and output validation | Download |
| [mpv](https://mpv.io/installation/) | Streaming playback and resume state | Watch |

Python packages are pinned by compatible version ranges in [`requirements.txt`](requirements.txt): Requests, Beautiful Soup, PyCryptodome, and Rich.

## Installation

### 1. Install the external tools

Windows with `winget`:

```powershell
winget install --id Gyan.FFmpeg --exact
winget install --id shinchiro.mpv --exact
```

macOS with Homebrew:

```bash
brew install ffmpeg mpv
```

Ubuntu or Debian:

```bash
sudo apt update
sudo apt install ffmpeg mpv
```

After installation, open a new terminal and verify that the tools are visible:

```text
ffmpeg -version
ffprobe -version
mpv --version
```

AniStream CLI searches `PATH` automatically. On Windows it also checks common WinGet, Scoop, Chocolatey, and Program Files locations. For security, executables stored inside the project tree are never selected automatically; a custom executable can still be chosen explicitly in Settings.

### 2. Clone and create an isolated environment

```bash
git clone https://github.com/godgodx/AniStream-CLI.git
cd AniStream-CLI
python -m venv .venv
```

Activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

Install the application dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Run

```bash
python main.py
```

To install the `anistream` command in the active environment instead:

```bash
python -m pip install -e .
anistream
```

## How it works

```text
Continue Watching -> local library -> select a saved title
      |                                |
      |                                +--> resume the exact episode and position
      v
Link or Search
      |
      v
Provider detection -> structured season and language discovery
      |
      v
Watch or Download
      |
      v
Resolve embeds -> probe media -> rank working routes
      |
      +--> Watch: launch mpv, save progress, offer next episode
      |
      +--> Download: FFmpeg transfer -> FFprobe validation -> final MP4
                         |
                         +--> failure: retry the next supported source
```

### Continue Watching

The local library opens instantly without contacting any provider. It lists every started movie and series with its source site, language and season, last-watched time, completion state, and exact episode or resume position.

Selecting an in-progress title refreshes only that catalogue and resumes the saved episode immediately. Completed movies and series remain visible and can be restarted from the beginning or opened at a specific episode. Legacy history is upgraded automatically when its catalogue is next loaded.

### Link

Paste a catalogue URL. AniStream CLI identifies the owning provider and rejects unsupported sites cleanly. After language and season selection, choose Watch or Download.

### Search

Search every enabled provider from one prompt. Results include their provider name so adding more sites never makes the source ambiguous.

### Download

Select one episode, a range such as `1-6`, a mixed selection such as `1,3,8-12`, or `all`. Before transferring anything, AniStream CLI probes the available sources and prefers the first player with complete coverage. Each episode retains an ordered fallback route.

Downloads use a hidden partial file and are finalized as `Episode 001.mp4` only when FFprobe confirms:

- the `.mp4` extension;
- a recognized MP4-family container;
- at least one video stream;
- a valid video codec and dimensions.

Existing valid episodes are skipped. Invalid or incomplete files are replaced safely.

During transfer, a centered live panel tracks each active episode without flooding the terminal. Percentage and ETA use the media duration reported by FFprobe; when a host does not expose a reliable duration, AniStream still reports transferred size, speed, and state without inventing a percentage.

If no single player covers the entire selection, AniStream combines verified episodes from multiple players before transferring anything. When their combined coverage is still incomplete, it lists the missing episodes and requires explicit confirmation before continuing; the missing episodes are retried during transfer in case a source has recovered.

### Watch

Watch mode resolves and probes candidates in order, then streams the first working source through mpv. If a candidate fails during resolution or preflight, the next candidate is tried automatically. Playback does not create a media download.

For safer playback of third-party streams, AniStream starts mpv without user configuration, external scripts, yt-dlp, file-local configuration, or unsafe playlists. Automatic discovery also rejects mpv executables stored inside the project tree; an explicit path can still be selected in Settings.

AniStream CLI stores the current episode, resume position, completed episodes, and mpv watch-later state. When an episode finishes normally, the next episode is offered immediately and receives a fresh source plan.

Normal mpv window playback is recommended. In-terminal video uses mpv's low-resolution Unicode `tct` output and is enabled only for compatible terminals such as Mintty; unsupported Windows terminals automatically fall back to window mode. On Windows, mpv is attached to a process guard so closing AniStream CLI cannot leave orphaned playback behind.

## Settings and local data

Preferences are requested only when first needed and can later be changed from Settings:

- download directory;
- sequential or parallel download mode;
- parallel worker count;
- FFmpeg, FFprobe, and mpv executable paths;
- window or terminal watch display;
- optional Anime-Sama user agent and Cloudflare clearance cookie.

Runtime state is stored under `data/`; media defaults to `downloads/`. Both locations, virtual environments, partial files, logs, environment files, cookies, session databases, and local launcher scripts are excluded from Git. Treat `data/settings.json` as private if you configure a cookie.

## Architecture

```text
src/anistream/
|-- providers/       Site detection, search, variants, and episode discovery
|-- resolvers/       Embed-host URLs converted into playable media sources
|-- services/        Planning, probing, downloading, playback, history, settings
|-- cli.py           Rich prompts, tables, status messages, and reports
|-- models.py        Provider-neutral domain models
`-- app.py           Interactive application workflow
```

Provider and resolver registries keep the application core independent from any single website. Language metadata is provider-owned and travels through the neutral core as a stable code plus a user-facing label, so a new site can expose its own dub, subtitle, or regional variants without changing the CLI workflow.

### Adding a provider

1. Implement the provider interface in `src/anistream/providers/`.
2. Return one structured `CatalogueVariant` per season/language pair.
3. Register it in `default_providers()` in `src/anistream/providers/__init__.py`.
4. Reuse the neutral catalogue, episode, language, variant, and search-result models.
5. Add focused parsing, language, deep-link, and URL-detection tests with mocked HTTP responses.

> [!TIP]
> AI coding agents should invoke [`$add-anistream-provider`](.agents/skills/add-anistream-provider/SKILL.md) before integrating a site. The repository skill defines the complete provider, language, resolver, failover, security, testing, and documentation contract.

### Adding an embed host

1. Implement the resolver interface in `src/anistream/resolvers/`.
2. Register it in `default_resolvers()`.
3. Return a `ResolvedMedia` value with the required Referer, Origin, and User-Agent headers.
4. Add resolver and probe coverage without depending on a live third-party endpoint.

## Development

Install development dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

Run the test suite:

```bash
python -m pytest
```

Tests cover provider parsing, source planning, download fallback, MP4 validation, history, HTTP cookie isolation, and key CLI behavior. Network-facing behavior may still change when providers or embed hosts update their pages.

## Troubleshooting

**A tool is installed but not detected**

Open a new terminal so its updated `PATH` is loaded, then run the tool's `--version` command. If necessary, set the full executable path in AniStream CLI Settings.

**Watch has sound but no terminal image**

Use the `window` display mode. Terminal rendering depends on capabilities that Windows Terminal, PowerShell, and classic Command Prompt do not consistently expose to mpv.

**A provider suddenly returns no results or sources**

Provider and host pages change independently. Retry later, update AniStream CLI, and include a sanitized error report when opening an issue. Never publish cookies or the contents of your `data/` directory.

## Contributing

Issues and focused pull requests are welcome. Keep provider-specific behavior isolated, add regression coverage for extraction changes, and never commit captured pages or fixtures containing authentication cookies or personal data.

## License

AniStream CLI is available under the [MIT License](LICENSE).
