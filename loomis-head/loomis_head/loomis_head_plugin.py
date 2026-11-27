import os

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Extension, Krita
from PyQt5 import uic
from PyQt5.QtCore import QStandardPaths, Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QColorDialog,
    QFrame,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import diag, timing
from .config import (
    LIVE_SAMPLES,
    RECONCILE_INTERVAL,
    REDRAW_DEBOUNCE,
    SYNC_INTERVAL,
    TRANSFORMED_REDRAW_DEBOUNCE,
)
from .document_registry import DocumentRegistry, node_key
from .head_instance import (
    NORMALIZE_SETTLE_POLLS,
    HeadInstance,
    has_transformed_placement,
    owning_layer,
    reset_transform_rotation,
)
from .linalg import q_identity
from .loomis_head_generator import LoomisHead3D
from .perf_settings import PerfSettingsStore
from .presets import (
    PresetStore,
    builtin_names,
    clean_name,
    load_button_label,
    preset_label,
)
from .trackball import TrackballWidget
from .ui_specs import EDITING_WIDGETS, SLIDERS, TOGGLES

PRESET_FOLDER = "loomis_method_head_guide"


class LoomisProportionsDocker(DockWidget):
    def __init__(self):
        super().__init__()

        self._fallback_model = LoomisHead3D()
        self.update_scheduled = False
        self._drawing = False
        self._last_sync = 0.0
        self._interacting = False

        self.registry = DocumentRegistry(
            on_changed=self._on_document_changed,
            before_change=self.flush_pending_draw,
        )

        self.draw_timer = QTimer(self)
        self.draw_timer.setSingleShot(True)
        self.draw_timer.setInterval(REDRAW_DEBOUNCE)
        self.draw_timer.timeout.connect(self._draw_pending)

        self.setWindowTitle("Loomis Method Head Guide")
        self.setMinimumSize(200, 160)

        app_data = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser(
            "~/.local/share/krita"
        )
        self.preset_store = PresetStore(os.path.join(app_data, PRESET_FOLDER))
        self.perf_store = PerfSettingsStore(os.path.join(app_data, PRESET_FOLDER))

        self.connect_ui()
        self._sync_preset_selection(rebuild=True)

        self.active_sync_timer = QTimer(self)
        self.active_sync_timer.setInterval(120)
        self.active_sync_timer.timeout.connect(self._poll_active_layer)
        self.active_sync_timer.start()

        self.reconcile_timer = QTimer(self)
        self.reconcile_timer.setInterval(RECONCILE_INTERVAL)
        self.reconcile_timer.timeout.connect(self.registry.reconcile)
        self.reconcile_timer.start()

        try:
            Krita.instance().notifier().applicationClosing.connect(
                self.registry.flush_local_state
            )
        except Exception:
            pass

        self.registry.attach(Krita.instance().activeDocument())


    @property
    def doc(self):
        return self.registry.doc

    @property
    def state(self):
        return self.registry.state

    def active_state(self):
        return self.registry.active_state()

    def active_instance(self):
        return self.registry.active_instance()

    @property
    def loomis_head(self) -> LoomisHead3D:
        instance = self.active_instance()
        return instance.model if instance is not None else self._fallback_model

    @property
    def loomis_layer(self):
        instance = self.active_instance()
        return instance.layer if instance is not None else None


    def _on_document_changed(self):
        self.refresh_instance_header()
        self.sync_ui_from_model()

    def showEvent(self, event):
        super().showEvent(event)
        self._last_sync = 0.0
        self.registry.attach(Krita.instance().activeDocument())
        self.active_sync_timer.start()
        self.reconcile_timer.start()

    def hideEvent(self, event):
        self.flush_pending_draw()
        self.registry.flush_local_state()
        self.active_sync_timer.stop()
        self.reconcile_timer.stop()
        super().hideEvent(event)

    def canvasChanged(self, canvas):
        if canvas is not None and canvas.view() is not None:
            doc = canvas.view().document()
        else:
            doc = Krita.instance().activeDocument()

        self._last_sync = 0.0
        self.registry.attach(doc)

    def _sync_active_reference(self):
        state = self.active_state()
        if state is None or self.doc is None:
            return
        now = timing.now()
        if now - self._last_sync < SYNC_INTERVAL / 1000.0:
            return
        self._last_sync = now
        try:
            node = owning_layer(self.doc.activeNode())
        except Exception:
            node = None
        key = node_key(node) if node is not None else None

        if key is not None and key in state.instances:
            if key != state.active_id:
                self._activate_instance(key)
            return

        if state.active_id is not None:
            self.registry.reconcile()

    def _add_head(self, state):
        self.flush_pending_draw()
        instance = HeadInstance.create(state.doc)
        state.instances[node_key(instance.layer)] = instance
        state.active_id = node_key(instance.layer)
        self.refresh_instance_header()
        self.sync_ui_from_model()
        instance.reveal(state.doc)
        self.registry.remember(instance)
        self.registry.flush_local_state()
        self.schedule_update()

    def add_head_clicked(self):
        state = self.active_state()
        if state is None:
            return
        self._add_head(state)

    def _activate_instance(self, key):
        state = self.active_state()
        if state is None or key not in state.instances or key == state.active_id:
            return
        previous = state.active()
        if previous is not None:
            self.registry.adopt(previous)
        self.registry.adopt(state.instances[key])
        state.active_id = key
        self.refresh_instance_header()
        self.sync_ui_from_model()

    def refresh_instance_header(self):
        label = self.ui.headLabel
        state = self.active_state()
        instance = state.active() if state is not None else None
        if instance is not None:
            label.setText(instance.name)
            label.setToolTip(instance.name)
        else:
            label.setText("No head")
            label.setToolTip("")

        has_instance = instance is not None
        self.ui.addHeadButton.setEnabled(state is not None)
        self._set_editing_enabled(has_instance)

    def _apply_limit_ranges(self):
        for spec in SLIDERS:
            getattr(self.ui, spec.widget).setRange(
                round(spec.limit.minimum / spec.factor),
                round(spec.limit.maximum / spec.factor),
            )

    def _set_editing_enabled(self, enabled):
        for name in EDITING_WIDGETS:
            getattr(self.ui, name).setEnabled(enabled)
        self._update_preset_buttons()

    def with_schedule_update(self, fn, preset_change=True):
        instance = self.active_instance()
        if instance is None:
            return
        fn()
        if preset_change:
            self._mark_preset_dirty(instance)
        self.schedule_update()

    def _mark_preset_dirty(self, instance=None):
        instance = instance or self.active_instance()
        if instance is None or not instance.preset_name or instance.preset_dirty:
            return
        instance.preset_dirty = True
        self._sync_preset_selection()
        self.registry.remember(instance)

    def _sync_color_swatch(self):
        self.ui.strokeColorSwatch.setStyleSheet(
            "background-color: %s; border: 1px solid #666;" % self.loomis_head.stroke_color
        )

    def pick_stroke_color(self):
        instance = self.active_instance()
        if instance is None:
            return
        old_hex = self.loomis_head.stroke_color
        old_qcolor = QColor(old_hex)
        old_dirty = instance.preset_dirty

        dlg = QColorDialog(self)
        dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        dlg.setCurrentColor(old_qcolor)

        def apply_color(col: QColor):
            if not col.isValid():
                return
            hex_rgb = col.name(QColor.HexRgb)
            self.loomis_head.set_stroke_color(hex_rgb)
            self._sync_color_swatch()
            self.schedule_update()

        def confirm_color(col: QColor):
            apply_color(col)
            self._mark_preset_dirty()

        dlg.currentColorChanged.connect(apply_color)
        dlg.colorSelected.connect(confirm_color)

        def revert():
            self.loomis_head.set_stroke_color(old_hex)
            self._sync_color_swatch()
            instance.preset_dirty = old_dirty
            self._sync_preset_selection()
            self.schedule_update()

        dlg.rejected.connect(revert)
        dlg.exec_()

    def connect_ui(self):
        ui_path = os.path.join(os.path.dirname(__file__), "ui", "LoomisDocker.ui")
        self.ui: QWidget = uic.loadUi(ui_path)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.ui)
        self.setWidget(scroll)

        self._apply_limit_ranges()

        host = self.ui.findChild(QWidget, "trackballHost")
        self.trackball = TrackballWidget(host)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.trackball)

        self.trackball.orientation_changed.connect(
            lambda v: self.with_schedule_update(
                lambda: self.loomis_head.set_quaternion(v), preset_change=False
            )
        )
        self.trackball.drag_started.connect(self._begin_interaction)
        self.trackball.drag_released.connect(self._end_interaction)

        for spec in SLIDERS:
            slider = getattr(self.ui, spec.widget)
            slider.valueChanged.connect(self._make_slider_handler(spec))
            slider.valueChanged.connect(self._update_value_labels)
            slider.sliderPressed.connect(self._begin_interaction)
            slider.sliderReleased.connect(self._end_interaction)

        for spec in TOGGLES:
            getattr(self.ui, spec.widget).toggled.connect(self._make_toggle_handler(spec))

        self.ui.strokeColorButton.clicked.connect(self.pick_stroke_color)
        self.ui.transformGuideButton.clicked.connect(self.show_transform_guide)
        self.trackball.reset_button.clicked.connect(self.reset_view)

        self.ui.addHeadButton.clicked.connect(self.add_head_clicked)

        self.ui.loadPresetButton.clicked.connect(self.load_preset)
        self.ui.savePresetButton.clicked.connect(self.save_preset)
        self.ui.deletePresetButton.clicked.connect(self.delete_preset)
        self.ui.presetCombo.currentIndexChanged.connect(self._update_preset_buttons)

        self.ui.updateIntervalSpin.valueChanged.connect(self._perf_changed)
        self.ui.liveUpdateCheck.toggled.connect(self._perf_changed)
        self.ui.reduceLiveCheck.toggled.connect(self._perf_changed)
        self.ui.liveSamplesSpin.valueChanged.connect(self._perf_changed)
        self._load_perf_settings()

        self._sync_color_swatch()
        self._update_value_labels()

    def _make_slider_handler(self, spec):
        def handle(value):
            self.with_schedule_update(
                lambda: getattr(self.loomis_head, spec.setter)(value * spec.factor)
            )

        return handle

    def _make_toggle_handler(self, spec):
        def handle(checked):
            self.with_schedule_update(
                lambda: getattr(self.loomis_head, spec.setter)(checked)
            )

        return handle

    def _update_value_labels(self, *_):
        for spec in SLIDERS:
            getattr(self.ui, spec.label).setText(
                str(getattr(self.ui, spec.widget).value())
            )


    def _load_perf_settings(self):
        values = self.perf_store.load()
        widgets = (
            self.ui.updateIntervalSpin,
            self.ui.liveUpdateCheck,
            self.ui.reduceLiveCheck,
            self.ui.liveSamplesSpin,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.ui.updateIntervalSpin.setValue(values["update_ms"])
        self.ui.liveUpdateCheck.setChecked(values["live_update"])
        self.ui.reduceLiveCheck.setChecked(values["reduce_live"])
        self.ui.liveSamplesSpin.setValue(values["live_samples"])
        for widget in widgets:
            widget.blockSignals(False)
        self._sync_perf_enabled()

    def _perf_changed(self, *_):
        self._sync_perf_enabled()
        try:
            self.perf_store.save(
                self.ui.updateIntervalSpin.value(),
                self.ui.liveUpdateCheck.isChecked(),
                self.ui.reduceLiveCheck.isChecked(),
                self.ui.liveSamplesSpin.value(),
            )
        except OSError as exc:
            diag.warn("could not save performance settings: %s" % exc)

    def _sync_perf_enabled(self):
        self.ui.updateIntervalSpin.setEnabled(self.ui.liveUpdateCheck.isChecked())
        self.ui.liveSamplesSpin.setEnabled(self.ui.reduceLiveCheck.isChecked())

    def _begin_interaction(self, *_):
        if not self._interacting:
            self.sync_manual_position()
        self._interacting = True

    def _end_interaction(self, *_):
        self._interacting = False
        self.draw_timer.stop()
        self.update_scheduled = False
        self.sync_manual_position()
        self.draw_lines_with_vectors(samples=LIVE_SAMPLES)

    def _redraw_samples(self):
        if self._interacting and self.ui.reduceLiveCheck.isChecked():
            return self.ui.liveSamplesSpin.value()
        return LIVE_SAMPLES


    def _sync_preset_selection(self, selected=None, rebuild=False):
        combo = self.ui.presetCombo
        instance = self.active_instance()
        if selected is not None:
            name = selected
        elif instance is not None and instance.preset_name:
            name = instance.preset_name
        elif rebuild:
            name = combo.currentData()
        else:
            name = None

        combo.blockSignals(True)
        if rebuild:
            combo.clear()
            for preset_name, _model, is_builtin in self.preset_store.all_presets():
                combo.addItem(preset_label(preset_name, is_builtin), preset_name)

        builtins = builtin_names()
        for index in range(combo.count()):
            item_name = combo.itemData(index)
            combo.setItemText(index, preset_label(item_name, item_name in builtins))

        index = combo.findData(name) if name else -1
        if index < 0:
            default_names = builtin_names()
            index = combo.findData(default_names[0]) if default_names else -1
        if index < 0 and combo.count():
            index = 0
        combo.setCurrentIndex(index)
        if index >= 0 and instance is not None and instance.preset_dirty:
            item_name = combo.itemData(index)
            combo.setItemText(index, preset_label(item_name, item_name in builtins, True))
        combo.blockSignals(False)
        self._update_preset_buttons()

    def _update_preset_buttons(self, *_args):
        instance = self.active_instance()
        has_instance = instance is not None
        name = self.ui.presetCombo.currentData()
        preset_name = instance.preset_name if instance is not None else None
        dirty = bool(preset_name and preset_name == name and instance.preset_dirty)
        button = self.ui.loadPresetButton
        button.setText(load_button_label(preset_name, name, dirty))
        button.setToolTip(
            "Reapply the selected preset, discarding unsaved edits. Position is not changed."
            if dirty
            else "Apply the selected preset. Position is not changed."
        )
        self.ui.presetCombo.setEnabled(has_instance)
        button.setEnabled(has_instance and bool(name))
        self.ui.savePresetButton.setEnabled(has_instance)
        self.ui.deletePresetButton.setEnabled(
            has_instance and bool(name) and name not in builtin_names()
        )

    def _preset_config(self, name):
        if not name:
            return None
        return self.preset_store.get(name)

    def load_preset(self):
        instance = self.active_instance()
        if instance is None:
            return
        name = self.ui.presetCombo.currentData()
        if not name:
            return
        config = self._preset_config(name)
        if config is None:
            return
        self.loomis_head.apply_preset_dict(config)
        instance.preset_name = name
        instance.preset_dirty = False
        self.sync_ui_from_model()
        self.schedule_update()

    def save_preset(self):
        instance = self.active_instance()
        if instance is None:
            return
        current = self.ui.presetCombo.currentData()
        suggestion = current if current and current not in builtin_names() else ""
        name, accepted = QInputDialog.getText(self, "Save preset", "Preset name:", text=suggestion)
        if not accepted:
            return
        try:
            cleaned = clean_name(name)
        except ValueError as exc:
            QMessageBox.information(self, "Save preset", str(exc))
            return
        if cleaned in self.preset_store.user_presets():
            answer = QMessageBox.question(
                self,
                "Save preset",
                "Overwrite preset “%s”?" % cleaned,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        try:
            saved = self.preset_store.save(cleaned, self.loomis_head.to_config_dict())
        except ValueError as exc:
            QMessageBox.information(self, "Save preset", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "Save preset", "Could not save preset:\n%s" % exc)
            return
        instance.preset_name = saved
        instance.preset_dirty = False
        self.registry.remember(instance)
        self._sync_preset_selection(selected=saved, rebuild=True)

    def delete_preset(self):
        if self.active_instance() is None:
            return
        name = self.ui.presetCombo.currentData()
        if not name:
            return
        if name in builtin_names():
            QMessageBox.information(self, "Delete preset", "Built-in presets cannot be deleted.")
            return
        answer = QMessageBox.question(
            self,
            "Delete preset",
            "Delete preset “%s”?" % name,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.preset_store.delete(name)
        except OSError as exc:
            QMessageBox.warning(self, "Delete preset", "Could not delete preset:\n%s" % exc)
            return
        state = self.active_state()
        if state is not None:
            for instance in state.instances.values():
                if instance.preset_name == name:
                    instance.preset_name = None
                    instance.preset_dirty = False
                    self.registry.remember(instance)
        self._sync_preset_selection(rebuild=True)

    def sync_ui_from_model(self):
        model = self.loomis_head
        widgets = [getattr(self.ui, spec.widget) for spec in SLIDERS]
        widgets += [getattr(self.ui, spec.widget) for spec in TOGGLES]
        for widget in widgets:
            widget.blockSignals(True)

        for spec in SLIDERS:
            getattr(self.ui, spec.widget).setValue(
                round(getattr(model, spec.attr) / spec.factor)
            )
        for spec in TOGGLES:
            getattr(self.ui, spec.widget).setChecked(getattr(model, spec.attr))

        for widget in widgets:
            widget.blockSignals(False)

        self._update_value_labels()
        self._sync_color_swatch()
        self.trackball.blockSignals(True)
        self.trackball.set_quaternion(model.q)
        self.trackball.blockSignals(False)
        self._sync_preset_selection()

    def _sync_trackball_rotation(self):
        self.trackball.blockSignals(True)
        self.trackball.set_quaternion(self.loomis_head.q)
        self.trackball.blockSignals(False)


    def flush_pending_draw(self, samples=LIVE_SAMPLES):
        if self.update_scheduled:
            self.draw_timer.stop()
            self.draw_lines_with_vectors(samples=samples)

    def schedule_update(self):
        if self.doc is not None and not self._drawing:
            self._sync_active_reference()
        instance = self.active_instance()
        if instance is None:
            self.update_scheduled = False
            return
        if self.doc is not None and not self._drawing and not self.update_scheduled:
            self.sync_manual_position()
        live_update = self.ui.liveUpdateCheck.isChecked()
        interval = self.ui.updateIntervalSpin.value() if live_update else 0
        if interval > 0 and has_transformed_placement(instance):
            interval = max(interval, TRANSFORMED_REDRAW_DEBOUNCE)
        self.registry.remember(instance)
        if interval == 0:
            if self._interacting:
                self.update_scheduled = True
                return
            self.update_scheduled = False
            self.draw_timer.stop()
            self.draw_lines_with_vectors(samples=LIVE_SAMPLES)
            return
        if self.draw_timer.interval() != interval:
            self.draw_timer.setInterval(interval)
        if self.update_scheduled:
            return

        self.update_scheduled = True
        self.draw_timer.start()

    def _draw_pending(self):
        if self.update_scheduled:
            self.draw_lines_with_vectors()

    def sync_manual_position(self):
        if self.doc is not None and not self._drawing:
            self._sync_active_reference()
        instance = self.active_instance()
        if not self.doc or instance is None or self.update_scheduled or self._drawing:
            return

        _moved, changed = self.registry.adopt(instance)
        if not self._interacting and instance.take_rotation_applied():
            self._sync_trackball_rotation()
        if not changed:
            if instance.needs_normalize:
                instance.stable_polls += 1
                if instance.stable_polls >= NORMALIZE_SETTLE_POLLS:
                    instance.needs_normalize = False
                    instance.stable_polls = 0
                    self.schedule_update()
            return

        instance.stable_polls = 0

    def _poll_active_layer(self):
        if self.doc is not None and not self._drawing:
            self.sync_manual_position()

    def draw_lines_with_vectors(self, samples: int | None = None):
        if samples is None:
            samples = self._redraw_samples()
        started = timing.now()
        self.update_scheduled = False
        sync_started = timing.now()
        if self.doc is not None and not self._drawing:
            self._sync_active_reference()
        instance = self.active_instance()
        sync_seconds = timing.now() - sync_started
        if not self.doc or instance is None:
            return

        draw_started = timing.now()
        self._drawing = True
        try:
            phases = instance.redraw(self.doc, samples=samples)
        finally:
            self._drawing = False
        draw_seconds = timing.now() - draw_started
        frame_seconds = timing.now() - started

        if timing.enabled():
            timing.emit(timing.format_redraw(phases))
            timing.emit(
                "frame total=%.2fms sync=%.2f redraw=%.2f other=%.2f samples=%d"
                % (
                    timing.milliseconds(frame_seconds),
                    timing.milliseconds(sync_seconds),
                    timing.milliseconds(draw_seconds),
                    timing.milliseconds(frame_seconds - sync_seconds - draw_seconds),
                    samples,
                )
            )


    def show_transform_guide(self):
        QMessageBox.information(
            self,
            "Loomis Head Guide",
            "Move or scale a head in either of two ways:\n"
            "- Drag or scale the layer's shapes with Select Shapes or the Transform tool.\n"
            "- Or add a Transform Mask (Layer > New > Transform Mask) for non-destructive placement.\n\n"
            "Copy: use Layer > Duplicate Layer or copy/paste. Masks are not copied.\n\n"
            "Credits: Based on the original Loomis Head Generator by lm0ss.\n"
            "Forked by Nedeljko Umjetnik, then forked by pine885.\n"
            "License: GPL-3.0.",
        )

    def reset_view(self):
        instance = self.active_instance()
        if instance is None:
            return
        self.trackball.reset(emit=False)
        self.loomis_head.set_quaternion(q_identity())
        reset_transform_rotation(instance)
        self.schedule_update()


class LoomisHeadPlugin(Extension):
    def __init__(self, parent):
        super().__init__(parent)

    def setup(self):
        Krita.instance().addDockWidgetFactory(
            DockWidgetFactory(
                "loomisMethodHeadGuide",
                DockWidgetFactoryBase.DockPosition.DockRight,
                LoomisProportionsDocker,
            )
        )

    def createActions(self, window):
        pass
