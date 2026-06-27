"""
config/settings.py
===================
Tunable, runtime-mutable experiment configuration.

Unlike config/constants.py (immutable, code-defined), settings here are
meant to be tweaked by a researcher mid-experiment without restarting the
simulation. A background watcher thread polls settings.json's mtime and
hot-swaps the in-memory ExperimentSettings instance when the file changes.

Usage:
    from config.settings import get_settings, start_hot_reload

    start_hot_reload()                 # call once at process startup
    settings = get_settings()          # always returns the latest version
    print(settings.tick_duration_seconds)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from config.constants import WORLD_CONSTANTS

logger = logging.getLogger("npmai_world.settings")

DEFAULT_SETTINGS_PATH = Path(
    os.environ.get("NPMAI_SETTINGS_PATH", "config/settings.json")
)


@dataclass
class ExperimentSettings:
    """All tunable parameters for one run of the simulation.

    Every field has a sane default pulled from WORLD_CONSTANTS where
    applicable, so a fresh checkout works with zero configuration.
    """

    # --- World clock ---
    tick_duration_seconds: float = WORLD_CONSTANTS["tick_duration_seconds"]
    max_ticks: int = 0  # 0 == run forever

    # --- Lifecycle ---
    grace_period_hours: float = WORLD_CONSTANTS["grace_period_hours"]
    elder_age_ticks: int = WORLD_CONSTANTS["elder_age_ticks"]
    max_age_ticks: int = WORLD_CONSTANTS["max_age_ticks"]
    starting_credits: float = WORLD_CONSTANTS["starting_credits"]
    starting_capability_bits: int = WORLD_CONSTANTS["starting_capability_bits"]

    # --- Reproduction ---
    max_children_error_trigger: int = WORLD_CONSTANTS["max_children_error_trigger"]
    min_children_error_trigger: int = WORLD_CONSTANTS["min_children_error_trigger"]
    max_children_success_trigger: int = WORLD_CONSTANTS["max_children_success_trigger"]
    min_children_success_trigger: int = WORLD_CONSTANTS["min_children_success_trigger"]
    children_age_trigger: int = WORLD_CONSTANTS["children_age_trigger"]

    # --- Snapshots ---
    snapshot_agent_interval_ticks: int = WORLD_CONSTANTS["snapshot_agent_interval_ticks"]
    snapshot_territory_interval_ticks: int = WORLD_CONSTANTS["snapshot_territory_interval_ticks"]
    snapshot_world_interval_ticks: int = WORLD_CONSTANTS["snapshot_world_interval_ticks"]

    # --- Gene bank ---
    gene_bank_retention_days: int = WORLD_CONSTANTS["gene_bank_retention_days"]

    # --- Governance ---
    election_quorum_pct: float = WORLD_CONSTANTS["election_quorum_pct"]
    election_pass_pct: float = WORLD_CONSTANTS["election_pass_pct"]

    # --- Event logging ---
    event_batch_size: int = 100
    event_batch_flush_seconds: float = 5.0
    local_buffer_path: str = "data/_local_event_buffer.jsonl"

    # --- Hot-reload ---
    hot_reload_poll_seconds: float = 2.0

    # --- LLM backend defaults (mirrors npmai_agents AgentBrain role defaults) ---
    default_planner_provider: str = "npmai"
    default_planner_model: str = "llama3.2:3b"
    default_coder_provider: str = "npmai"
    default_coder_model: str = "codellama:7b-instruct"
    default_auditor_provider: str = "npmai"
    default_auditor_model: str = "qwen2.5-coder:7b"
    default_verifier_provider: str = "npmai"
    default_verifier_model: str = "llama3.2:3b"
    default_chatter_provider: str = "npmai"
    default_chatter_model: str = "granite3.3:2b"

    # --- Supabase connection (loaded from environment, never hardcoded) ---
    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", ""))
    supabase_key: str = field(default_factory=lambda: os.environ.get("SUPABASE_KEY", ""))

    # --- Redis hot cache ---
    redis_host: str = field(default_factory=lambda: os.environ.get("REDIS_HOST", "localhost"))
    redis_port: int = field(default_factory=lambda: int(os.environ.get("REDIS_PORT", "6379")))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperimentSettings":
        # Only accept known fields so a malformed/old settings.json never
        # crashes the world; unknown keys are dropped with a warning.
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        dropped = set(data) - valid_fields
        if dropped:
            logger.warning("Ignoring unknown settings keys: %s", sorted(dropped))
        return cls(**filtered)


def load_settings(path: Path | str = DEFAULT_SETTINGS_PATH) -> ExperimentSettings:
    """Load settings from a JSON file, falling back to defaults if missing
    or malformed. Always returns a valid ExperimentSettings instance."""
    path = Path(path)
    if not path.exists():
        logger.info("No settings file at %s; using defaults.", path)
        settings = ExperimentSettings()
        save_settings(settings, path)
        return settings
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return ExperimentSettings.from_dict(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load settings from %s (%s); using defaults.", path, exc)
        return ExperimentSettings()


def save_settings(settings: ExperimentSettings, path: Path | str = DEFAULT_SETTINGS_PATH) -> None:
    """Persist settings to disk as pretty JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(settings.to_dict(), fh, indent=2, sort_keys=True)
    tmp_path.replace(path)  # atomic on POSIX


# ---------------------------------------------------------------------------
# Process-wide singleton + hot reload
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_current_settings: ExperimentSettings = load_settings()
_settings_path: Path = DEFAULT_SETTINGS_PATH
_last_mtime: float = 0.0
_watcher_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_on_reload_callbacks: list[Callable[[ExperimentSettings], None]] = []

if _settings_path.exists():
    _last_mtime = _settings_path.stat().st_mtime


def get_settings() -> ExperimentSettings:
    """Thread-safe accessor for the current in-memory settings."""
    with _lock:
        return _current_settings


def register_reload_callback(callback: Callable[[ExperimentSettings], None]) -> None:
    """Register a function to be called with the new settings object every
    time settings.json changes on disk."""
    _on_reload_callbacks.append(callback)


def _reload_if_changed() -> None:
    global _current_settings, _last_mtime
    if not _settings_path.exists():
        return
    mtime = _settings_path.stat().st_mtime
    if mtime <= _last_mtime:
        return
    new_settings = load_settings(_settings_path)
    with _lock:
        _current_settings = new_settings
        _last_mtime = mtime
    logger.info("Hot-reloaded settings from %s", _settings_path)
    for cb in _on_reload_callbacks:
        try:
            cb(new_settings)
        except Exception:  # callbacks must never crash the watcher
            logger.exception("Settings reload callback raised an exception")


def _watch_loop(poll_seconds: float) -> None:
    while not _stop_event.is_set():
        try:
            _reload_if_changed()
        except Exception:
            logger.exception("Error while polling settings file")
        _stop_event.wait(poll_seconds)


def start_hot_reload(path: Path | str = DEFAULT_SETTINGS_PATH, poll_seconds: Optional[float] = None) -> None:
    """Start a daemon thread that watches settings.json and hot-swaps the
    process-wide settings singleton whenever the file's mtime advances.
    Safe to call multiple times; subsequent calls are no-ops while a
    watcher is already running."""
    global _settings_path, _watcher_thread, _last_mtime, _current_settings
    with _lock:
        _settings_path = Path(path)
        _current_settings = load_settings(_settings_path)
        _last_mtime = _settings_path.stat().st_mtime if _settings_path.exists() else 0.0
        if _watcher_thread is not None and _watcher_thread.is_alive():
            return
        interval = poll_seconds or _current_settings.hot_reload_poll_seconds
        _stop_event.clear()
        _watcher_thread = threading.Thread(
            target=_watch_loop, args=(interval,), name="settings-hot-reload", daemon=True
        )
        _watcher_thread.start()
    logger.info("Started settings hot-reload watcher on %s (poll=%.1fs)", _settings_path, interval)


def stop_hot_reload() -> None:
    """Stop the background watcher thread (used in tests / clean shutdown)."""
    _stop_event.set()
    global _watcher_thread
    if _watcher_thread is not None:
        _watcher_thread.join(timeout=5)
        _watcher_thread = None
