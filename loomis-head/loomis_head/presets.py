import copy
import os
import shutil

from . import diag, jsonio

from .linalg import q_axis_angle

SCHEMA_VERSION = 1

BUILTIN_ORDER = (
    "Default",
)

LEGACY_BUILTIN_NAMES = {
    "Front": "Default",
}


def canonical_name(name: str) -> str:
    return LEGACY_BUILTIN_NAMES.get(name, name)


def _quaternion(yaw_deg: float, pitch_deg: float = 0.0):
    q_yaw = q_axis_angle([0.0, 1.0, 0.0], yaw_deg * 3.141592653589793 / 180.0)
    q_pitch = q_axis_angle([1.0, 0.0, 0.0], pitch_deg * 3.141592653589793 / 180.0)
    return q_yaw * q_pitch


def _builtin_model(yaw: float = 0.0, pitch: float = 0.0) -> dict:
    from .loomis_head_generator import LoomisHead3D

    model = LoomisHead3D()
    model.set_quaternion(_quaternion(yaw, pitch))
    return model.to_preset_dict()


BUILTIN_PRESETS: dict[str, dict] = {
    "Default": _builtin_model(),
}


def builtin_names() -> list[str]:
    return list(BUILTIN_ORDER)


def preset_label(name: str, is_builtin: bool, dirty: bool = False) -> str:
    label = str(name)
    if dirty:
        label += " *"
    return label


def load_button_label(
    preset_name: str | None, selected_name: str | None, dirty: bool = False
) -> str:
    if preset_name and preset_name == selected_name and dirty:
        return "Reload"
    return "Load"


def clean_name(name: str) -> str:
    cleaned = " ".join(str(name).split()).strip()
    if not cleaned:
        raise ValueError("preset name is empty")
    if len(cleaned) > 64:
        cleaned = cleaned[:64].strip()
    if any(ord(ch) < 32 for ch in cleaned):
        raise ValueError("preset name contains control characters")
    return cleaned


class PresetStore:

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.path = os.path.join(directory, "presets.json")

    def _backup_corrupt(self) -> None:
        try:
            if os.path.exists(self.path):
                shutil.copyfile(self.path, self.path + ".bak")
        except OSError as exc:
            diag.warn("could not back up corrupt presets file: %s" % exc)

    def _load_file(self) -> dict:
        data = jsonio.read_json(self.path)
        if not isinstance(data, dict):
            self._backup_corrupt()
            return {}
        presets = data.get("presets")
        if not isinstance(presets, dict):
            self._backup_corrupt()
            return {}
        return {
            str(name): model
            for name, model in presets.items()
            if isinstance(name, str) and isinstance(model, dict)
        }

    def _write_file(self, presets: dict) -> None:
        jsonio.write_json(
            self.path, {"version": SCHEMA_VERSION, "presets": presets}
        )

    def user_presets(self) -> dict[str, dict]:
        return self._load_file()

    def save(self, name: str, model: dict) -> str:
        cleaned = clean_name(name)
        if cleaned in BUILTIN_PRESETS:
            raise ValueError("built-in preset name is reserved")
        presets = self._load_file()
        presets[cleaned] = copy.deepcopy(model)
        self._write_file(presets)
        return cleaned

    def delete(self, name: str) -> bool:
        if name in BUILTIN_PRESETS:
            return False
        presets = self._load_file()
        if name not in presets:
            return False
        del presets[name]
        self._write_file(presets)
        return True

    def all_presets(self) -> list[tuple[str, dict, bool]]:
        result: list[tuple[str, dict, bool]] = [
            (name, copy.deepcopy(BUILTIN_PRESETS[name]), True) for name in BUILTIN_ORDER
        ]
        user = self._load_file()
        for name in sorted(user, key=str.lower):
            if name in BUILTIN_PRESETS:
                continue
            result.append((name, copy.deepcopy(user[name]), False))
        return result

    def get(self, name: str) -> dict | None:
        if not name:
            return None
        if name in BUILTIN_PRESETS:
            return copy.deepcopy(BUILTIN_PRESETS[name])
        user = self._load_file()
        if name in user:
            return copy.deepcopy(user[name])
        current = canonical_name(name)
        if current in BUILTIN_PRESETS:
            return copy.deepcopy(BUILTIN_PRESETS[current])
        return copy.deepcopy(user.get(current))
