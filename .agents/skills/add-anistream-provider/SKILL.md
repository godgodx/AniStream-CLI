---
name: add-anistream-provider
description: Add, update, or repair a media catalogue provider in AniStream CLI, including strict URL detection, cross-provider search attribution, structured season and language variants, episode and embed extraction, provider registration, private settings, resolver integration, and regression tests. Use whenever an agent is asked to support a new streaming site or adapt an existing provider after a site change.
---

# Add an AniStream Provider

Implement site-specific behavior behind AniStream's neutral provider contract. Preserve the existing search, Watch, Download, history, automatic fallback, and MP4-verification workflows.

## Inspect the contract first

Read these files before editing:

- `src/anistream/models.py`
- `src/anistream/providers/base.py`
- `src/anistream/providers/registry.py`
- `src/anistream/providers/__init__.py`
- one complete provider such as `src/anistream/providers/anime_sama.py`
- `src/anistream/resolvers/base.py` and `src/anistream/resolvers/registry.py`
- focused provider, resolver, HTTP, planner, player, and downloader tests

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

## Register and configure the provider

Export the provider and instantiate it in `default_providers()` in `src/anistream/providers/__init__.py`. Keep this as the only enabled-provider registration point.

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
- registry construction and any new settings migration;
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
- Search results visibly identify their source site.
- Language selection remains provider-neutral and each choice loads the expected catalogue.
- Watch and Download receive the same ordered episode candidates and retain automatic fallback.
- History and download paths remain isolated by provider, catalogue URL, season, and language.
- Existing providers and the full offline test suite still pass.
- Documentation lists the provider and any new dependency or private setting.
- No runtime data, downloads, cookies, secrets, hardcoded personal paths, or launcher files are staged.
