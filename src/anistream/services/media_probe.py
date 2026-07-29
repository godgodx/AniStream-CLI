from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from anistream.models import ProbeResult, ResolvedMedia
from anistream.utils.http import HttpClient


MAX_PLAYLIST_BYTES = 2_000_000
MP4_PROBE_BYTES = 4 * 1024
MEDIA_PROBE_BYTES = 64 * 1024
URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)


def incomplete_vod_media_playlist(text: str) -> bool:
    upper = text.upper()
    return "#EXTINF:" in upper and "#EXT-X-ENDLIST" not in upper


class RemoteMediaProbe:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def probe(self, media: ResolvedMedia) -> ProbeResult:
        headers = dict(media.headers)
        expected_hls = (
            media.kind == "hls"
            or ".m3u8" in urlparse(media.url).path.casefold()
        )
        maximum = MAX_PLAYLIST_BYTES
        headers["Range"] = f"bytes=0-{maximum - 1}"
        try:
            response = self.http.get(
                media.url,
                headers=headers,
                stream=True,
                timeout=(5, 10),
            )
        except Exception as exc:
            return ProbeResult(False, detail=f"connection failed: {exc}")
        try:
            if response.status_code not in (200, 206):
                return ProbeResult(False, detail=f"HTTP {response.status_code}")
            content_type = (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .casefold()
            )
            body, complete = self._read_probe_body(
                response,
                maximum,
                require_complete=(
                    expected_hls
                    or ".m3u8" in urlparse(str(response.url)).path.casefold()
                    or "mpegurl" in content_type
                ),
            )
            final_url = str(response.url)
            is_hls = (
                expected_hls
                or ".m3u8" in urlparse(final_url).path.casefold()
                or "mpegurl" in content_type
                or body.lstrip().startswith(b"#EXTM3U")
            )
            if is_hls:
                if not body.lstrip().startswith(b"#EXTM3U"):
                    return ProbeResult(
                        False,
                        "hls",
                        "response did not contain an HLS playlist",
                    )
                if not complete:
                    return ProbeResult(False, "hls", "HLS playlist is incomplete")
                self._probe_first_hls_resource(
                    body,
                    final_url,
                    dict(media.headers),
                )
                return ProbeResult(
                    True,
                    "hls",
                    "valid HLS playlist and startup resource",
                )
            if content_type.startswith(("text/", "image/")) or "html" in content_type:
                return ProbeResult(
                    False,
                    detail=f"unexpected content type: {content_type or 'unknown'}",
                )
            if len(body) >= 12 and body[4:8] == b"ftyp":
                return ProbeResult(True, "mp4", "ISO Base Media header detected")
            if content_type.startswith("video/") and len(body) >= 1024:
                return ProbeResult(True, "video", f"video response: {content_type}")
            return ProbeResult(
                False,
                detail="response did not look like playable media",
            )
        except (UnicodeDecodeError, ValueError, OSError) as exc:
            return ProbeResult(False, detail=f"connection failed: {exc}")
        finally:
            response.close()

    @staticmethod
    def _read_probe_body(
        response,
        maximum: int,
        *,
        require_complete: bool,
    ) -> tuple[bytes, bool]:
        body = bytearray()
        complete = True
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            body.extend(chunk)
            if body.lstrip().startswith(b"#EXTM3U"):
                require_complete = True
            if len(body) > maximum:
                complete = False
                break
            if not require_complete and len(body) >= MP4_PROBE_BYTES:
                break
        sample = bytes(body[:maximum])
        if require_complete and response.status_code == 206:
            total = (
                response.headers.get("Content-Range", "")
                .rpartition("/")[2]
                .strip()
            )
            complete = total.isdigit() and int(total) <= len(sample)
        return sample, complete

    def _probe_first_hls_resource(
        self,
        body: bytes,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        text = body.decode("utf-8-sig")
        plain_uris = self._plain_uris(text)
        if not plain_uris:
            raise ValueError("HLS playlist has no playable resource")
        playlist_url = base_url
        if "#EXT-X-STREAM-INF" in text.upper():
            variant_url = urljoin(playlist_url, plain_uris[0])
            playlist, playlist_url = self._fetch_playlist(variant_url, headers)
            text = playlist.decode("utf-8-sig")
            plain_uris = self._plain_uris(text)
            if not plain_uris:
                raise ValueError("HLS variant has no media segment")

        if incomplete_vod_media_playlist(text):
            raise ValueError("HLS VOD playlist is incomplete")

        targets = [urljoin(playlist_url, plain_uris[0])]
        key_line = next(
            (
                line
                for line in text.splitlines()
                if line.lstrip().upper().startswith("#EXT-X-KEY:")
            ),
            "",
        )
        key_match = URI_ATTRIBUTE.search(key_line)
        if key_match is not None:
            targets.insert(0, urljoin(playlist_url, key_match.group(1)))
        for target in targets:
            resource_headers = dict(headers)
            resource_headers["Range"] = f"bytes=0-{MEDIA_PROBE_BYTES - 1}"
            resource = self.http.get(
                target,
                headers=resource_headers,
                stream=True,
                timeout=(5, 10),
            )
            try:
                if resource.status_code not in {200, 206}:
                    raise ValueError(
                        f"HLS startup resource returned HTTP {resource.status_code}"
                    )
                sample, _ = self._read_probe_body(
                    resource,
                    MEDIA_PROBE_BYTES,
                    require_complete=False,
                )
                if not sample:
                    raise ValueError("HLS startup resource is empty")
            finally:
                resource.close()

    def _fetch_playlist(
        self,
        url: str,
        headers: dict[str, str],
    ) -> tuple[bytes, str]:
        playlist_headers = dict(headers)
        playlist_headers["Range"] = f"bytes=0-{MAX_PLAYLIST_BYTES - 1}"
        response = self.http.get(
            url,
            headers=playlist_headers,
            stream=True,
            timeout=(5, 10),
        )
        try:
            if response.status_code not in {200, 206}:
                raise ValueError(
                    f"HLS variant returned HTTP {response.status_code}"
                )
            body, complete = self._read_probe_body(
                response,
                MAX_PLAYLIST_BYTES,
                require_complete=True,
            )
            if not complete:
                raise ValueError("HLS variant playlist is incomplete")
            if not body.lstrip().startswith(b"#EXTM3U"):
                raise ValueError("HLS variant is not a playlist")
            return body, str(response.url)
        finally:
            response.close()

    @staticmethod
    def _plain_uris(text: str) -> list[str]:
        return [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
