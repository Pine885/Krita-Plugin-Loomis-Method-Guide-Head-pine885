"""Persistent live-redraw performance settings.

Kept free of Krita and PyQt imports so the docker can load and save the same
values the test suite validates with plain Python.
"""

import json
import os
import tempfile

SCHEMA_VERSION = 2

DEFAULT_UPDATE_MS = 250
DEFAULT_LIVE_UPDATE = True
DEFAULT_REDUCE_LIVE = True
DEFAULT_LIVE_SAMPLES = 64

UPDATE_MIN = 10
UPDATE_MAX = 2000
SAMPLES_MIN = 16
SAMPLES_MAX = 1024


def clamp_update_ms(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_UPDATE_MS
    return max(UPDATE_MIN, min(UPDATE_MAX, number))


def clamp_live_samples(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIVE_SAMPLES
    return max(SAMPLES_MIN, min(SAMPLES_MAX, number))


def defaults() -> dict:
    return {
        "update_ms": DEFAULT_UPDATE_MS,
        "live_update": DEFAULT_LIVE_UPDATE,
        "reduce_live": DEFAULT_REDUCE_LIVE,
        "live_samples": DEFAULT_LIVE_SAMPLES,
    }


def normalize(values) -> dict:
    data = defaults()
    if not isinstance(values, dict):
        return data
    update_ms = values.get("update_ms", data["update_ms"])
    try:
        release_mode = int(update_ms) == 0
    except (TypeError, ValueError):
        release_mode = False
    if "live_update" in values:
        data["live_update"] = bool(values["live_update"])
    else:
        # Older files used update_ms=0 to mean "update on release".
        data["live_update"] = not release_mode
    if release_mode:
        update_ms = data["update_ms"]
    data["update_ms"] = clamp_update_ms(update_ms)
    data["reduce_live"] = bool(values.get("reduce_live", data["reduce_live"]))
    data["live_samples"] = clamp_live_samples(
        values.get("live_samples", data["live_samples"])
    )
    return data


class PerfSettingsStore:
    """JSON-backed performance settings with atomic writes."""

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.path = os.path.join(directory, "settings.json")

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return defaults()
        return normalize(data)

    def save(self, update_ms, live_update, reduce_live, live_samples) -> None:
        os.makedirs(self.directory, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "update_ms": clamp_update_ms(update_ms),
            "live_update": bool(live_update),
            "reduce_live": bool(reduce_live),
            "live_samples": clamp_live_samples(live_samples),
        }
        fd, temp_path = tempfile.mkstemp(
            prefix=".settings-", suffix=".json", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_path, self.path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
