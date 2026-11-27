from typing import NamedTuple

from . import limits


class SliderSpec(NamedTuple):
    widget: str
    label: str
    attr: str
    setter: str
    factor: float
    limit: limits.Limit


class ToggleSpec(NamedTuple):
    widget: str
    attr: str
    setter: str


SLIDERS = (
    SliderSpec("sideCutSlider", "sideCutValue", "side_cut", "set_sidecut", 0.01, limits.SIDE_CUT),
    SliderSpec("faceAgeSlider", "faceAgeValue", "face_age", "set_face_age", 0.01, limits.FACE_AGE),
    SliderSpec(
        "chinWidthSlider", "chinWidthValue", "chin_width", "set_chin_width", 0.01, limits.CHIN_WIDTH
    ),
    SliderSpec(
        "frontStrokeSlider",
        "frontStrokeValue",
        "front_line_stroke",
        "set_front_line_stroke",
        1.0,
        limits.FRONT_LINE_STROKE,
    ),
    SliderSpec(
        "backStrokeSlider",
        "backStrokeValue",
        "back_line_stroke",
        "set_back_line_stroke",
        1.0,
        limits.BACK_LINE_STROKE,
    ),
)

TOGGLES = (
    ToggleSpec("showFace", "show_face", "set_show_face"),
    ToggleSpec("showArrow", "show_arrow", "set_arrow"),
    ToggleSpec("showSilhouette", "show_silhouette", "set_silhouette"),
    ToggleSpec("showSideRims", "show_side_rims", "set_side_rims"),
    ToggleSpec("showSideCross", "show_side_cross", "set_side_cross"),
)

EDITING_WIDGETS = tuple(spec.widget for spec in SLIDERS) + tuple(
    spec.widget for spec in TOGGLES
) + ("strokeColorButton", "trackballHost")
