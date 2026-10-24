#!/usr/bin/env python3
"""
CursorFX - Mouse pointer highlighter for screen recording and streaming.

Features:
- Yellow circle highlighting the pointer location
- Pulsing ripple effect on mouse clicks
- Transparent overlay that passes through clicks
"""

import sys
import threading
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QPointF, pyqtSignal, QObject, QSettings
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QRadialGradient, QCursor, QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QColorDialog, QSystemTrayIcon, QMenu, QAction,
)

# X11 imports for global mouse click detection
from Xlib import X, XK, display
from Xlib.ext import record
from Xlib.protocol import rq


SPECIAL_KEY_NAMES = {
    XK.XK_Escape: "Esc",
    XK.XK_BackSpace: "Backspace",
    XK.XK_Return: "Enter",
    XK.XK_KP_Enter: "Enter",
    XK.XK_Delete: "Delete",
    XK.XK_Insert: "Insert",
    XK.XK_Tab: "Tab",
    XK.XK_Home: "Home",
    XK.XK_End: "End",
    XK.XK_Prior: "Page Up",
    XK.XK_Next: "Page Down",
    XK.XK_Left: "←",
    XK.XK_Right: "→",
    XK.XK_Up: "↑",
    XK.XK_Down: "↓",
    XK.XK_Shift_L: "Shift esquerdo",
    XK.XK_Shift_R: "Shift direito",
    XK.XK_Control_L: "Ctrl esquerdo",
    XK.XK_Control_R: "Ctrl direito",
    XK.XK_Alt_L: "Alt esquerdo",
    XK.XK_Alt_R: "Alt direito",
    XK.XK_Super_L: "Super esquerdo",
    XK.XK_Super_R: "Super direito",
    XK.XK_Caps_Lock: "Caps Lock",
    XK.XK_Num_Lock: "Num Lock",
    XK.XK_space: "Espaço",
    0xFE20: "Tab",   # ISO_Left_Tab
    0xFE03: "AltGr", # ISO_Level3_Shift
    0xFE50: "Acento grave (`)",
    0xFE51: "Acento agudo (´)",
    0xFE52: "Circunflexo (^)",
    0xFE53: "Til (~)",
    0xFE57: "Trema (¨)",
    0xFE5B: "Cedilha (¸)",
    0xFE08: "Trocar grupo do teclado",
    0xFE11: "Nível 5",
}

KEYSYM_NAMES = {}
for _constant_name, _keysym_value in vars(XK).items():
    if _constant_name.startswith("XK_") and isinstance(_keysym_value, int):
        KEYSYM_NAMES.setdefault(_keysym_value, _constant_name[3:])


def readable_key_name(keysym: int, keycode: int) -> str:
    """Return a printable label for any keysym known to python-xlib."""
    if keysym in SPECIAL_KEY_NAMES:
        return SPECIAL_KEY_NAMES[keysym]

    # Printable Latin-1 keysyms map directly to their character.
    if 0x20 <= keysym <= 0xFF:
        character = chr(keysym)
        if character.isprintable():
            return character

    # X11 Unicode keysyms use 0x01000000 | Unicode code point.
    if keysym & 0xFF000000 == 0x01000000:
        try:
            character = chr(keysym & 0x00FFFFFF)
            if character.isprintable():
                return character
        except ValueError:
            pass

    name = KEYSYM_NAMES.get(keysym)
    if name:
        if name.startswith("dead_"):
            return "Acento " + name[5:].replace("_", " ")
        if name.startswith("KP_"):
            return "Num " + name[3:].replace("_", " ")
        if name.startswith("XF86"):
            return name[4:]
        return name.replace("_", " ")

    return f"Key {keycode}"


@dataclass
class Config:
    """Configuration for the cursor highlighter."""
    # Highlight circle
    highlight_color: tuple = (255, 220, 0)  # Yellow RGB
    highlight_radius: int = 25
    highlight_opacity: float = 0.4
    highlight_border_width: int = 3
    highlight_border_opacity: float = 0.8

    # Position offset (to center on cursor tip)
    offset_x: int = 0
    offset_y: int = -30  # Negative moves circle up (optimized for arrow cursor)

    # Click ripple effect
    ripple_color: tuple = (255, 180, 0)  # Orange-yellow RGB
    ripple_max_radius: int = 60
    ripple_duration_ms: int = 400
    ripple_rings: int = 2

    # Keyboard overlay. Key names are kept only in memory until this timeout.
    show_keys: bool = True
    key_timeout_ms: int = 1600
    key_font_size: int = 22
    key_background_color: tuple = (20, 24, 32)
    key_text_color: tuple = (255, 255, 255)

    # Performance
    update_interval_ms: int = 16  # ~60 FPS


@dataclass
class Ripple:
    """Represents an active click ripple animation."""
    x: float
    y: float
    progress: float = 0.0  # 0.0 to 1.0

    def is_finished(self) -> bool:
        return self.progress >= 1.0


class InputEventSignal(QObject):
    """Qt signals for input events received by the X11 listener thread."""
    clicked = pyqtSignal(int, int)
    key_pressed = pyqtSignal(str)


class X11InputListener:
    """Passively observes mouse and keyboard events through X11 RECORD."""

    def __init__(self, click_callback, key_callback, show_keys=True):
        self.click_callback = click_callback
        self.key_callback = key_callback
        self.keys_enabled = True
        self.show_keys = show_keys
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.record_ctx = None
        self.local_display = None
        self.record_display = None

    def start(self):
        """Start listening for mouse events in a background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop listening for mouse events."""
        self.running = False
        if self.record_ctx and self.record_display:
            try:
                self.record_display.record_disable_context(self.record_ctx)
                self.record_display.flush()
            except Exception:
                pass

    def _listen(self):
        """Background thread that captures X11 mouse events."""
        try:
            self.local_display = display.Display()
            self.record_display = display.Display()

            ctx = self.record_display.record_create_context(
                0,
                [record.AllClients],
                [{
                    'core_requests': (0, 0),
                    'core_replies': (0, 0),
                    'ext_requests': (0, 0, 0, 0),
                    'ext_replies': (0, 0, 0, 0),
                    'delivered_events': (0, 0),
                    # X RECORD expects an inclusive event-type range here,
                    # not the event-selection mask constants.
                    'device_events': (X.KeyPress, X.ButtonRelease),
                    'errors': (0, 0),
                    'client_started': False,
                    'client_died': False,
                }]
            )
            self.record_ctx = ctx

            self.record_display.record_enable_context(ctx, self._handle_event)
            self.record_display.record_free_context(ctx)

        except Exception as e:
            print(f"X11 mouse listener error: {e}", file=sys.stderr)

    def _handle_event(self, reply):
        """Process X11 record events."""
        if not self.running:
            return

        data = reply.data
        while len(data):
            event, data = rq.EventField(None).parse_binary_value(
                data, self.local_display.display, None, None
            )

            if event.type == X.ButtonPress:
                # Button 1 = left click, 2 = middle, 3 = right
                if event.detail in (1, 2, 3):
                    self.click_callback(event.root_x, event.root_y)
            elif event.type == X.KeyPress:
                self._handle_key(event)

    def _handle_key(self, event):
        """Emit a key label only; never log or persist key events."""
        shifted = bool(event.state & X.ShiftMask) ^ bool(event.state & X.LockMask)
        level = 1 if shifted else 0
        if event.state & X.Mod5Mask:
            level = 3 if level else 2
        keysym = self.local_display.keycode_to_keysym(event.detail, level)
        key_name = readable_key_name(keysym, event.detail)

        # Readable labels for common control keys.
        # Show modifier keys by themselves. For chords, the following key also
        # displays the active modifiers as a combined label.
        modifier_keys = {
            "Shift esquerdo", "Shift direito", "Ctrl esquerdo", "Ctrl direito",
            "Alt esquerdo", "Alt direito", "Super esquerdo", "Super direito", "AltGr",
        }
        if key_name in modifier_keys:
            if self.show_keys:
                self.key_callback(key_name)
            return

        modifiers = []
        state = event.state
        if state & X.ControlMask:
            modifiers.append("Ctrl")
        if state & X.Mod1Mask:
            modifiers.append("Alt")
        if state & X.ShiftMask:
            modifiers.append("Shift")
        if state & X.Mod4Mask:
            modifiers.append("Super")
        if state & X.Mod5Mask:
            modifiers.append("AltGr")

        # Ctrl+Alt+K toggles key-name display; while paused, no labels are emitted.
        if (self.keys_enabled and key_name.lower() == "k"
                and "Ctrl" in modifiers and "Alt" in modifiers):
            self.show_keys = not self.show_keys
            self.key_callback("__TOGGLE_KEYS__")
            return
        if (key_name.lower() == "o" and "Ctrl" in modifiers
                and "Alt" in modifiers):
            self.key_callback("__TOGGLE_PANEL__")
            return
        if self.show_keys:
            self.key_callback(" + ".join(modifiers + [key_name]))


class CursorHighlighter(QWidget):
    """Transparent overlay widget that highlights the cursor."""

    panel_toggle_requested = pyqtSignal()

    def __init__(self, config: Optional[Config] = None):
        super().__init__()
        self.config = config or Config()
        self.ripples: List[Ripple] = []
        self.cursor_enabled = True
        self.keyboard_enabled = self.config.show_keys
        self.control_panel = None

        # Signal for cross-thread click notifications
        self.input_signal = InputEventSignal()
        self.input_signal.clicked.connect(self._on_click)
        self.input_signal.key_pressed.connect(self._on_key)

        # X11 input listener. It does not consume or change user input.
        self.input_listener = X11InputListener(
            lambda x, y: self.input_signal.clicked.emit(x, y),
            self.input_signal.key_pressed.emit,
            show_keys=self.config.show_keys,
        )

        self._setup_window()
        self._setup_timers()

    def _setup_window(self):
        """Configure the transparent overlay window."""
        # Frameless, transparent, always on top
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |  # Don't show in taskbar
            Qt.WindowTransparentForInput  # Click-through
        )

        # Enable transparency
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        # Cover all screens (multi-monitor support)
        primary = QApplication.primaryScreen()
        virtual = primary.virtualGeometry()
        self.setGeometry(virtual)
        # The overlay spans the virtual desktop, but its painter uses local
        # coordinates. Anchor the label inside the primary monitor's rectangle.
        self.primary_local_geometry = primary.geometry().translated(-virtual.x(), -virtual.y())
        self.key_label = ""
        self.key_visible_until = 0

    def _setup_timers(self):
        """Set up update timers."""
        # Main update timer for cursor tracking and animations
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update)
        self.update_timer.start(self.config.update_interval_ms)

        # Ripple animation timer
        self.ripple_timer = QTimer(self)
        self.ripple_timer.timeout.connect(self._update_ripples)
        self.ripple_timer.start(16)  # 60 FPS for smooth animation

    def start(self):
        """Start the highlighter."""
        self.input_listener.start()
        self.show()

    def stop(self):
        """Stop the highlighter."""
        self.input_listener.stop()
        self.hide()

    def _on_key(self, label: str):
        if label == "__TOGGLE_KEYS__":
            self.set_keyboard_enabled(not self.keyboard_enabled, show_status=True)
            return
        if label == "__TOGGLE_PANEL__":
            self.panel_toggle_requested.emit()
            return
        if not self.keyboard_enabled:
            return
        self.key_label = label
        self.key_visible_until = self._now_ns() + self.config.key_timeout_ms * 1000000
        self.update()

    def set_cursor_enabled(self, enabled: bool):
        self.cursor_enabled = bool(enabled)
        if not self.cursor_enabled:
            self.ripples.clear()
        self.update()
        if self.control_panel:
            self.control_panel.set_cursor_state(self.cursor_enabled)

    def set_highlight_color(self, rgb):
        """Update the halo color and redraw immediately."""
        self.config.highlight_color = tuple(int(channel) for channel in rgb)
        self.update()

    def set_keyboard_enabled(self, enabled: bool, show_status: bool = False):
        self.keyboard_enabled = bool(enabled)
        self.input_listener.show_keys = self.keyboard_enabled
        if show_status:
            self.key_label = "Teclas ativadas" if self.keyboard_enabled else "Teclas pausadas"
            self.key_visible_until = self._now_ns() + self.config.key_timeout_ms * 1000000
        else:
            self.key_label = ""
        self.update()
        if self.control_panel:
            self.control_panel.set_keyboard_state(self.keyboard_enabled)

    @staticmethod
    def _now_ns():
        import time
        return time.monotonic_ns()

    def _on_click(self, x: int, y: int):
        """Handle mouse click - create a new ripple."""
        # Apply offset to match highlight position
        self.ripples.append(Ripple(
            x=float(x + self.config.offset_x),
            y=float(y + self.config.offset_y)
        ))

    def _update(self):
        """Update the display."""
        self.update()

    def _update_ripples(self):
        """Update ripple animations."""
        if not self.ripples:
            return

        # Progress ripples
        progress_step = 16 / self.config.ripple_duration_ms
        for ripple in self.ripples:
            ripple.progress += progress_step

        # Remove finished ripples
        self.ripples = [r for r in self.ripples if not r.is_finished()]

    def paintEvent(self, event):
        """Paint the cursor highlight and ripples."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Get current cursor position with offset applied
        cursor_pos = QCursor.pos()
        x = cursor_pos.x() + self.config.offset_x
        y = cursor_pos.y() + self.config.offset_y

        # Draw ripples first (behind the highlight)
        if self.cursor_enabled:
            self._draw_ripples(painter)
            self._draw_highlight(painter, x, y)
        self._draw_key_label(painter)

    def _draw_key_label(self, painter):
        """Draw a short-lived key label at the top center of the screen."""
        if not self.keyboard_enabled or not self.key_label:
            return
        if self._now_ns() > self.key_visible_until:
            return

        painter.save()
        font = QFont("Sans Serif", self.config.key_font_size, QFont.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(self.key_label)
        box_width = text_width + 36
        box_height = metrics.height() + 20
        monitor = self.primary_local_geometry
        x = monitor.x() + (monitor.width() - box_width) // 2
        y = monitor.y() + monitor.height() - box_height - 40

        painter.setPen(Qt.NoPen)
        background = QColor(*self.config.key_background_color)
        background.setAlpha(220)
        painter.setBrush(background)
        painter.drawRoundedRect(x, y, box_width, box_height, 12, 12)
        text_color = QColor(*self.config.key_text_color)
        text_color.setAlpha(245)
        painter.setPen(text_color)
        painter.drawText(x + 18, y + 10, box_width - 36, box_height - 20,
                         Qt.AlignCenter, self.key_label)
        painter.restore()

    def _draw_highlight(self, painter: QPainter, x: int, y: int):
        """Draw the cursor highlight circle."""
        cfg = self.config

        # Create gradient for the highlight fill
        gradient = QRadialGradient(x, y, cfg.highlight_radius)
        color = QColor(*cfg.highlight_color)

        # Inner color (more opaque)
        inner_color = QColor(color)
        inner_color.setAlphaF(cfg.highlight_opacity * 0.3)
        gradient.setColorAt(0, inner_color)

        # Outer color (more visible)
        outer_color = QColor(color)
        outer_color.setAlphaF(cfg.highlight_opacity)
        gradient.setColorAt(0.7, outer_color)

        # Edge fade
        edge_color = QColor(color)
        edge_color.setAlphaF(0)
        gradient.setColorAt(1, edge_color)

        # Draw filled circle
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            QPointF(x, y),
            cfg.highlight_radius,
            cfg.highlight_radius
        )

        # Draw border ring
        border_color = QColor(*cfg.highlight_color)
        border_color.setAlphaF(cfg.highlight_border_opacity)
        pen = QPen(border_color, cfg.highlight_border_width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(
            QPointF(x, y),
            cfg.highlight_radius - cfg.highlight_border_width / 2,
            cfg.highlight_radius - cfg.highlight_border_width / 2
        )

    def _draw_ripples(self, painter: QPainter):
        """Draw click ripple animations."""
        cfg = self.config

        for ripple in self.ripples:
            # Ease-out animation curve
            t = ripple.progress
            eased = 1 - (1 - t) ** 3

            # Calculate ripple size
            current_radius = cfg.highlight_radius + (
                (cfg.ripple_max_radius - cfg.highlight_radius) * eased
            )

            # Fade out as it expands
            alpha = (1 - t) * 0.6

            # Draw multiple rings for a richer effect
            for ring in range(cfg.ripple_rings):
                ring_offset = ring * 8
                ring_radius = current_radius + ring_offset
                ring_alpha = alpha * (1 - ring * 0.3)

                if ring_alpha <= 0:
                    continue

                color = QColor(*cfg.ripple_color)
                color.setAlphaF(ring_alpha)

                pen = QPen(color, max(1, 3 - ring))
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(
                    QPointF(ripple.x, ripple.y),
                    ring_radius,
                    ring_radius
                )

    def closeEvent(self, event):
        """Clean up on close."""
        self.stop()
        super().closeEvent(event)


class ToggleButton(QPushButton):
    """Compact on/off switch for the control panel."""

    def __init__(self, checked=True, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(92, 32)
        self.setStyleSheet("""
            QPushButton {
                background: #596273;
                color: white;
                border: 0;
                border-radius: 16px;
                font-weight: 700;
            }
            QPushButton:checked { background: #168c52; }
        """)
        self.toggled.connect(self._update_text)
        self.setChecked(checked)
        self._update_text(checked)

    def _update_text(self, enabled):
        self.setText("LIGADO" if enabled else "DESLIGADO")

    def set_on(self, enabled):
        previous = self.blockSignals(True)
        self.setChecked(bool(enabled))
        self._update_text(bool(enabled))
        self.blockSignals(previous)


class ControlPanel(QWidget):
    """Small panel for toggling mouse and keyboard overlays."""

    def __init__(self, highlighter):
        super().__init__()
        self.highlighter = highlighter
        self.settings = QSettings()
        highlighter.control_panel = self
        highlighter.panel_toggle_requested.connect(self.toggle_panel)

        self.setWindowTitle("CursorFX — Painel")
        self.setFixedWidth(350)
        self.setStyleSheet("""
            QWidget { background: #171b24; color: #f3f5f7; }
            QLabel#title { font-size: 20px; font-weight: 700; }
            QLabel#description { color: #b7bfcc; }
            QPushButton#quit { background: #713b43; border: 0; border-radius: 6px; padding: 8px 12px; }
            QPushButton#quit:hover { background: #8b4650; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        title = QLabel("CursorFX")
        title.setObjectName("title")
        layout.addWidget(title)

        description = QLabel("Controles da sobreposição")
        description.setObjectName("description")
        layout.addWidget(description)

        self.cursor_switch = ToggleButton(highlighter.cursor_enabled)
        self.keyboard_switch = ToggleButton(highlighter.keyboard_enabled)
        self.cursor_switch.toggled.connect(highlighter.set_cursor_enabled)
        self.keyboard_switch.toggled.connect(highlighter.set_keyboard_enabled)

        self._add_switch_row(layout, "Halo e cliques do mouse", self.cursor_switch)
        self._add_switch_row(layout, "Mostrar teclas", self.keyboard_switch)

        self.color_buttons = {}
        self._add_color_picker(layout, "Cor do halo", "highlight_color", self.choose_highlight_color)
        self._add_color_picker(layout, "Fundo das teclas", "key_background_color", self.choose_key_background_color)
        self._add_color_picker(layout, "Cor das letras", "key_text_color", self.choose_key_text_color)

        shortcut = QLabel(
            "Atalhos: Ctrl+Alt+K alterna as teclas · Ctrl+Alt+O minimiza ou reabre o painel. "
            "Também é possível reabrir pelo ícone do CursorFX na bandeja."
        )
        shortcut.setObjectName("description")
        shortcut.setWordWrap(True)
        layout.addWidget(shortcut)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        quit_button = QPushButton("Sair")
        quit_button.setObjectName("quit")
        quit_button.clicked.connect(QApplication.quit)
        buttons.addWidget(quit_button)
        layout.addLayout(buttons)

        self.adjustSize()
        available = QApplication.primaryScreen().availableGeometry()
        self.move(available.right() - self.width() - 24, available.top() + 32)

        self.tray_icon = None
        self._create_tray_icon()

    @staticmethod
    def _add_switch_row(layout, title, switch):
        row = QHBoxLayout()
        label = QLabel(title)
        row.addWidget(label, 1)
        row.addWidget(switch)
        layout.addLayout(row)

    def _add_color_picker(self, layout, title, setting_name, callback):
        row = QHBoxLayout()
        row.addWidget(QLabel(title), 1)
        button = QPushButton()
        button.setFixedSize(92, 32)
        button.clicked.connect(callback)
        self.color_buttons[setting_name] = button
        row.addWidget(button)
        layout.addLayout(row)
        self._update_color_button(setting_name)

    def set_cursor_state(self, enabled):
        self.cursor_switch.set_on(enabled)
        self._sync_tray_action(getattr(self, "tray_cursor_action", None), enabled)

    def set_keyboard_state(self, enabled):
        self.keyboard_switch.set_on(enabled)
        self._sync_tray_action(getattr(self, "tray_keyboard_action", None), enabled)

    @staticmethod
    def _sync_tray_action(action, enabled):
        if action is None:
            return
        previous = action.blockSignals(True)
        action.setChecked(bool(enabled))
        action.blockSignals(previous)

    def choose_highlight_color(self):
        self._choose_color("highlight_color", "Escolher cor do halo")

    def choose_key_background_color(self):
        self._choose_color("key_background_color", "Escolher cor do fundo das teclas")

    def choose_key_text_color(self):
        self._choose_color("key_text_color", "Escolher cor das letras")

    def _choose_color(self, setting_name, title):
        current = QColor(*getattr(self.highlighter.config, setting_name))
        color = QColorDialog.getColor(current, self, title)
        if not color.isValid():
            return
        rgb = (color.red(), color.green(), color.blue())
        setattr(self.highlighter.config, setting_name, rgb)
        self.settings.setValue(setting_name, color.name())
        self.highlighter.update()
        self._update_color_button(setting_name)

    def _update_color_button(self, setting_name):
        color = QColor(*getattr(self.highlighter.config, setting_name))
        button = self.color_buttons[setting_name]
        button.setText(color.name().upper())
        button.setStyleSheet(
            f"QPushButton {{ background: {color.name()}; color: "
            f"{'#111111' if color.lightness() > 140 else '#ffffff'}; "
            "border: 1px solid #8b94a3; border-radius: 6px; font-weight: 700; }"
        )

    def _create_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = Path(__file__).resolve().with_name("cursorfx.svg")
        self.tray_icon = QSystemTrayIcon(QIcon(str(icon_path)), self)
        self.tray_icon.setToolTip("CursorFX — clique para abrir o painel")
        menu = QMenu(self)
        open_action = QAction("Abrir painel", self)
        open_action.triggered.connect(self.show_panel)
        menu.addAction(open_action)
        menu.addSeparator()
        cursor_action = QAction("Halo e cliques do mouse", self)
        cursor_action.setCheckable(True)
        cursor_action.setChecked(self.highlighter.cursor_enabled)
        cursor_action.toggled.connect(self.highlighter.set_cursor_enabled)
        menu.addAction(cursor_action)
        keyboard_action = QAction("Mostrar teclas", self)
        keyboard_action.setCheckable(True)
        keyboard_action.setChecked(self.highlighter.keyboard_enabled)
        keyboard_action.toggled.connect(self.highlighter.set_keyboard_enabled)
        menu.addAction(keyboard_action)
        menu.addSeparator()
        quit_action = QAction("Sair", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()
        self.tray_cursor_action = cursor_action
        self.tray_keyboard_action = keyboard_action

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel()

    def show_panel(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_panel(self):
        if self.isVisible() and not self.isMinimized():
            self.showMinimized()
        else:
            self.show_panel()

    def closeEvent(self, event):
        event.ignore()
        self.showMinimized()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='CursorFX - Mouse pointer highlighter for screen recording'
    )
    parser.add_argument(
        '--color', type=str, default=None,
        help='Highlight color as R,G,B (default: 255,220,0 yellow)'
    )
    parser.add_argument(
        '--radius', type=int, default=25,
        help='Highlight circle radius in pixels (default: 25)'
    )
    parser.add_argument(
        '--opacity', type=float, default=0.4,
        help='Highlight opacity 0.0-1.0 (default: 0.4)'
    )
    parser.add_argument(
        '--ripple-radius', type=int, default=60,
        help='Maximum ripple radius (default: 60)'
    )
    parser.add_argument(
        '--ripple-duration', type=int, default=400,
        help='Ripple animation duration in ms (default: 400)'
    )
    parser.add_argument(
        '--offset-x', type=int, default=0,
        help='Horizontal offset in pixels (default: 0)'
    )
    parser.add_argument(
        '--offset-y', type=int, default=-30,
        help='Vertical offset in pixels, negative=up (default: -30)'
    )
    parser.add_argument(
        '--key-timeout', type=int, default=1600,
        help='Key label visibility in milliseconds (default: 1600)'
    )
    parser.add_argument(
        '--no-keys', action='store_true',
        help='Disable keyboard labels'
    )

    args = parser.parse_args()

    # Parse and validate color
    try:
        settings = QSettings("CursorFX", "CursorFX")
        def load_rgb(name, default):
            saved = QColor(str(settings.value(name, QColor(*default).name())))
            return (saved.red(), saved.green(), saved.blue()) if saved.isValid() else default

        color = (
            tuple(int(c) for c in args.color.split(','))
            if args.color
            else load_rgb("highlight_color", (255, 220, 0))
        )
        if len(color) != 3:
            raise ValueError("must have 3 components")
        for i, c in enumerate(color):
            if not 0 <= c <= 255:
                print(f"Color value {c} out of range. Each RGB value must be 0-255.", file=sys.stderr)
                sys.exit(1)
    except ValueError:
        print("Invalid color format. Use R,G,B (e.g., 255,220,0)", file=sys.stderr)
        sys.exit(1)

    # Validate opacity
    if not 0.0 <= args.opacity <= 1.0:
        print("Opacity must be between 0.0 and 1.0", file=sys.stderr)
        sys.exit(1)

    # Validate radius
    if args.radius <= 0:
        print("Radius must be greater than 0", file=sys.stderr)
        sys.exit(1)

    if args.ripple_radius <= 0:
        print("Ripple radius must be greater than 0", file=sys.stderr)
        sys.exit(1)

    # Validate duration
    if args.ripple_duration <= 0:
        print("Ripple duration must be greater than 0", file=sys.stderr)
        sys.exit(1)

    if args.key_timeout <= 0:
        print("Key timeout must be greater than 0", file=sys.stderr)
        sys.exit(1)

    # Check X11 RECORD extension availability
    try:
        test_display = display.Display()
        if not test_display.has_extension('RECORD'):
            print("Warning: X11 RECORD extension not available. Click ripples will not work.", file=sys.stderr)
        test_display.close()
    except Exception as e:
        print(f"Warning: Could not check X11 RECORD extension: {e}", file=sys.stderr)

    # Create config
    config = Config(
        highlight_color=color,
        highlight_radius=args.radius,
        highlight_opacity=args.opacity,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
        ripple_max_radius=args.ripple_radius,
        ripple_duration_ms=args.ripple_duration,
        show_keys=not args.no_keys,
        key_timeout_ms=args.key_timeout,
        key_background_color=load_rgb("key_background_color", (20, 24, 32)),
        key_text_color=load_rgb("key_text_color", (255, 255, 255)),
    )

    # Create and run application
    app = QApplication(sys.argv)
    app.setApplicationName('CursorFX')
    app.setOrganizationName('CursorFX')

    highlighter = CursorHighlighter(config)
    highlighter.start()
    panel = ControlPanel(highlighter)
    panel.show()

    print("CursorFX running. Ctrl+Alt+O opens or hides the control panel.")

    # Handle Ctrl+C gracefully
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
