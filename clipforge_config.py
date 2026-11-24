"""ClipForge single config file: every credential and setting in one YAML file.

The rest of the code base reads plain environment variables (``GEMINI_API_KEY``,
``LLM_BASE_URL``, ``AWS_*`` ...). This module maps the friendly keys of
``clipforge.config.yaml`` onto those names at process start, so nothing else
has to learn about the file.

Precedence (highest first): a real environment variable, ``.env`` (already
loaded by ``load_dotenv`` callers, which never override), this file. A value
set in Docker or CI therefore always wins over the file.

The file is git-ignored; ``clipforge.config.example.yaml`` documents every key.
Secrets are never logged: ``describe()`` reports only which keys are set.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

CONFIG_ENV = "CLIPFORGE_CONFIG"
DEFAULT_FILENAME = "clipforge.config.yaml"
# Set once a server process applied the file. Pipeline children inherit a
# filtered copy of the server's environment (app.child_env strips secrets on
# purpose); they must not read the file again and get those secrets back.
APPLIED_MARKER = "CLIPFORGE_CONFIG_APPLIED"

# dotted.path in the YAML -> environment variable the code already reads.
KEY_MAP: dict[str, str] = {
    # LLM providers
    "llm.gemini.api_key": "GEMINI_API_KEY",
    "llm.gemini.model": "GEMINI_MODEL",
    "llm.provider": "LLM_PROVIDER",
    "llm.openai_compatible.base_url": "LLM_BASE_URL",
    "llm.openai_compatible.model": "LLM_MODEL",
    "llm.openai_compatible.api_key": "LLM_API_KEY",
    "llm.openai_compatible.timeout_seconds": "LLM_TIMEOUT",
    "llm.openai_compatible.score_batch": "LLM_SCORE_BATCH",
    # Voice / generative media
    "elevenlabs.api_key": "ELEVENLABS_API_KEY",
    "fal.api_key": "FAL_KEY",
    # Social posting through Upload-Post
    "upload_post.api_key": "UPLOAD_POST_API_KEY",
    "upload_post.profile": "UPLOAD_POST_PROFILE",
    # YouTube source downloads (cookies are read from a file, see FILE_KEYS)
    "youtube.download_cookies_file": "YOUTUBE_COOKIES",
    # S3-compatible backup
    "storage.s3.access_key_id": "AWS_ACCESS_KEY_ID",
    "storage.s3.secret_access_key": "AWS_SECRET_ACCESS_KEY",
    "storage.s3.region": "AWS_REGION",
    "storage.s3.bucket": "AWS_S3_BUCKET",
    "storage.s3.public_bucket": "AWS_S3_PUBLIC_BUCKET",
    # Transcription
    "transcription.backend": "TRANSCRIBE_BACKEND",
    "transcription.whisper_model": "WHISPER_MODEL",
    "transcription.whisper_device": "WHISPER_DEVICE",
    "transcription.whisper_compute": "WHISPER_COMPUTE",
    # Processing / machine sizing
    "processing.max_concurrent_jobs": "MAX_CONCURRENT_JOBS",
    "processing.clip_workers": "CLIP_WORKERS",
    "processing.ffmpeg_encoder": "FFMPEG_ENCODER",
    "processing.job_retention_seconds": "JOB_RETENTION_SECONDS",
    # Delivery quality (ClipForge Phase 2)
    "render.quality_preset": "RENDER_PRESET",
    "render.speech_cleanup": "SPEECH_CLEANUP",
    "render.loudness_normalize": "AUDIO_NORMALIZE",
    "render.quality_checks": "RENDER_QC",
    # Branding / finished-short packaging (ClipForge Phase 4)
    "branding.logo_path": "BRAND_LOGO_PATH",
    "branding.logo_corner": "BRAND_LOGO_CORNER",
    "branding.accent_color": "BRAND_ACCENT_COLOR",
    "branding.outro_text": "BRAND_OUTRO_TEXT",
    "branding.credit_text": "BRAND_CREDIT_TEXT",
    "branding.auto_package": "BRAND_AUTO_PACKAGE",
    "branding.auto_intro_card": "BRAND_AUTO_INTRO",
    # Clip selection defaults
    "clips.min_seconds": "CLIP_MIN_SECONDS",
    "clips.source_map": "SOURCE_MAP",
    "clips.sentence_boundaries": "CLIP_REFINE",
    "clips.max_seconds": "CLIP_MAX_SECONDS",
}

# Keys whose YAML value is a path; the env var receives the file's contents.
FILE_KEYS = frozenset({"youtube.download_cookies_file"})

# Flags the code reads as "1"/"0" rather than true/false.
NUMERIC_FLAGS = frozenset({"SPEECH_CLEANUP", "AUDIO_NORMALIZE", "RENDER_QC",
                           "BRAND_AUTO_PACKAGE", "BRAND_AUTO_INTRO", "SOURCE_MAP",
                           "CLIP_REFINE"})
# Keys whose value is a path: made absolute (relative to the config file).
PATH_KEYS = frozenset({"branding.logo_path"})

# Env vars whose value is a credential: never logged or shown.
SECRET_ENV = frozenset({
    "GEMINI_API_KEY", "LLM_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY",
    "UPLOAD_POST_API_KEY", "YOUTUBE_COOKIES", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
})


class ConfigError(ValueError):
    """The config file exists but cannot be used."""


def config_path(base_dir: Optional[Path] = None) -> Path:
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    return (base_dir or Path(__file__).resolve().parent) / DEFAULT_FILENAME


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise ConfigError(f"config keys must be strings, got {key!r}")
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping) and dotted != "env":
            out.update(_flatten(value, dotted + "."))
        else:
            out[dotted] = value
    return out


def _as_env_value(dotted: str, value: Any) -> Optional[str]:
    """A YAML scalar as the string an env var would carry; None = unset."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    raise ConfigError(f"{dotted}: expected a single value, got {type(value).__name__}")


def load(path: Path) -> dict[str, str]:
    """Parse the file into {ENV_VAR: value}. Missing file -> {}."""
    if not path.is_file():
        return {}
    import yaml  # PyYAML; imported lazily so a missing file needs nothing.

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from None
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path.name} must be a mapping at the top level")

    env: dict[str, str] = {}
    for dotted, value in _flatten(data).items():
        if dotted == "env":
            # Escape hatch: any other env var the code reads, verbatim.
            if not isinstance(value, Mapping):
                raise ConfigError("env: must be a mapping of NAME: value")
            for name, raw in value.items():
                if not isinstance(name, str) or not name.isidentifier() or not name.isupper():
                    raise ConfigError(f"env: {name!r} is not an UPPER_CASE variable name")
                text = _as_env_value(f"env.{name}", raw)
                if text is not None:
                    env[name] = text
            continue
        target = KEY_MAP.get(dotted)
        if target is None:
            log.warning("clipforge config: unknown key %s ignored", dotted)
            continue
        text = _as_env_value(dotted, value)
        if text is None:
            continue
        if target in NUMERIC_FLAGS and isinstance(value, bool):
            text = "1" if value else "0"
        if dotted in PATH_KEYS:
            file_path = Path(text).expanduser()
            text = str(file_path if file_path.is_absolute() else path.parent / file_path)
        if dotted in FILE_KEYS:
            file_path = Path(text).expanduser()
            if not file_path.is_absolute():
                file_path = path.parent / file_path
            if not file_path.is_file():
                raise ConfigError(f"{dotted}: file not found")
            text = file_path.read_text(encoding="utf-8")
        env[target] = text
    return env


def apply(path: Optional[Path] = None, environ: Optional[dict] = None) -> list[str]:
    """Copy the file's values into ``environ`` where not already set.

    Returns the env var names that were set from the file. Safe to call more
    than once; a pipeline child that inherited the server's env skips it.
    """
    environ = os.environ if environ is None else environ
    if environ.get(APPLIED_MARKER):
        return []
    path = path or config_path()
    values = load(path)
    applied = []
    for name, value in values.items():
        if name not in environ:
            environ[name] = value
            applied.append(name)
    environ[APPLIED_MARKER] = "1"
    if applied:
        log.info("clipforge config: %d setting(s) from %s", len(applied), path.name)
    return applied


def describe(environ: Optional[Mapping[str, str]] = None) -> dict[str, bool]:
    """Which credentials the server holds, as booleans only (safe to expose)."""
    environ = os.environ if environ is None else environ
    return {
        "gemini": bool(environ.get("GEMINI_API_KEY")),
        "localLlm": bool(environ.get("LLM_BASE_URL")),
        "uploadPost": bool(environ.get("UPLOAD_POST_API_KEY")),
        "uploadPostProfile": bool(environ.get("UPLOAD_POST_PROFILE")),
        "elevenlabs": bool(environ.get("ELEVENLABS_API_KEY")),
        "fal": bool(environ.get("FAL_KEY")),
    }


def redact(text: str, environ: Optional[Mapping[str, str]] = None) -> str:
    """Replace any configured secret value found in ``text`` with ***."""
    environ = os.environ if environ is None else environ
    for name in SECRET_ENV:
        value = environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return text
