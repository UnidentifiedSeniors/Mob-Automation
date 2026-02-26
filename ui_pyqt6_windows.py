# ui_pyqt6.py
from pathlib import Path
import sys
from PyQt6.QtGui import QPixmap, QPalette, QBrush

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QToolButton,
    QDockWidget,
    QCheckBox,
    QFrame,
    QSpacerItem,
    QSizePolicy,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QUrl
from PyQt6.QtGui import QFont, QPainter, QColor, QRadialGradient, QPainterPath, QLinearGradient
from PyQt6.QtCore import pyqtSignal

from GameBot_windows import BotSettings, GameBot

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    HAS_QT_MULTIMEDIA = True
except Exception:
    HAS_QT_MULTIMEDIA = False


class HelpPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("How to use Mob Automation")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        steps = [
            "1. Place the game window in front of other apps (so clicks/keys target it).",
            "2. Set Monster target (currently: Ashgor).",
            "3. Click Start to begin automation (the program will control mouse/keyboard).",
            "4. Use Stop to immediately halt automation.",
            "5. Monitor the Status and the live log for progress and errors.",
        ]
        for s in steps:
            lbl = QLabel(s)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        layout.addStretch()
        self.setLayout(layout)


class SettingsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        title = QLabel("Settings")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        # Core automation behavior
        behavior_group = QGroupBox("Automation")
        behavior_form = QFormLayout()
        behavior_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        behavior_form.setContentsMargins(8, 8, 8, 8)
        behavior_form.setSpacing(8)
        self.checkbox_focus = QCheckBox("Auto-focus target window before actions")
        self.checkbox_safe = QCheckBox("Enable safety checks (recommended)")
        self.checkbox_safe.setChecked(True)  # default ON as requested
        behavior_form.addRow(self.checkbox_focus)
        behavior_form.addRow(self.checkbox_safe)
        behavior_group.setLayout(behavior_form)
        layout.addWidget(behavior_group)

        # Run limits
        limits_group = QGroupBox("Run Limits")
        limits_form = QFormLayout()
        limits_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        limits_form.setContentsMargins(8, 8, 8, 8)
        limits_form.setSpacing(8)

        self.checkbox_limit_minutes = QCheckBox("Limit by runtime (minutes)")
        self.spin_limit_minutes = QDoubleSpinBox()
        self.spin_limit_minutes.setDecimals(1)
        self.spin_limit_minutes.setRange(0.5, 9999.0)
        self.spin_limit_minutes.setSingleStep(0.5)
        self.spin_limit_minutes.setValue(10.0)
        self.spin_limit_minutes.setEnabled(False)
        limits_form.addRow(self.checkbox_limit_minutes, self.spin_limit_minutes)

        self.checkbox_limit_loops = QCheckBox("Limit by loop count")
        self.spin_limit_loops = QSpinBox()
        self.spin_limit_loops.setRange(1, 1000000)
        self.spin_limit_loops.setValue(10)
        self.spin_limit_loops.setEnabled(False)
        limits_form.addRow(self.checkbox_limit_loops, self.spin_limit_loops)

        limits_group.setLayout(limits_form)
        layout.addWidget(limits_group)

        self.checkbox_limit_minutes.stateChanged.connect(self._sync_limit_controls)
        self.checkbox_limit_loops.stateChanged.connect(self._sync_limit_controls)
        self._sync_limit_controls()

        layout.addStretch()
        self.setLayout(layout)

    def _sync_limit_controls(self):
        minutes_on = self.checkbox_limit_minutes.isChecked()
        loops_on = self.checkbox_limit_loops.isChecked()

        # Mutual exclusion: enabling one auto-disables the other.
        if minutes_on and loops_on:
            sender = self.sender()
            if sender is self.checkbox_limit_minutes:
                self.checkbox_limit_loops.setChecked(False)
                loops_on = False
            else:
                self.checkbox_limit_minutes.setChecked(False)
                minutes_on = False

        self.spin_limit_minutes.setEnabled(minutes_on)
        self.spin_limit_loops.setEnabled(loops_on)
        self.checkbox_limit_minutes.setEnabled(not loops_on or minutes_on)
        self.checkbox_limit_loops.setEnabled(not minutes_on or loops_on)


class FogBubble(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoBubble")
        self._phase = 0.0

        self._fog_timer = QTimer(self)
        self._fog_timer.timeout.connect(self._advance_fog)
        self._fog_timer.start(45)

    def _advance_fog(self):
        self._phase = (self._phase + 0.0075) % 1.0
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 20, 20)
        painter.setClipPath(path)

        w = float(self.width())
        h = float(self.height())
        clouds = [
            (0.20 + 0.70 * self._phase, 0.25, 0.42 * w, 42),
            (0.90 - 0.80 * self._phase, 0.62, 0.36 * w, 34),
            (0.35 + 0.55 * ((self._phase + 0.35) % 1.0), 0.80, 0.30 * w, 26),
        ]

        for cx_ratio, cy_ratio, radius, alpha in clouds:
            cx = w * cx_ratio
            cy = h * cy_ratio
            grad = QRadialGradient(cx, cy, radius)
            grad.setColorAt(0.0, QColor(255, 255, 255, alpha))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(grad)
            painter.drawEllipse(int(cx - radius), int(cy - radius * 0.6), int(radius * 2), int(radius * 1.2))


class FogTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._fog_timer = QTimer(self)
        self._fog_timer.timeout.connect(self._advance_fog)
        self._fog_timer.start(100)

    def _advance_fog(self):
        self._phase = (self._phase + 0.0025) % 1.0
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.viewport().rect().adjusted(2, 2, -2, -2)), 8, 8)
        painter.setClipPath(path)

        w = float(self.viewport().width())
        h = float(self.viewport().height())
        fog_radius = max(0.60 * w, 0.95 * h)
        clouds = [
            (0.08 + 0.50 * self._phase, 0.22, fog_radius, 18),
            (0.90 - 0.45 * self._phase, 0.52, fog_radius * 1.02, 15),
            (0.22 + 0.40 * ((self._phase + 0.4) % 1.0), 0.80, fog_radius * 0.96, 12),
        ]

        for cx_ratio, cy_ratio, radius, alpha in clouds:
            cx = w * cx_ratio
            cy = h * cy_ratio
            grad = QRadialGradient(cx, cy, radius)
            grad.setColorAt(0.0, QColor(255, 255, 255, alpha))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(grad)
            painter.drawEllipse(int(cx - radius), int(cy - radius * 0.6), int(radius * 2), int(radius * 1.2))


class FogButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._phase = 0.0
        self._gleam_progress = -1.0

        self._fog_timer = QTimer(self)
        self._fog_timer.timeout.connect(self._advance_fog)
        self._fog_timer.start(65)

        self._gleam_timer = QTimer(self)
        self._gleam_timer.timeout.connect(self._advance_gleam)

    def _advance_fog(self):
        self._phase = (self._phase + 0.005) % 1.0
        self.update()

    def trigger_gleam(self):
        if not self.isEnabled() or self._gleam_timer.isActive():
            return
        self._gleam_progress = -0.35
        self._gleam_timer.start(16)

    def _advance_gleam(self):
        self._gleam_progress += 0.06
        if self._gleam_progress > 1.35:
            self._gleam_timer.stop()
            self._gleam_progress = -1.0
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 10, 10)
        painter.setClipPath(path)

        w = float(self.width())
        h = float(self.height())
        clouds = [
            (0.10 + 0.55 * self._phase, 0.35, 0.50 * w, 15),
            (0.85 - 0.45 * self._phase, 0.75, 0.42 * w, 11),
        ]

        for cx_ratio, cy_ratio, radius, alpha in clouds:
            cx = w * cx_ratio
            cy = h * cy_ratio
            grad = QRadialGradient(cx, cy, radius)
            grad.setColorAt(0.0, QColor(255, 255, 255, alpha))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(grad)
            painter.drawEllipse(int(cx - radius), int(cy - radius * 0.6), int(radius * 2), int(radius * 1.2))

        if self._gleam_progress >= 0.0:
            gleam_x = self._gleam_progress * w
            gleam_grad = QLinearGradient(gleam_x - 70, 0, gleam_x + 70, h)
            gleam_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
            gleam_grad.setColorAt(0.5, QColor(255, 255, 255, 95))
            gleam_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(gleam_grad)
            painter.drawRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 10, 10)


class IntroVideoSplash(QWidget):
    def __init__(self, video_path: Path, on_finished, parent=None):
        super().__init__(parent)
        self._on_finished = on_finished
        self._done = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet("background-color: black;")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.video_widget = QVideoWidget(self)
        layout.addWidget(self.video_widget)
        self.setLayout(layout)

        screen_geo = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(screen_geo)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.85)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)

        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_error)
        self.player.setSource(QUrl.fromLocalFile(str(video_path)))

    def start(self):
        self.show()
        self.player.play()
        QTimer.singleShot(15000, self.finish)

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.finish()

    def _on_error(self, _error, _error_string):
        self.finish()

    def finish(self):
        if self._done:
            return
        self._done = True
        self.player.stop()
        self.close()
        if callable(self._on_finished):
            self._on_finished()


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str, str)
    bot_finished_signal = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()

        self.WIDTH = 1000
        self.HEIGHT = 730
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.center_window()
        self.setWindowTitle("Mob Automation")
        self.setStyleSheet(self._dark_stylesheet())
        self.bot = None

        central = QWidget()
        central.setObjectName("centralWidget")
        central_layout = QVBoxLayout()
        central_layout.setContentsMargins(20, 20, 20, 20)
        central_layout.setSpacing(12)
        central.setLayout(central_layout)
        central.setStyleSheet("#centralWidget { background: transparent; }")
        
        self.setCentralWidget(central)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(0, 0, 0, 0)

        self.help_btn = QToolButton()
        self.help_btn.setText("?")
        self.help_btn.setFont(QFont("PT Mono", 20))
        self.help_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.help_btn.setFixedSize(38, 38)
        self.help_btn.clicked.connect(self.toggle_help_panel)
        topbar.addWidget(self.help_btn)

        topbar.addItem(QSpacerItem(20, 1, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.gear_btn = QToolButton()
        self.gear_btn.setText("⚙")
        self.gear_btn.setFont(QFont("Arial", 29))
        self.gear_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.gear_btn.setFixedSize(38, 38)
        self.gear_btn.clicked.connect(self.toggle_settings_panel)
        topbar.addWidget(self.gear_btn)

        central_layout.addLayout(topbar)

        title_label = QLabel("MOB AUTOMATION")
        title_label.setFont(QFont("ARIAL BLACK", 40))
        title_label.setStyleSheet("color: #ffffff;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        central_layout.addWidget(title_label)

        info_bubble = FogBubble()
        bubble_layout = QVBoxLayout()
        bubble_layout.setContentsMargins(18, 14, 18, 14)
        bubble_layout.setSpacing(8)
        info_bubble.setLayout(bubble_layout)

        monster_row = QHBoxLayout()
        monster_label = QLabel("Monster target:")
        monster_label.setFont(QFont("Sinhala Sangam MN", 18))
        monster_label.setStyleSheet("color: #d0d0d0;")
        self.monster_name = QLabel("Ashgor")
        self.monster_name.setFont(QFont("Sinhala Sangam MN", 18, QFont.Weight.Bold))
        self.monster_name.setStyleSheet("color: #FF5D5D; margin-left: 8px;")

        monster_row.addStretch()
        monster_row.addWidget(monster_label)
        monster_row.addWidget(self.monster_name)
        monster_row.addStretch()
        bubble_layout.addLayout(monster_row)

        status_row = QHBoxLayout()
        status_text = QLabel("Status:")
        status_text.setFont(QFont("Sinhala Sangam MN", 18))
        status_text.setStyleSheet("color: #d0d0d0;")
        self.status_value = QLabel("Idle")
        self.status_value.setFont(QFont("Sinhala Sangam MN", 18, QFont.Weight.Bold))
        self.status_value.setStyleSheet("color: #FFAA00; margin-left: 8px;")

        status_row.addStretch()
        status_row.addWidget(status_text)
        status_row.addWidget(self.status_value)
        status_row.addStretch()
        bubble_layout.addLayout(status_row)
        central_layout.addWidget(info_bubble)

        self.log = FogTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "background: #121212; color: #e6e6e6; border: 2px solid #D084FF; padding:8px;"
        )
        self.log.setFont(QFont("PT Mono", 14))
        self.log.setFixedHeight(360)
        central_layout.addWidget(self.log)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(50, 0, 50, 0)
        buttons_row.setSpacing(20)

        self.start_btn = FogButton("Start")
        self.start_btn.setFont(QFont("Arial Black", 16))
        self.start_btn.setFixedHeight(48)
        self.start_btn.clicked.connect(self.on_start)

        self.stop_btn = FogButton("Stop")
        self.stop_btn.setFont(QFont("Arial Black", 16))
        self.stop_btn.setFixedHeight(48)
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)

        self.start_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.stop_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        buttons_row.addWidget(self.start_btn)
        buttons_row.addWidget(self.stop_btn)
        central_layout.addLayout(buttons_row)

        self.help_dock = QDockWidget("Help", self)
        self.help_dock.setWidget(HelpPanel())
        self.help_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.help_dock)
        self.help_dock.hide()

        self.settings_dock = QDockWidget("Settings", self)
        self.settings_panel = SettingsPanel()
        self.settings_dock.setWidget(self.settings_panel)
        self.settings_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.settings_dock)
        self.settings_dock.hide()

        self._idle_gleam_timer = QTimer(self)
        self._idle_gleam_timer.timeout.connect(self._gleam_start_if_idle)
        self._idle_gleam_timer.start(3000)

        self.log_signal.connect(self.append_log)
        self.status_signal.connect(self.set_status)
        self.bot_finished_signal.connect(self._on_bot_finished)

        self.append_log("UI initialized. Ready.")

    def _dark_stylesheet(self):
        return """
        QMainWindow {
            background: qlineargradient(
                x1:0, y1:0,
                x2:0, y2:1,
                stop:0 #811dbf,
                stop:0.2 #040207
            );
        }

        QLabel {
            color: #e6e6e6;
        }

        QPushButton {
            background-color: #8721c6;
            border: 1px solid #ffffff;
            border-radius: 10px;
            padding: 8px;
            color: white;
        }

        QPushButton:hover {
            background-color: #D084FF;
        }

        QPushButton:pressed {
            background-color: #3a3a3a;
        }

        QToolButton {
            background-color: #a31bf7;
            border: 1px solid #ffffff;
            border-radius: 8px;
            color: white;
        }

        QToolButton:hover {
            background-color: #a31bf7;
            border: 1px solid #ffffff;
        }

        QTextEdit {
            border-radius: 8px;
        }

        QFrame#infoBubble {
            background-color: transparent;
            border: 2px solid #D084FF;
            border-radius: 20px;
        }

        QDockWidget {
            background: qradialgradient(
                cx:0.5, cy:0.5, radius:1.0,
                fx:0.5, fy:0.5,
                stop:0 #2a1b3d,
                stop:1 #0f0718
            );
            color: white;
            border: 1px solid #2e1a40;
        }
        """

    def center_window(self):
        screen = QApplication.primaryScreen().geometry()
        screen_w = screen.width()
        screen_h = screen.height()
        x = int((screen_w - self.WIDTH) / 2)
        y = int((screen_h - self.HEIGHT) / 2)
        self.move(x, y)

    def toggle_help_panel(self):
        if self.help_dock.isVisible():
            self.help_dock.hide()
        else:
            self.help_dock.show()

    def toggle_settings_panel(self):
        if self.settings_dock.isVisible():
            self.settings_dock.hide()
        else:
            self.settings_dock.show()

    def append_log(self, text: str):
        self.log.append(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def set_status(self, text: str, color: str = "#7CFF7C"):
        self.status_value.setText(text)
        self.status_value.setStyleSheet(f"color: {color}; margin-left: 8px;")

    def _gleam_start_if_idle(self):
        if self.status_value.text() == "Idle":
            self.start_btn.trigger_gleam()

    def _build_bot_settings(self) -> BotSettings:
        return BotSettings(
            target_name=self.monster_name.text(),
            auto_focus=self.settings_panel.checkbox_focus.isChecked(),
            safety_checks=self.settings_panel.checkbox_safe.isChecked(),
            runtime_limit_enabled=self.settings_panel.checkbox_limit_minutes.isChecked(),
            runtime_limit_minutes=self.settings_panel.spin_limit_minutes.value(),
            loop_limit_enabled=self.settings_panel.checkbox_limit_loops.isChecked(),
            loop_limit_count=self.settings_panel.spin_limit_loops.value(),
            # Keep safe by default; switch to False when you add real input sending.
            dry_run=True,
        )

    def _on_bot_finished(self, success: bool, reason: str):
        if reason.startswith("Error"):
            self.set_status("Error", "#FF6B6B")
        else:
            self.set_status("Idle", "#7CFF7C")
        self.append_log(f"GameBot finished: {reason}")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_start(self):
        if self.bot and self.bot.is_running:
            self.append_log("GameBot is already running.")
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.set_status("Starting...", "#FFD166")

        self.bot = GameBot(
            settings=self._build_bot_settings(),
            on_log=self.log_signal.emit,
            on_status=self.status_signal.emit,
            on_finished=self.bot_finished_signal.emit,
        )
        if not self.bot.start():
            self.append_log("Failed to start GameBot.")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.set_status("Idle", "#7CFF7C")

    def on_stop(self):
        if self.bot and self.bot.is_running:
            self.append_log("Stop requested by user.")
            self.set_status("Stopping...", "#FF6B6B")
            self.bot.stop()
        else:
            self.append_log("GameBot is not running.")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def closeEvent(self, event):
        if self.bot and self.bot.is_running:
            self.bot.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    mw = MainWindow()

    base_dir = Path(__file__).resolve().parent
    intro_candidates = [
        base_dir / "introf.mp4",
        base_dir / "assets" / "intro.mp4",
        base_dir / "logo_intro.mp4",
    ]
    intro_path = next((p for p in intro_candidates if p.exists()), None)

    if HAS_QT_MULTIMEDIA and intro_path is not None:
        splash = IntroVideoSplash(intro_path, on_finished=mw.show)
        app._intro_splash = splash
        splash.start()
    else:
        mw.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
