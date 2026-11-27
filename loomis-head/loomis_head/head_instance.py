import math
import re
import xml.etree.ElementTree as ET

from . import diag, limits, timing
from .config import LIVE_SAMPLES, TRACE_TRANSFORM
from .linalg import q_axis_angle
from .loomis_head_generator import LoomisHead3D

LAYER_PREFIX = "_HeadGuide"

SCALE_TOLERANCE = 0.003
ROTATION_TOLERANCE = math.radians(0.5)
ROTATION_DISAGREEMENT = math.radians(1.0)
NORMALIZE_SETTLE_POLLS = 3


def scaled_placement(center, offset, scale, factor, minimum, maximum):
    new_scale = max(minimum, min(maximum, scale * factor))
    effective = new_scale / scale if scale else 1.0
    position = (
        center[0] + effective * offset[0],
        center[1] + effective * offset[1],
    )
    return new_scale, position


def _wrap_angle(angle: float) -> float:
    angle = math.fmod(angle + math.pi, 2.0 * math.pi)
    if angle <= 0.0:
        angle += 2.0 * math.pi
    return angle - math.pi


def _rotated_offset(offset, angle):
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    return (
        offset[0] * cos_a - offset[1] * sin_a,
        offset[0] * sin_a + offset[1] * cos_a,
    )


def _matrix_rotation(matrix):
    if matrix is None:
        return None
    try:
        m11 = float(matrix.m11())
        m12 = float(matrix.m12())
        m21 = float(matrix.m21())
        m22 = float(matrix.m22())
    except Exception:
        return None
    if not all(math.isfinite(value) for value in (m11, m12, m21, m22)):
        return None
    det = m11 * m22 - m12 * m21
    if det <= 0.0:
        return None
    scale = math.sqrt(det)
    if scale <= 1e-9:
        return None
    a, b = m11 / scale, m12 / scale
    c, d = m21 / scale, m22 / scale
    if abs(a - d) > 1e-3 or abs(b + c) > 1e-3:
        return None
    if abs(math.hypot(a, b) - 1.0) > 1e-3 or abs(math.hypot(c, d) - 1.0) > 1e-3:
        return None
    return math.atan2(b, a)


def _parent_shape(shape):
    getter = getattr(shape, "parentShape", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


def _matrix_from(shape, name):
    getter = getattr(shape, name, None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


def _placement_matrix(shape):
    local = _matrix_from(shape, "transformation")
    absolute = _matrix_from(shape, "absoluteTransformation")
    if _parent_shape(shape) is not None:
        return (local if local is not None else absolute), True

    absolute_angle = _matrix_rotation(absolute)
    if absolute_angle is not None and abs(absolute_angle) > ROTATION_TOLERANCE:
        return absolute, False
    if local is not None:
        return local, True
    return absolute, False


def _shape_rotation(shape):
    matrix, is_local = _placement_matrix(shape)
    angle = _matrix_rotation(matrix)
    if angle is None:
        return None
    if is_local and _parent_shape(shape) is None:
        return -angle
    return angle


def _mean_rotation(angles):
    if not angles:
        return None
    sin_sum = sum(math.sin(angle) for angle in angles)
    cos_sum = sum(math.cos(angle) for angle in angles)
    if abs(sin_sum) < 1e-12 and abs(cos_sum) < 1e-12:
        return None
    mean = math.atan2(sin_sum, cos_sum)
    for angle in angles:
        if abs(_wrap_angle(angle - mean)) > ROTATION_DISAGREEMENT:
            return None
    return mean

_NAME_RE = re.compile(r"^_HeadGuide_(\d+)$")
_LEGACY_RE = re.compile(r"^Loomis Method Head Guide(?: (\d+))?$")
_COPY_PREFIX_RE = re.compile(r"^copy\s+of\s+", re.IGNORECASE)
_COPY_SUFFIX_RE = re.compile(
    r"(?:\s*[\(\[]?\s*(?:copy|duplicate)(?:\s*(?:#\s*)?\d+)?\s*[\)\]]?"
    r"|\s*[\-_]\s*copy(?:\s*(?:#\s*)?\d+)?)$",
    re.IGNORECASE,
)


def strip_copy_name(name: str) -> str | None:
    text = (name or "").strip()
    changed = False
    while True:
        match = _COPY_PREFIX_RE.match(text)
        if match:
            text = text[match.end():].strip()
            changed = True
            continue
        match = _COPY_SUFFIX_RE.search(text)
        if match:
            text = text[: match.start()].rstrip()
            changed = True
            continue
        break
    if not changed or not text:
        return None
    return text


def head_base_name(name: str) -> str | None:
    name = name or ""
    if _NAME_RE.match(name) or _LEGACY_RE.match(name):
        return name
    base = strip_copy_name(name)
    if base and (_NAME_RE.match(base) or _LEGACY_RE.match(base)):
        return base
    return None


def is_head_name(name: str) -> bool:
    return head_base_name(name) is not None


def _name_index(name: str) -> int:
    name = head_base_name(name) or name or ""
    match = _NAME_RE.match(name)
    if match:
        return int(match.group(1))
    match = _LEGACY_RE.match(name)
    if not match:
        return 0
    number = match.group(1)
    return int(number) if number else 1


def owning_layer(node):
    try:
        if str(node.type()).endswith("mask"):
            return node.parentNode()
    except Exception:
        pass
    return node


def transform_masks(layer) -> list[object]:
    found = []
    try:
        children = layer.childNodes()
    except Exception:
        return found
    for node in children:
        try:
            if str(node.type()) == "transformmask":
                found.append(node)
        except Exception:
            continue
    return found


def has_transformed_placement(instance) -> bool:
    for mask in transform_masks(instance.layer):
        try:
            transform = mask.finalAffineTransform()
            if transform is not None and not transform.isIdentity():
                return True
        except Exception:
            return True
    return False


def _zero_rotation(xml: str):
    try:
        root = ET.fromstring(xml)
    except Exception:
        return None
    free = root.find(".//free_transform")
    if free is None:
        return None
    changed = False
    for tag in ("aX", "aY", "aZ", "boundsRotation"):
        node = free.find(tag)
        if node is None:
            continue
        try:
            if float(node.get("value", "0")) == 0.0:
                continue
        except (TypeError, ValueError):
            pass
        node.set("value", "0")
        changed = True
    if not changed:
        return None
    return ET.tostring(root, encoding="unicode")


def reset_transform_rotation(instance) -> bool:
    changed = False
    for mask in transform_masks(instance.layer):
        try:
            xml = mask.toXML()
            updated = _zero_rotation(xml)
            if updated is None or updated == xml:
                continue
            if mask.fromXML(updated):
                changed = True
        except Exception:
            continue
    return changed


def next_name(doc, reserved=()) -> str:
    highest = 0
    for node in iter_vector_layers(doc):
        try:
            name = node.name()
        except Exception:
            continue
        if is_head_name(name):
            highest = max(highest, _name_index(name))
    for name in reserved:
        highest = max(highest, _name_index(name))
    return "%s_%d" % (LAYER_PREFIX, highest + 1)


def iter_vector_layers(doc) -> list[object]:
    try:
        root = doc.rootNode()
    except Exception:
        return []

    found: list[object] = []

    def walk(nodes):
        for node in nodes:
            try:
                if node.type() == "vectorlayer":
                    found.append(node)
            except Exception:
                continue
            try:
                children = node.childNodes()
            except Exception:
                children = []
            if children:
                walk(children)

    try:
        walk(root.childNodes())
    except Exception:
        pass
    return found


class HeadInstance:
    def __init__(self, name, layer, model: LoomisHead3D, point_scale=(1.0, 1.0)):
        self.name = name
        self.layer = layer
        self.model = model
        self.point_scale_x = float(point_scale[0])
        self.point_scale_y = float(point_scale[1])
        self.last_shape_states = self.shape_states()
        self._refresh_gate()
        self.last_placement = None
        self.needs_normalize = False
        self.stable_polls = 0
        self.rotation_applied = False
        self._refresh_placement_baseline()
        self.preset_name: str | None = None
        self.preset_dirty = False

    def _layer_bounds(self):
        try:
            box = self.layer.bounds()
        except Exception:
            return None
        if box is None or box.isNull() or box.isEmpty():
            return None
        return (box.x(), box.y(), box.width(), box.height())

    def _shape_signature(self):
        try:
            shapes = self.layer.shapes()
        except Exception:
            return None
        for shape in shapes:
            try:
                if "arrow" in (shape.name() or "").lower():
                    continue
            except Exception:
                continue
            try:
                point = shape.position()
                matrix, _is_local = _placement_matrix(shape)
                return (
                    float(point.x()),
                    float(point.y()),
                    float(matrix.m11()),
                    float(matrix.m12()),
                    float(matrix.m21()),
                    float(matrix.m22()),
                )
            except Exception:
                return None
        return None

    def _refresh_gate(self):
        self.last_bounds = self._layer_bounds()
        self.last_signature = self._shape_signature()

    @staticmethod
    def _point_scale(doc):
        try:
            return (doc.xRes() / 72.0, doc.yRes() / 72.0)
        except (AttributeError, RuntimeError, TypeError):
            return (1.0, 1.0)

    @classmethod
    def create(cls, doc, model: LoomisHead3D | None = None, name: str | None = None) -> "HeadInstance":
        name = name or next_name(doc)
        layer = doc.createVectorLayer(name)
        doc.rootNode().addChildNode(layer, None)

        if model is None:
            model = LoomisHead3D()
            model.set_position_x(doc.width() // 2)
            model.set_position_y(doc.height() // 2)

        instance = cls(name, layer, model, cls._point_scale(doc))
        return instance

    @classmethod
    def attach(cls, doc, name: str, layer, model: LoomisHead3D | None = None) -> "HeadInstance":
        instance = cls(
            name,
            layer,
            model if model is not None else LoomisHead3D(),
            cls._point_scale(doc),
        )
        instance.last_shape_states = instance.shape_states()
        instance._refresh_placement_baseline()
        return instance

    def _head_shapes(self):
        try:
            shapes = self.layer.shapes()
        except Exception:
            return None
        found = []
        for shape in shapes:
            try:
                name = shape.name()
            except Exception:
                continue
            if "arrow" in (name or "").lower():
                continue
            found.append((shape, name))
        return found

    def shape_states(self):
        shapes = self._head_shapes()
        if shapes is None:
            return []
        states = []
        for shape, name in shapes:
            try:
                point = shape.position()
                box = shape.boundingBox()
                center = None
                if box is not None and not box.isNull() and not box.isEmpty():
                    center = (
                        box.x() + box.width() / 2.0,
                        box.y() + box.height() / 2.0,
                    )
                states.append((name, (float(point.x()), float(point.y())), center))
            except Exception:
                continue
        return states

    def placement_delta(self):
        bounds = self._layer_bounds()
        signature = self._shape_signature()
        if (
            bounds is not None
            and bounds == self.last_bounds
            and signature == self.last_signature
        ):
            return None, None, self.last_shape_states, False
        self.last_bounds = bounds
        self.last_signature = signature
        if TRACE_TRANSFORM:
            self._trace_shapes()

        current = self.shape_states()
        previous = self.last_shape_states
        if previous is None or len(previous) != len(current):
            return None, self.scale_factor(), current, True

        deltas = []
        for (_, prev_pos, prev_center), (_, cur_pos, cur_center) in zip(previous, current):
            dx = (cur_pos[0] - prev_pos[0]) * self.point_scale_x
            dy = (cur_pos[1] - prev_pos[1]) * self.point_scale_y
            if prev_center is not None and cur_center is not None:
                box_dx = (cur_center[0] - prev_center[0]) * self.point_scale_x
                box_dy = (cur_center[1] - prev_center[1]) * self.point_scale_y
                if abs(box_dx) >= 0.5 or abs(box_dy) >= 0.5:
                    dx, dy = box_dx, box_dy
            if abs(dx) >= 0.5 or abs(dy) >= 0.5:
                deltas.append((dx, dy))

        if not deltas:
            return None, self.scale_factor(), current, True
        dx = sum(item[0] for item in deltas) / len(deltas)
        dy = sum(item[1] for item in deltas) / len(deltas)
        return (dx, dy), self.scale_factor(), current, True

    def bounds_center(self):
        boxes, _, _ = self._shape_boxes()
        if boxes is None:
            return None
        if boxes:
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            return ((left + right) / 2.0, (top + bottom) / 2.0)

        bounds = self._layer_bounds()
        if bounds is None:
            return None
        x, y, width, height = bounds
        return (
            (x + width / 2.0) / self.point_scale_x,
            (y + height / 2.0) / self.point_scale_y,
        )

    def _shape_boxes(self):
        shapes = self._head_shapes()
        if shapes is None:
            return None, [], []
        boxes = []
        factors = []
        rotations = []
        rotations_ok = True
        for shape, _name in shapes:
            try:
                box = shape.boundingBox()
            except Exception:
                box = None
            if box is not None and not box.isNull() and not box.isEmpty():
                boxes.append((box.x(), box.y(), box.width(), box.height()))
            try:
                getter = getattr(shape, "absoluteTransformation", None)
                matrix = getter() if getter is not None else shape.transformation()
                det = matrix.m11() * matrix.m22() - matrix.m12() * matrix.m21()
            except Exception:
                det = 0.0
            if det > 0.0 and math.isfinite(det):
                factors.append(math.sqrt(det))
            if rotations_ok:
                angle = _shape_rotation(shape)
                if angle is None:
                    rotations_ok = False
                else:
                    rotations.append(angle)
        if not rotations_ok:
            rotations = []
        return boxes, factors, rotations

    @staticmethod
    def _matrix_text(matrix):
        if matrix is None:
            return "none"
        try:
            return "m11=%g m12=%g m21=%g m22=%g" % (
                matrix.m11(),
                matrix.m12(),
                matrix.m21(),
                matrix.m22(),
            )
        except Exception:
            return "error"

    @staticmethod
    def _shape_matrix_text(shape, attribute):
        getter = getattr(shape, attribute, None)
        if getter is None:
            return "none"
        try:
            return HeadInstance._matrix_text(getter())
        except Exception:
            return "error"

    def _trace_shapes(self):
        for shape, name in self._head_shapes() or []:
            try:
                point = shape.position()
                position = "(%g,%g)" % (point.x(), point.y())
            except Exception:
                position = "?"
            try:
                box = shape.boundingBox()
                bounding = "(%g,%g,%g,%g)" % (
                    box.x(),
                    box.y(),
                    box.width(),
                    box.height(),
                )
            except Exception:
                bounding = "?"
            try:
                parent = type(shape.parentShape()).__name__
            except Exception:
                parent = "?"
            diag.warn(
                "transform shape=%r pos=%s box=%s local[%s] absolute[%s] parent=%s"
                % (
                    name,
                    position,
                    bounding,
                    self._shape_matrix_text(shape, "transformation"),
                    self._shape_matrix_text(shape, "absoluteTransformation"),
                    parent,
                )
            )

    def _trace_decision(self, changed, angle, factor, delta):
        _boxes, _factors, rotations = self._shape_boxes()
        baseline = None
        if self.last_placement is not None:
            baseline = self.last_placement.get("rotation")
        if not changed:
            decision = "skip (unchanged)"
        elif angle is not None:
            decision = "rotate"
        elif factor is not None:
            decision = "scale"
        elif delta is not None:
            decision = "move"
        else:
            decision = "none"
        diag.warn(
            "transform rotations=%r baseline_rotation=%r rotation_delta=%r decision=%s"
            % (
                [round(value, 6) for value in rotations],
                baseline,
                angle,
                decision,
            )
        )

    def placement_metrics(self):
        boxes, factors, rotations = self._shape_boxes()
        if boxes is None:
            return None

        transform = None
        if factors:
            transform = math.exp(sum(math.log(item) for item in factors) / len(factors))

        if boxes:
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            center = (
                (left + right) / 2.0 * self.point_scale_x,
                (top + bottom) / 2.0 * self.point_scale_y,
            )
            extent = (right - left, bottom - top)
            return {
                "center": center,
                "extent": extent,
                "transform": transform,
                "rotation": _mean_rotation(rotations),
            }

        bounds = self._layer_bounds()
        if bounds is None:
            return None
        x, y, width, height = bounds
        return {
            "center": (x + width / 2.0, y + height / 2.0),
            "extent": (width, height),
            "transform": transform,
            "rotation": _mean_rotation(rotations),
        }

    def _refresh_placement_baseline(self, keep_rotation=False):
        previous = None
        if self.last_placement is not None:
            previous = self.last_placement.get("rotation")
        metrics = self.placement_metrics()
        if metrics is not None:
            metrics["offset"] = (
                self.model.position_x - metrics["center"][0],
                self.model.position_y - metrics["center"][1],
            )
            if keep_rotation and self.last_placement is not None:
                metrics["rotation"] = self.last_placement.get("rotation")
        self.last_placement = metrics
        if TRACE_TRANSFORM:
            diag.warn(
                "transform baseline keep_rotation=%s rotation %r -> %r"
                % (
                    keep_rotation,
                    previous,
                    None if metrics is None else metrics.get("rotation"),
                )
            )

    def scale_factor(self):
        baseline = self.last_placement
        current = self.placement_metrics()
        if baseline is None or current is None:
            return None

        factor = None
        before = baseline.get("transform")
        after = current.get("transform")
        if before is not None and after is not None:
            factor = after / before
        else:
            before_extent = baseline.get("extent")
            after_extent = current.get("extent")
            if before_extent and after_extent:
                ex0, ey0 = before_extent
                ex1, ey1 = after_extent
                if ex0 > 1e-6 and ey0 > 1e-6 and ex1 > 0.0 and ey1 > 0.0:
                    factor = math.sqrt((ex1 / ex0) * (ey1 / ey0))

        if factor is None or abs(factor - 1.0) < SCALE_TOLERANCE:
            return None
        return factor

    def apply_scale_factor(self, factor, keep_rotation=False) -> bool:
        baseline = self.last_placement
        current = self.placement_metrics()
        if baseline is None or current is None or factor is None:
            return False
        scale, position = scaled_placement(
            current["center"],
            baseline.get("offset", (0.0, 0.0)),
            self.model.scale,
            factor,
            limits.SCALE.minimum,
            limits.SCALE.maximum,
        )
        self.model.set_scale(scale)
        self.model.set_position_x(position[0])
        self.model.set_position_y(position[1])
        self._refresh_placement_baseline(keep_rotation=keep_rotation)
        self.last_shape_states = self.shape_states()
        self._refresh_gate()
        return True

    def rotation_delta(self):
        baseline = self.last_placement
        current = self.placement_metrics()
        if baseline is None or current is None:
            return None
        before = baseline.get("rotation")
        after = current.get("rotation")
        if before is None or after is None:
            return None
        delta = _wrap_angle(after - before)
        if abs(delta) < ROTATION_TOLERANCE:
            return None
        return delta

    def apply_rotation_delta(self, angle, factor=None) -> bool:
        baseline = self.last_placement
        current = self.placement_metrics()
        if baseline is None or current is None:
            return False
        scale = self.model.scale
        effective = 1.0
        if factor is not None:
            scale = max(
                limits.SCALE.minimum,
                min(limits.SCALE.maximum, self.model.scale * factor),
            )
            effective = scale / self.model.scale if self.model.scale else 1.0
        offset = _rotated_offset(baseline.get("offset", (0.0, 0.0)), angle)
        position = (
            current["center"][0] + effective * offset[0],
            current["center"][1] + effective * offset[1],
        )
        self.model.set_scale(scale)
        self.model.set_position_x(position[0])
        self.model.set_position_y(position[1])
        self.model.set_quaternion(
            q_axis_angle((0.0, 0.0, 1.0), -angle) * self.model.q
        )
        self._refresh_placement_baseline()
        self.last_shape_states = self.shape_states()
        self._refresh_gate()
        self.rotation_applied = True
        return True

    def take_rotation_applied(self) -> bool:
        applied = self.rotation_applied
        self.rotation_applied = False
        return applied

    def adopt_placement(self):
        delta, factor, current, changed = self.placement_delta()
        self.last_shape_states = current
        angle = self.rotation_delta() if changed else None
        if TRACE_TRANSFORM and changed:
            self._trace_decision(changed, angle, factor, delta)
        if angle is not None:
            self.apply_rotation_delta(angle, factor)
            if factor is not None:
                self.needs_normalize = True
            return True, changed
        if factor is not None:
            self.apply_scale_factor(factor, keep_rotation=True)
            self.needs_normalize = True
            return True, changed
        if delta is not None:
            self.model.set_position_x(self.model.position_x + delta[0])
            self.model.set_position_y(self.model.position_y + delta[1])
            self._refresh_placement_baseline(keep_rotation=True)
            return True, changed
        return False, changed

    def _selected_shape_names(self):
        names = []
        try:
            for shape in self.layer.shapes():
                try:
                    if shape.isSelected():
                        names.append(shape.name())
                except Exception:
                    continue
        except Exception:
            return names
        return names

    def _restore_shape_selection(self, names):
        if not names:
            return
        wanted = set(names)
        try:
            for shape in self.layer.shapes():
                try:
                    if shape.name() in wanted:
                        shape.select()
                except Exception:
                    continue
        except Exception:
            pass

    def _clear_shape_rotation(self):
        try:
            shapes = self.layer.shapes()
        except Exception:
            return
        cleared = 0
        for shape in shapes:
            try:
                matrix = shape.transformation()
                angle = _matrix_rotation(matrix)
                if angle is None or abs(angle) < ROTATION_TOLERANCE:
                    continue
                m11 = matrix.m11()
                m12 = matrix.m12()
                m21 = matrix.m21()
                m22 = matrix.m22()
                det = m11 * m22 - m12 * m21
                if det <= 0.0 or not math.isfinite(det):
                    continue
                scale = math.sqrt(det)
                matrix.setMatrix(
                    scale,
                    0.0,
                    0.0,
                    scale,
                    matrix.dx(),
                    matrix.dy(),
                )
                shape.setTransformation(matrix)
                cleared += 1
            except Exception:
                continue
        if TRACE_TRANSFORM and cleared:
            diag.warn("transform redraw cleared_rotation_shapes=%d" % cleared)

    def redraw(self, doc, samples: int = LIVE_SAMPLES) -> dict:
        started = timing.now()
        if TRACE_TRANSFORM:
            diag.warn("transform redraw begin")
        svg = self.model.build_svg(
            width=doc.width(),
            height=doc.height(),
            dash_back="8,8",
            samples=samples,
        )
        built = timing.now()

        selected = self._selected_shape_names()
        shapes = self.layer.shapes()
        shapes_read = timing.now()
        remove_times: list[float] = []
        for shape in shapes:
            remove_started = timing.now()
            shape.remove()
            remove_times.append(timing.now() - remove_started)
        removed = timing.now()

        self.layer.addShapesFromSvg(svg)
        self._restore_shape_selection(selected)
        self._clear_shape_rotation()
        added = timing.now()
        if TRACE_TRANSFORM:
            diag.warn("transform redraw shapes_added")

        self.last_shape_states = self.shape_states()
        self._refresh_placement_baseline()
        self.needs_normalize = False
        self.stable_polls = 0
        states_read = timing.now()

        self._refresh_gate()
        finished = timing.now()

        return {
            "total": finished - started,
            "build_svg": built - started,
            "shapes_call": shapes_read - built,
            "remove": removed - shapes_read,
            "add": added - removed,
            "states": states_read - added,
            "bounds": finished - states_read,
            "shape_count": len(shapes),
            "samples": samples,
            "remove_each": remove_times,
        }

    def reveal(self, doc) -> None:
        try:
            self.layer.setVisible(True)
            doc.setActiveNode(self.layer)
        except Exception:
            pass
