from PyQt5.QtCore import QByteArray, QTimer

from . import diag, local_state
from .config import LOCAL_SAVE_DEBOUNCE
from .head_instance import (
    HeadInstance,
    head_base_name,
    iter_vector_layers,
    next_name,
    owning_layer,
    strip_copy_name,
)
from .loomis_head_generator import LoomisHead3D
from .presets import canonical_name


def doc_key(doc) -> str:
    try:
        return str(doc.rootNode().uniqueId())
    except Exception:
        return str(id(doc))


def node_key(node) -> str:
    try:
        return str(node.uniqueId())
    except Exception:
        return str(id(node))


class DocumentState:
    def __init__(self, doc):
        self.doc = doc
        self.instances: dict[str, HeadInstance] = {}
        self.active_id: str | None = None
        self.local = local_state.empty_state()

    def active(self) -> HeadInstance | None:
        if self.active_id is None:
            return None
        return self.instances.get(self.active_id)


class DocumentRegistry:

    def __init__(self, on_changed, before_change=None):
        self.states: dict[str, DocumentState] = {}
        self.doc = None
        self.state: DocumentState | None = None
        self._on_changed = on_changed
        self._before_change = before_change or (lambda: None)
        self._local_dirty = False

        self.save_timer = QTimer()
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(LOCAL_SAVE_DEBOUNCE)
        self.save_timer.timeout.connect(self.write_local_state)

    def active_state(self) -> DocumentState | None:
        return self.state

    def active_instance(self) -> HeadInstance | None:
        state = self.state
        return state.active() if state is not None else None

    def attach(self, doc) -> None:
        if doc is None:
            if self.doc is not None:
                self._before_change()
                self.flush_local_state()
            self.doc = None
            self.state = None
            self._on_changed()
            return

        key = doc_key(doc)
        old_key = doc_key(self.doc) if self.doc is not None else None
        doc_changed = key != old_key
        if doc_changed:
            self._before_change()
            self.flush_local_state()

        state = self.states.get(key)
        if state is None:
            state = DocumentState(doc)
            state.local = self.read_local_state(doc)
            self.states[key] = state
        elif doc_changed:
            state.local = self.read_local_state(doc)
            state.instances.clear()
            state.active_id = None
        elif not state.local.get("instances"):
            state.local = self.read_local_state(doc)
        state.doc = doc
        self.doc = doc
        self.state = state

        self.reconcile()
        self._on_changed()

    def read_local_state(self, doc):
        try:
            payload = doc.annotation(local_state.ANNOTATION_TYPE)
        except Exception as exc:
            diag.warn("could not read local state: %s" % exc)
            return local_state.empty_state()
        if not payload:
            return local_state.empty_state()
        return local_state.loads(payload)

    def write_local_state(self) -> None:
        self.save_timer.stop()
        state = self.active_state()
        if not self._local_dirty or state is None or self.doc is None:
            return
        payload = local_state.dumps(state.local).encode("utf-8")
        try:
            self.doc.setAnnotation(
                local_state.ANNOTATION_TYPE,
                local_state.ANNOTATION_DESCRIPTION,
                QByteArray(payload),
            )
        except Exception as exc:
            diag.warn("could not write local state: %s" % exc)
            return
        self._local_dirty = False

    def flush_local_state(self) -> None:
        self.save_timer.stop()
        if self._local_dirty:
            self.write_local_state()

    def schedule_local_save(self) -> None:
        self._local_dirty = True
        self.save_timer.start()

    def remember(self, instance=None) -> bool:
        state = self.active_state()
        if instance is None and state is not None:
            instance = state.active()
        if state is None or instance is None:
            return False
        placement = instance.model.to_local_dict()
        changed = local_state.remember(
            state.local,
            node_key(instance.layer),
            instance.name,
            placement["position"],
            placement["quaternion"],
            config=instance.model.to_config_dict(),
            preset=instance.preset_name,
            preset_dirty=instance.preset_dirty,
            scale=placement["scale"],
        )
        if changed:
            self.schedule_local_save()
        return changed

    def adopt(self, instance):
        moved, changed = instance.adopt_placement()
        if moved:
            self.remember(instance)
        return moved, changed

    @staticmethod
    def _head_source_instance(state, base_name):
        if not base_name:
            return None
        for instance in state.instances.values():
            try:
                if instance.layer.name() == base_name:
                    return instance
            except Exception:
                continue
        for instance in state.instances.values():
            if instance.name == base_name:
                return instance
        return None

    @staticmethod
    def _known_local_head(state, key):
        if not isinstance(state.local, dict):
            return False
        instances = state.local.get("instances")
        return isinstance(instances, dict) and key in instances

    def reconcile(self) -> None:
        state = self.active_state()
        if state is None or self.doc is None:
            return

        current: dict[str, object] = {}
        for node in iter_vector_layers(self.doc):
            current[node_key(node)] = node

        changed = False
        for key in list(state.instances):
            if key not in current:
                state.instances.pop(key, None)
                if state.active_id == key:
                    state.active_id = None
                if local_state.discard(state.local, key):
                    self.schedule_local_save()
                changed = True

        for key, instance in list(state.instances.items()):
            node = current.get(key)
            if node is None:
                continue
            instance.layer = node
            try:
                name = node.name()
            except Exception:
                name = instance.name
            if name != instance.name:
                instance.name = name
                if local_state.rename(state.local, key, name):
                    self.schedule_local_save()
                changed = True

        used_names = set()
        for existing in state.instances.values():
            used_names.add(existing.name)
            try:
                used_names.add(existing.layer.name())
            except Exception:
                pass

        pending = []
        for key, layer in current.items():
            if key in state.instances:
                continue
            try:
                name = layer.name()
            except Exception:
                continue
            base = head_base_name(name)
            if base is None:
                source_name = strip_copy_name(name)
                if not self._head_source_instance(state, source_name or name):
                    if not self._known_local_head(state, key):
                        continue
            pending.append((key, layer, name))

        pending.sort(key=lambda item: strip_copy_name(item[2]) is not None)

        for key, layer, name in pending:
            base = head_base_name(name)
            source_name = strip_copy_name(name)
            source = None
            is_copy = False
            if base is not None:
                if name != base or name in used_names:
                    is_copy = True
                    source = self._head_source_instance(state, base)
            else:
                source = self._head_source_instance(state, source_name or name)
                if source is not None:
                    is_copy = True
                elif not self._known_local_head(state, key):
                    continue

            target_name = name
            if is_copy:
                if source is None:
                    source = self._head_source_instance(state, source_name or name)
                target_name = next_name(self.doc, used_names)
                try:
                    layer.setName(target_name)
                except Exception:
                    pass
            used_names.add(target_name)

            model = None
            if source is not None:
                model = LoomisHead3D()
                model.apply_preset_dict(source.model.to_preset_dict())
                model.apply_local_dict(source.model.to_local_dict())
            instance = HeadInstance.attach(self.doc, target_name, layer, model)
            instance.name = target_name
            entry = local_state.lookup(state.local, key, target_name)
            if entry is not None:
                instance.model.apply_local_dict(entry)
                config = entry.get("config")
                if isinstance(config, dict):
                    instance.model.apply_config_dict(config)
                    if "scale" not in entry and "scale" in config:
                        instance.model.apply_local_dict({"scale": config["scale"]})
                preset = entry.get("preset")
                if isinstance(preset, str) and preset:
                    instance.preset_name = canonical_name(preset)
                instance.preset_dirty = entry.get("preset_dirty") is True
            elif model is None:
                center = instance.bounds_center()
                if center is not None:
                    instance.model.set_position_x(center[0] * instance.point_scale_x)
                    instance.model.set_position_y(center[1] * instance.point_scale_y)
            instance._refresh_placement_baseline()
            state.instances[key] = instance
            changed = True

        active_id = None
        try:
            active_node = owning_layer(self.doc.activeNode())
        except Exception:
            active_node = None
        if active_node is not None:
            key = node_key(active_node)
            if key in state.instances:
                active_id = key

        if active_id != state.active_id:
            previous = state.instances.get(state.active_id) if state.active_id is not None else None
            if previous is not None:
                self.adopt(previous)
            instance = state.instances.get(active_id) if active_id is not None else None
            if instance is not None:
                self.adopt(instance)
            state.active_id = active_id
            changed = True

        if changed:
            self._on_changed()
