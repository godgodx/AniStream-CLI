class AniStreamError(Exception):
    """Base exception for expected application errors."""


class ProviderError(AniStreamError):
    """A catalogue provider could not complete an operation."""


class ResolverError(AniStreamError):
    """An embed host could not be converted into a playable media URL."""


class MediaValidationError(AniStreamError):
    """A file or remote media response failed validation."""


class ToolNotFoundError(AniStreamError):
    """A required external executable could not be found."""


class PlaybackError(AniStreamError):
    """mpv stopped unexpectedly after a media source was selected."""
