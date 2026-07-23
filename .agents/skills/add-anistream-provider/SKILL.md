---
name: add-anistream-provider
description: Add, update, or repair a media catalogue provider in AniStream CLI, including strict URL detection, configurable source selection, cross-provider search attribution, structured season and language variants, episode and embed extraction, provider registration, private settings, resolver integration, and regression tests. Use whenever an agent is asked to support a new streaming site or adapt an existing provider after a site change.
---

# Add an AniStream Provider

Implement site-specific behavior behind AniStream's neutral provider contract. Preserve configurable source selection, Search, Link, Watch, Download, Local, history, automatic fallback, and MP4-verification workflows.

## Inspect the contract first

Read these files before editing:

- `src/anistream/models.py`
- `src/anistream/providers/base.py`
- `src/anistream/providers/registry.py`
- `src/anistream/providers/__init__.py`
- one complete provider such as `src/anistream/providers/anime_sama.py`
- `src/anistream/services/settings.py`
- provider selection in `src/anistream/app.py` and `src/anistream/cli.py`
- `src/anistream/resolvers/base.py` and `src/anistream/resolvers/registry.py`
- focused provider, settings, application, CLI, resolver, HTTP, planner, player, and downloader tests

Check the worktree before changing files. Preserve unrelated user changes and runtime data.

## Keep provider boundaries strict

Implement the four `Provider` methods in a dedicated module under `src/anistream/providers/`:

1. `matches(url)` must validate parsed hostnames and appropriate paths. Never accept a site with substring matching.
2. `search(query)` must return `SearchResult` values carrying the provider's stable `provider_id`, display name, title, and canonical catalogue URL.
3. `variants(url)` must return every playable season and language as separate `CatalogueVariant` values.
4. `catalogue(url)` must return one language-specific `Catalogue` with a correctly aligned episode matrix and ordered embed candidates.

Keep HTML, JavaScript, API, route, and language-code knowledge inside the provider. Do not add site names, route shapes, or language codes to `app.py`, the CLI, history, downloader, player, or planner.

## Model languages without global assumptions

Treat provider language codes as opaque, provider-owned identifiers:

```python
language = MediaLanguage(code="provider-native-code", label="Human label")
variant = CatalogueVariant(
    name="Season 1 - Human label",
    url=language_specific_url,
    season="Season 1",
    language=language,
)
```

Apply these invariants:

- Normalize `MediaLanguage.code` through the model and keep it stable for that provider.
- Use a concise user-facing label; include a familiar site code when it prevents ambiguity.
- Return one variant per season/language pair. Never merge episodes from two languages.
- Preserve the selected `MediaLanguage` unchanged in the resulting `Catalogue`.
- Handle a direct season/language URL without silently replacing it with another language.
- When seasons live on separate pages, follow the provider's public relationship metadata and emit variants for every reachable season/language pair.
- Keep the current page usable when optional related-season discovery partially fails.
- Discover variants from the site's own metadata when possible. Use a provider-local candidate list only as a documented fallback.
- Do not create a global enum of languages: future sites may expose dub, subtitle, regional, or mixed variants unknown today.

The neutral core uses the label for display and download folders, the code for durable metadata, and the language-specific catalogue URL for isolation of history and mpv resume state.

## Register, configure, and expose the provider

Export the provider and instantiate it in `default_providers()` in `src/anistream/providers/__init__.py`. This function declares every available provider; it does not decide which providers are active for the current user.

Use a stable, lowercase `provider.id`. Source selection is opt-out:

- `SettingsStore.provider_enabled(provider_id)` returns true unless the ID is present in `disabled_providers`.
- `Application.available_providers` must retain every value returned by `default_providers()`.
- `Application._refresh_providers()` must build the active `ProviderRegistry` from the saved choices.
- Pass every available provider, not only active providers, to **Settings > Sources** so a disabled source can be re-enabled.
- Do not add a new provider to `disabled_providers` or create a migration merely to enable it. An absent ID means enabled, including for existing users.

An inactive provider must be excluded from Search, direct Link handling, and online Continue Watching refresh. A matching disabled Link must be identified as disabled rather than unsupported. Local library discovery and verified local playback must remain available without contacting that provider. If all sources are disabled, Search must stop cleanly and direct the user to **Settings > Sources**.

Read provider-specific configuration with `SettingsStore.provider_settings(provider_id)`. Save it with `set_provider_settings()` only when required. Keep all provider configuration under the `providers` namespace and never hardcode credentials, cookies, tokens, user paths, or captured responses.

If the provider needs its own session or headers, construct an appropriately scoped HTTP client. Restrict cookies to exact catalogue hosts. Confirm with an HTTP regression test that provider cookies cannot reach embed or media hosts.

## Handle embed hosts separately

Provider code should expose embed URLs, not duplicate host-resolution logic. If an embed host is unsupported:

1. Add a focused resolver under `src/anistream/resolvers/`.
2. Register it in `default_resolvers()`.
3. Return all required Referer, Origin, and User-Agent headers in `ResolvedMedia`.
4. Test resolution with mocked responses.

Do not weaken source probing, automatic fallback, download validation, or mpv process handling to accommodate a provider.

## Add deterministic coverage

Use mocked HTTP responses or small sanitized inline fixtures. Do not make the automated suite depend on a live third-party site.

Cover at least:

- accepted canonical and alternate domains plus rejected lookalike domains;
- empty search behavior and provider attribution on results;
- root catalogue discovery and direct deep links;
- related seasons stored on separate catalogue pages, including partial relationship failures;
- every supported language-code mapping and the displayed label;
- distinct variants for each season/language pair;
- equality between the selected variant language and catalogue language;
- episode numbering, missing-player alignment, and unusable embed filtering;
- HTTP failures, malformed responses, and no-variant/no-episode errors;
- availability through `default_providers()` and default-enabled behavior for the stable provider ID;
- active-registry filtering, persisted disable/enable behavior, disabled-Link messaging, and the zero-enabled Search path;
- visibility of disabled sources in Settings and continued independence of Local;
- any genuinely required settings migration;
- any new resolver and its required request headers.

Run from the repository root:

```text
python -m pytest -q
python -m compileall -q main.py src
```

Optionally perform one sanitized live smoke check after deterministic tests pass. Report it separately because third-party availability can change. Never print cookies or commit live page captures.

## Definition of done

Confirm all of the following before handing off:

- Link detection chooses only the intended provider.
- The provider appears in **Settings > Sources**, is enabled by default, and can be disabled and re-enabled persistently.
- Search and Link honor the active source selection, while Local remains independent.
- Search results visibly identify their source site.
- Language selection remains provider-neutral and each choice loads the expected catalogue.
- Watch and Download receive the same ordered episode candidates and retain automatic fallback.
- History and download paths remain isolated by provider, catalogue URL, season, and language.
- Existing providers and the full offline test suite still pass.
- Documentation lists the provider, source-selection behavior, and any new dependency or private setting.
- No runtime data, downloads, cookies, secrets, hardcoded personal paths, or launcher files are staged.
