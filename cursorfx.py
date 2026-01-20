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

from PyQt5.QtCore import Qt, QTimer, QPointF, pyqtSignal, QObject
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QRadialGradient
from PyQt5.QtWidgets import QApplication, QWidget

# X11 imports for global mouse click detection
from Xlib import X, display
from Xlib.ext import record
from Xlib.protocol import rq


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


class MouseClickSignal(QObject):
    """Qt signal wrapper for mouse click events from X11 thread."""
    clicked = pyqtSignal(int, int)


class X11MouseListener:
    """Listens for global mouse click events using X11 record extension."""

    def __init__(self, callback):
        self.callback = callback
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.record_ctx = None
        self.local_display = None

    def start(self):
        """Start listening for mouse events in a background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop listening for mouse events."""
        self.running = False
        if self.record_ctx and self.local_display:
            try:
                self.local_display.record_disable_context(self.record_ctx)
                self.local_display.flush()
            except Exception:
                pass

    def _listen(self):
        """Background thread that captures X11 mouse events."""
        try:
            self.local_display = display.Display()
            record_display = display.Display()

            ctx = record_display.record_create_context(
                0,
                [record.AllClients],
                [{
                    'core_requests': (0, 0),
                    'core_replies': (0, 0),
                    'ext_requests': (0, 0, 0, 0),
                    'ext_replies': (0, 0, 0, 0),
                    'delivered_events': (0, 0),
                    'device_events': (X.ButtonPressMask, X.ButtonReleaseMask),
                    'errors': (0, 0),
                    'client_started': False,
                    'client_died': False,
                }]
            )
            self.record_ctx = ctx

            record_display.record_enable_context(ctx, self._handle_event)
            record_display.record_free_context(ctx)

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
                    self.callback(event.root_x, event.root_y)


class CursorHighlighter(QWidget):
    """Transparent overlay widget that highlights the cursor."""

    def __init__(self, config: Optional[Config] = None):
        super().__init__()
        self.config = config or Config()
        self.ripples: List[Ripple] = []

        # Signal for cross-thread click notifications
        self.click_signal = MouseClickSignal()
        self.click_signal.clicked.connect(self._on_click)

        # X11 mouse listener
        self.mouse_listener = X11MouseListener(
            lambda x, y: self.click_signal.clicked.emit(x, y)
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

        # Cover the entire screen
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

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
        self.mouse_listener.start()
        self.show()

    def stop(self):
        """Stop the highlighter."""
        self.mouse_listener.stop()
        self.hide()

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
        from PyQt5.QtGui import QCursor
        cursor_pos = QCursor.pos()
        x = cursor_pos.x() + self.config.offset_x
        y = cursor_pos.y() + self.config.offset_y

        # Draw ripples first (behind the highlight)
        self._draw_ripples(painter)

        # Draw the cursor highlight circle
        self._draw_highlight(painter, x, y)

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

                pen = QPen(color, 3 - ring)
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


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='CursorFX - Mouse pointer highlighter for screen recording'
    )
    parser.add_argument(
        '--color', type=str, default='255,220,0',
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

    args = parser.parse_args()

    # Parse color
    try:
        color = tuple(int(c) for c in args.color.split(','))
        if len(color) != 3:
            raise ValueError
    except ValueError:
        print("Invalid color format. Use R,G,B (e.g., 255,220,0)", file=sys.stderr)
        sys.exit(1)

    # Create config
    config = Config(
        highlight_color=color,
        highlight_radius=args.radius,
        highlight_opacity=args.opacity,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
        ripple_max_radius=args.ripple_radius,
        ripple_duration_ms=args.ripple_duration,
    )

    # Create and run application
    app = QApplication(sys.argv)
    app.setApplicationName('CursorFX')

    highlighter = CursorHighlighter(config)
    highlighter.start()

    print("CursorFX running. Press Ctrl+C to exit.")

    # Handle Ctrl+C gracefully
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
