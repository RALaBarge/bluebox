import threading
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QComboBox,
    QLineEdit, QSystemTrayIcon, QMenu, QGroupBox,
    QFormLayout, QSpinBox, QCheckBox, QStatusBar, QTabWidget, QTextEdit,
)

from .config import AppConfig, ButtonMapping
from .device import list_devices, DeviceMonitor
from .audio_worker import AudioRecorder, list_audio_devices, default_input_device_index
from .stt import STTEngine
from .typer import insert_text, get_active_window_title, get_open_windows


class WorkerSignals(QObject):
    button_event = pyqtSignal(object)
    transcript = pyqtSignal(str)
    status = pyqtSignal(str)
    error = pyqtSignal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Finger Mouse STT")
        self.setMinimumSize(700, 500)

        self.config = AppConfig.load()
        self.signals = WorkerSignals()
        self.device_monitor: Optional[DeviceMonitor] = None
        self._sessions: dict = {}  # (device_id, button_code) -> dict(recorder, stt_engine, mapping, timer)

        self._status_queue = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_mappings)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_from_events)

        self._build_ui()
        self._connect_signals()
        self._load_config_into_ui()
        self._refresh_devices()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        mapping_tab = QWidget()
        tabs.addTab(mapping_tab, "Button Mapping")
        self._build_mapping_tab(mapping_tab)

        settings_tab = QWidget()
        tabs.addTab(settings_tab, "Settings")
        self._build_settings_tab(settings_tab)

        about_tab = QWidget()
        tabs.addTab(about_tab, "About")
        self._build_about_tab(about_tab)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.show)
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.instance().quit)

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.setToolTip("Finger Mouse STT")
        self.tray_icon.show()

    def _build_mapping_tab(self, parent):
        layout = QVBoxLayout(parent)

        device_group = QGroupBox("Device")
        dlayout = QVBoxLayout(device_group)
        dev_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(300)
        self.refresh_devices_btn = QPushButton("Refresh")
        dev_row.addWidget(QLabel("Input device:"))
        dev_row.addWidget(self.device_combo, 1)
        dev_row.addWidget(self.refresh_devices_btn)
        dlayout.addLayout(dev_row)
        layout.addWidget(device_group)

        mapping_group = QGroupBox("Button → Window Mappings")
        mlayout = QVBoxLayout(mapping_group)
        self.mapping_list = QListWidget()
        self.mapping_list.setAlternatingRowColors(True)
        mlayout.addWidget(self.mapping_list)

        btn_row = QHBoxLayout()
        self.add_mapping_btn = QPushButton("+ Add Mapping")
        self.remove_mapping_btn = QPushButton("− Remove")
        btn_row.addWidget(self.add_mapping_btn)
        btn_row.addWidget(self.remove_mapping_btn)
        btn_row.addStretch()
        mlayout.addLayout(btn_row)
        layout.addWidget(mapping_group, 1)

        status_group = QGroupBox("Status")
        slayout = QVBoxLayout(status_group)
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(100)
        slayout.addWidget(self.status_text)
        layout.addWidget(status_group)

    def _build_settings_tab(self, parent):
        layout = QVBoxLayout(parent)

        stt_group = QGroupBox("Speech-to-Text Engine")
        sform = QFormLayout(stt_group)
        self.stt_engine_combo = QComboBox()
        self.stt_engine_combo.addItems(["remote_server", "whisper_local", "vosk"])
        sform.addRow("Engine:", self.stt_engine_combo)
        self.stt_url_input = QLineEdit()
        self.stt_url_input.setPlaceholderText("http://192.168.1.100:5000")
        sform.addRow("Server URL:", self.stt_url_input)
        self.test_stt_btn = QPushButton("Test Connection")
        sform.addRow("", self.test_stt_btn)
        layout.addWidget(stt_group)

        audio_group = QGroupBox("Audio Input")
        aform = QFormLayout(audio_group)
        self.audio_device_combo = QComboBox()
        aform.addRow("Microphone:", self.audio_device_combo)
        self.refresh_audio_btn = QPushButton("Refresh")
        aform.addRow("", self.refresh_audio_btn)
        self.sample_rate_spin = QSpinBox()
        self.sample_rate_spin.setRange(8000, 48000)
        self.sample_rate_spin.setValue(16000)
        self.sample_rate_spin.setSingleStep(1000)
        aform.addRow("Sample rate:", self.sample_rate_spin)
        layout.addWidget(audio_group)

        typing_group = QGroupBox("Behavior")
        tform = QFormLayout(typing_group)
        self.hold_to_talk_check = QCheckBox("Hold button while speaking (push-to-talk)")
        self.hold_to_talk_check.setChecked(True)
        self.hold_to_talk_check.toggled.connect(lambda v: setattr(self.config, 'push_to_talk', v) or self.config.save())
        tform.addRow("", self.hold_to_talk_check)
        layout.addWidget(typing_group)

        layout.addStretch()

    def _build_about_tab(self, parent):
        layout = QVBoxLayout(parent)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml("""
        <h2>Finger Mouse STT</h2>
        <p>Map USB finger-mouse buttons to voice-to-text dictation
        into specific windows.</p>
        <p><b>How it works:</b></p>
        <ol>
        <li>Plug in your USB finger mouse</li>
        <li>Select the device from the list</li>
        <li>For each button, click <b>Map</b> then click the target window</li>
        <li>Press the button, speak, release — text appears in the window</li>
        </ol>
        """)
        layout.addWidget(text)

    def _connect_signals(self):
        self.refresh_devices_btn.clicked.connect(self._refresh_devices)
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        self.add_mapping_btn.clicked.connect(self._add_mapping)
        self.remove_mapping_btn.clicked.connect(self._remove_mapping)
        self.stt_engine_combo.currentTextChanged.connect(self._on_stt_engine_changed)
        self.stt_url_input.textChanged.connect(self._on_stt_url_changed)
        self.test_stt_btn.clicked.connect(self._test_stt)
        self.refresh_audio_btn.clicked.connect(self._refresh_audio_devices)

        self.signals.button_event.connect(self._on_button_event)
        self.signals.transcript.connect(self._on_transcript)
        self.signals.status.connect(self._queue_status)
        self.signals.error.connect(self._on_error)

    def _load_config_into_ui(self):
        self.stt_engine_combo.setCurrentText(self.config.default_stt_engine)
        self.stt_url_input.setText(self.config.default_stt_server_url)
        self.sample_rate_spin.setValue(self.config.audio_sample_rate)
        self.hold_to_talk_check.setChecked(getattr(self.config, 'push_to_talk', True))
        self._on_stt_engine_changed(self.config.default_stt_engine)
        self._refresh_audio_devices()

        if self.config.audio_device:
            idx = self.audio_device_combo.findText(self.config.audio_device)
            if idx >= 0:
                self.audio_device_combo.setCurrentIndex(idx)

        for m in self.config.mappings:
            self._add_mapping_widget(m)

    def _refresh_devices(self):
        current = self.device_combo.currentText()
        self.device_combo.clear()
        for d in list_devices():
            iface = {2: " (pointer)", 6: " (keys)", 1: " (consumer)"}.get(d.get("usage", 0), "")
            self.device_combo.addItem(f"{d['name']}{iface} ({d['path']})", d)
        for i in range(self.device_combo.count()):
            if self.device_combo.itemText(i) == current:
                self.device_combo.setCurrentIndex(i)
                break

    def _on_device_selected(self, index):
        if self.device_monitor:
            self.device_monitor.stop()
            self.device_monitor = None

        if index < 0:
            return

        d = self.device_combo.itemData(index)
        if not d:
            return

        self.config.selected_device = d["path"]
        self.config.save()
        self.device_monitor = DeviceMonitor(
            d["path"], self.signals.button_event.emit,
            usage_page=d.get("usage_page", 1), usage=d.get("usage", 2),
        )
        self.device_monitor.start()
        iface = {2: "pointer", 6: "keys", 1: "consumer"}.get(d.get("usage", 0), "?")
        self._queue_status(f"Listening on {d['name']} ({iface})")

    def _on_button_event(self, event):
        if isinstance(event, tuple) and len(event) == 2:
            self._handle_button(*event)
        elif isinstance(event, tuple) and event[0] == "error":
            self._queue_status(f"Device error: {event[1]}")

    def _add_mapping(self):
        self._add_mapping_widget()

    def _add_mapping_widget(self, mapping=None):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(4, 2, 4, 2)

        btn_code = mapping["button_code"] if mapping else 0
        btn_label = mapping.get("button_label", f"Button {btn_code}") if mapping else "Button 0"
        target = mapping.get("target_window_title", "") if mapping else ""

        btn_combo = QComboBox()
        btn_combo.setMinimumWidth(120)
        for bcode, blabel in [(0, "Button 0"), (1, "Left"), (2, "Right"),
                              (3, "Middle"), (4, "Side"), (5, "Extra")]:
            btn_combo.addItem(blabel, bcode)
        if mapping:
            idx = btn_combo.findData(btn_code)
            if idx >= 0:
                btn_combo.setCurrentIndex(idx)

        engine_combo = QComboBox()
        engine_combo.addItems(["remote_server", "whisper_local", "vosk"])
        if mapping:
            engine_combo.setCurrentText(mapping.get("stt_engine", self.config.default_stt_engine))
        else:
            engine_combo.setCurrentText(self.config.default_stt_engine)

        server_input = QLineEdit()
        server_input.setPlaceholderText("STT server URL")
        server_input.setText(mapping.get("stt_server_url", "") if mapping else "")
        server_input.setFixedWidth(200)

        target_input = QLineEdit()
        target_input.setPlaceholderText("Window title or click Map")
        target_input.setText(target)
        target_input.setFixedWidth(200)

        map_btn = QPushButton("Map ⟶")
        map_btn.setToolTip("Click, then click the target window")

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(30)

        row.addWidget(QLabel("Button:"))
        row.addWidget(btn_combo)
        row.addWidget(QLabel("Engine:"))
        row.addWidget(engine_combo)
        row.addWidget(server_input)
        row.addWidget(target_input)
        row.addWidget(map_btn)
        row.addWidget(remove_btn)

        list_item = QListWidgetItem(self.mapping_list)
        list_item.setSizeHint(widget.sizeHint())
        self.mapping_list.addItem(list_item)
        self.mapping_list.setItemWidget(list_item, widget)

        map_btn.clicked.connect(lambda: self._start_window_map(target_input, map_btn))
        remove_btn.clicked.connect(lambda: self._remove_mapping_item(list_item))

        for w in (btn_combo, engine_combo, server_input, target_input):
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._defer_save)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(self._defer_save)

    def _defer_save(self):
        self._save_timer.start(300)

    def _start_window_map(self, target_input, btn):
        if not get_open_windows():
            self._queue_status("No tool available for window detection")
            return

        btn.setEnabled(False)
        btn.setText("Click target window...")
        self._queue_status("Hide app, click the target window...")

        self.hide()
        QTimer.singleShot(100, lambda: self._poll_active_window(target_input, btn))

    def _poll_active_window(self, target_input, btn):
        last_title = ""
        stable_count = 0

        def poll():
            nonlocal last_title, stable_count
            title = get_active_window_title()
            if title:
                if title == last_title:
                    stable_count += 1
                else:
                    last_title = title
                    stable_count = 0

                if stable_count >= 3:
                    target_input.setText(title)
                    btn.setEnabled(True)
                    btn.setText("Map ⟶")
                    self.show()
                    self.raise_()
                    self.activateWindow()
                    self._defer_save()
                    self._queue_status(f"Mapped to: {title}")
                    return

            if btn.isEnabled():
                return

            QTimer.singleShot(500, poll)

        poll()

    def _remove_mapping(self):
        for item in self.mapping_list.selectedItems():
            self._remove_mapping_item(item)

    def _remove_mapping_item(self, item):
        self.mapping_list.takeItem(self.mapping_list.row(item))
        self._save_mappings()

    def _save_mappings(self):
        self.config.mappings = []
        for i in range(self.mapping_list.count()):
            item = self.mapping_list.item(i)
            widget = self.mapping_list.itemWidget(item)
            if not widget:
                continue
            combos = widget.findChildren(QComboBox)
            lines = widget.findChildren(QLineEdit)
            if len(combos) >= 1 and len(lines) >= 2:
                self.config.mappings.append({
                    "device_id": self.config.selected_device,
                    "button_code": combos[0].currentData(),
                    "button_label": combos[0].currentText(),
                    "target_window_title": lines[1].text(),
                    "stt_engine": combos[1].currentText() if len(combos) > 1 else self.config.default_stt_engine,
                    "stt_server_url": lines[0].text(),
                    "push_to_talk": self.hold_to_talk_check.isChecked(),
                })
        self.config.save()

    def _handle_button(self, code, is_down):
        if not self.config.selected_device:
            return
        mapping = self.config.get_mapping(self.config.selected_device, code)
        if not mapping:
            return
        if is_down and mapping.push_to_talk:
            self._start_dictation(mapping)
        elif not is_down and mapping.push_to_talk:
            self._stop_dictation(mapping)

    def _session_key(self, mapping: ButtonMapping):
        return (mapping.device_id, mapping.button_code)

    def _start_dictation(self, mapping: ButtonMapping):
        key = self._session_key(mapping)
        if key in self._sessions:
            return  # already recording for this button

        engine = mapping.stt_engine or self.config.default_stt_engine
        url = mapping.stt_server_url or self.config.default_stt_server_url
        stt_engine = STTEngine(engine, url)

        dev = self.audio_device_combo.currentData()
        recorder = AudioRecorder(
            on_audio=lambda x: None,
            sample_rate=self.sample_rate_spin.value(),
            device=dev,
        )
        recorder.start()
        recorder.start_recording()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_recording_timeout(key))
        timer.start(30000)

        self._sessions[key] = {
            "recorder": recorder,
            "stt_engine": stt_engine,
            "mapping": mapping,
            "timer": timer,
        }
        self._queue_status(f"Recording... ({mapping.button_label})")
        self.status_text.setStyleSheet("background-color: #d4edda;")

    def _stop_dictation(self, mapping: ButtonMapping):
        key = self._session_key(mapping)
        session = self._sessions.pop(key, None)
        if not session:
            return

        session["timer"].stop()
        recorder = session["recorder"]
        recorder.stop_recording()
        self._queue_status("Transcribing...")
        self.status_text.setStyleSheet("background-color: #fff3cd;")

        audio_data = recorder.get_recorded_audio()
        recorder.stop()
        if audio_data is None or len(audio_data) < 1000:
            self._queue_status("Audio too short")
            self.status_text.setStyleSheet("")
            return

        stt_engine = session["stt_engine"]

        def transcribe_and_type():
            text = stt_engine.transcribe(audio_data, self.sample_rate_spin.value())
            if text:
                self.signals.transcript.emit(text)
                ok = insert_text(text, mapping.target_window_title)
                if ok:
                    self.signals.status.emit(f"Typed into window")
                else:
                    self.signals.status.emit("Typed (window may be wrong)")
            else:
                self.signals.status.emit("No transcription result")
            self.status_text.setStyleSheet("")

        threading.Thread(target=transcribe_and_type, daemon=True).start()

    def _on_recording_timeout(self, key):
        session = self._sessions.pop(key, None)
        if session:
            session["recorder"].stop_recording()
            session["recorder"].stop()
        self._queue_status("Recording timed out (30s)")
        self.status_text.setStyleSheet("")

    def _on_transcript(self, text: str):
        self.status_text.append(f"<b>Text:</b> {text[:200]}")

    def _queue_status(self, msg: str):
        self._status_queue.append(msg)
        if not self._status_timer.isActive():
            self._status_timer.start(50)

    def _update_status_from_events(self):
        self._status_timer.stop()
        while self._status_queue:
            msg = self._status_queue.pop(0)
            self.status_bar.showMessage(msg, 5000)
            self.status_text.append(msg)

    def _on_error(self, msg: str):
        self.status_text.append(f"<span style='color:red'>Error: {msg}</span>")
        self.status_bar.showMessage(f"Error: {msg}", 8000)

    def _on_stt_engine_changed(self, engine: str):
        self.stt_url_input.setEnabled(engine == "remote_server")
        self.config.default_stt_engine = engine
        self.config.save()

    def _on_stt_url_changed(self, url: str):
        self.config.default_stt_server_url = url
        self.config.save()

    def _test_stt(self):
        engine = STTEngine(self.stt_engine_combo.currentText(), self.stt_url_input.text())
        if engine.is_available():
            self._queue_status("STT engine reachable")
        else:
            self._queue_status("STT not reachable — check URL")

    def _refresh_audio_devices(self):
        current = self.audio_device_combo.currentText()
        self.audio_device_combo.clear()
        devices = list_audio_devices()
        default_idx = default_input_device_index()
        default_row = -1
        for row, d in enumerate(devices):
            self.audio_device_combo.addItem(d["name"], d["index"])
            if d["index"] == default_idx:
                default_row = row
        idx = self.audio_device_combo.findText(current)
        if idx >= 0:
            self.audio_device_combo.setCurrentIndex(idx)
        elif default_row >= 0:
            self.audio_device_combo.setCurrentIndex(default_row)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        event.ignore()


def run():
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName("Finger Mouse STT")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())