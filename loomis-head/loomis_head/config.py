import os


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


LIVE_SAMPLES = _env_int("LOOMIS_LIVE_SAMPLES", 128, 16, 1024)
RECONCILE_INTERVAL = 1000
SYNC_INTERVAL = 100
REDRAW_DEBOUNCE = _env_int("LOOMIS_REDRAW_MS", 40, 0, 1000)
TRANSFORMED_REDRAW_DEBOUNCE = _env_int("LOOMIS_TRANSFORMED_REDRAW_MS", 300, 0, 2000)
TRACE_TRANSFORM = _env_int("LOOMIS_TRACE_TRANSFORM", 0, 0, 1)
LOCAL_SAVE_DEBOUNCE = 150
