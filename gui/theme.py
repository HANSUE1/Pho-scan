"""Modern dark theme: palette, stylesheet, glassmorphism, person colors and painted icons."""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)

BG = "#0f1115"
PANEL = "#1a1d23"
PANEL_2 = "#22262e"
BORDER = "#31353d"
BORDER_FOCUS = "#4f8cff"
TEXT = "#e8eaed"
MUTED = "#9aa0a8"
ACCENT = "#4f8cff"
ACCENT_2 = "#6ea3ff"
ACCENT_GLOW = "rgba(79, 140, 255, 0.35)"
SUCCESS = "#34c759"
WARNING = "#ff9500"
DANGER = "#ff3b30"

# Golden-angle hue spacing gives every person a stable, distinct color.
GOLDEN_ANGLE = 137.508

# Standard easing for all UI animations.
EASE_OUT = QEasingCurve.Type.OutCubic
EASE_OUT_BACK = QEasingCurve.Type.OutBack
EASE_IN_OUT = QEasingCurve.Type.InOutCubic


def person_color(person_id, alpha=1.0):
    if person_id is None:
        color = QColor.fromHslF(220 / 360, 0.05, 0.63)
    else:
        color = QColor.fromHslF(((person_id * GOLDEN_ANGLE) % 360) / 360, 0.75, 0.58)
    color.setAlphaF(alpha)
    return color


def tag_color(tag_id, alpha=1.0):
    """Tag hues share the golden-angle spacing but sit cooler and softer than
    person colors, so the two sidebar lists stay visually distinct."""
    color = QColor.fromHslF(((tag_id * GOLDEN_ANGLE + 40) % 360) / 360, 0.45, 0.68)
    color.setAlphaF(alpha)
    return color


def make_palette():
    pal = QPalette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        pal.setColor(group, QPalette.Window, QColor(BG))
        pal.setColor(group, QPalette.Base, QColor(BG))
        pal.setColor(group, QPalette.AlternateBase, QColor(PANEL))
        pal.setColor(group, QPalette.WindowText, QColor(TEXT))
        pal.setColor(group, QPalette.Text, QColor(TEXT))
        pal.setColor(group, QPalette.Button, QColor(PANEL_2))
        pal.setColor(group, QPalette.ButtonText, QColor(TEXT))
        pal.setColor(group, QPalette.ToolTipBase, QColor(PANEL_2))
        pal.setColor(group, QPalette.ToolTipText, QColor(TEXT))
        pal.setColor(group, QPalette.Highlight, QColor(ACCENT))
        pal.setColor(group, QPalette.HighlightedText, QColor("#ffffff"))
        pal.setColor(group, QPalette.PlaceholderText, QColor(MUTED))
    return pal


STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-size: 14px; }}

#topbar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
#brand {{ font-size: 18px; font-weight: 700; letter-spacing: -0.5px; }}

QLineEdit {{
  background: {BG}; border: 1px solid {BORDER}; border-radius: 10px;
  padding: 8px 14px; selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

QPushButton#analyzeBtn {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 #3a6ee0);
  color: #ffffff; font-weight: 600; font-size: 14px;
  border: none; border-radius: 10px; padding: 8px 24px;
}}
QPushButton#analyzeBtn:hover {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT_2}, stop:1 {ACCENT});
}}
QPushButton#analyzeBtn:pressed {{
  background: #3567d6;
}}
QPushButton#analyzeBtn:disabled {{ background: #3a4451; color: #c8ccd2; }}

QToolButton#browseBtn {{
  background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 10px;
  padding: 8px 14px; font-size: 13px;
}}
QToolButton#browseBtn:hover {{ border: 1px solid {ACCENT}; background: {PANEL}; }}
QToolButton#browseBtn:pressed {{ background: {PANEL_2}; border: 1px solid {ACCENT_2}; }}

#progressRow {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QProgressBar {{
  background: {PANEL_2}; border: none; border-radius: 6px;
  max-height: 10px; min-height: 10px; text-align: center;
}}
QProgressBar::chunk {{
  border-radius: 6px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
}}
QLabel#progressLabel, QLabel#mutedLabel {{ color: {MUTED}; font-size: 12px; }}

QFrame#banner {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(34,38,46,0.95), stop:1 {PANEL_2});
  border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#banner QLabel {{ font-size: 13px; background: transparent; }}
QPushButton#linkBtn {{
  color: {ACCENT_2}; background: transparent; border: none;
  font-size: 13px; text-decoration: underline; padding: 0;
}}
QPushButton#linkBtn:hover {{ color: {ACCENT}; }}

QLabel#emptyState {{ color: {MUTED}; font-size: 15px; }}

#sidebar {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
QLabel#sidebarTitle {{ color: {MUTED}; font-size: 11px; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase; }}

QToolButton#actionBtn {{
  background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QToolButton#actionBtn:hover {{ border: 1px solid {ACCENT}; background: {PANEL}; }}
QToolButton#actionBtn:pressed {{ background: {PANEL_2}; border: 1px solid {ACCENT_2}; }}

QFrame#personRow {{ background: transparent; border: 1px solid transparent; border-radius: 10px; }}
QFrame#personRow:hover {{ background: {PANEL_2}; }}
QFrame#personRow[active="true"] {{ background: {PANEL_2}; border: 1px solid {ACCENT}; }}
QLabel#personCount {{ color: {MUTED}; font-size: 12px; background: transparent; }}
QToolButton#personBtn {{
  background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px;
  color: {MUTED}; font-size: 13px; padding: 2px 6px;
}}
QToolButton#personBtn:hover {{ background: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; }}
QToolButton#personBtn:pressed {{ background: #3567d6; }}

QListView#gallery {{ background: {BG}; border: none; outline: none; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5058; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #4a5058; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER};
  border-radius: 6px; padding: 6px 10px; font-size: 12px; }}

QToolButton#lightboxClose {{
  background: rgba(15, 17, 21, 0.85); color: {TEXT};
  border: 1px solid {BORDER}; border-radius: 18px; font-size: 16px;
}}
QToolButton#lightboxClose:hover {{ background: rgba(26, 29, 35, 0.9); border: 1px solid {ACCENT}; }}

QComboBox, QSpinBox, QDoubleSpinBox {{
  background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
  padding: 6px 10px; min-width: 110px;
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox QAbstractItemView {{
  background: {PANEL_2}; border: 1px solid {BORDER};
  selection-background-color: {ACCENT};
}}

QDialog {{ background: {BG}; }}
QDialog QLabel {{ color: {TEXT}; }}
QDialog QPushButton {{
  background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px;
  padding: 6px 16px; min-width: 80px;
}}
QDialog QPushButton:hover {{ border: 1px solid {ACCENT}; background: {PANEL}; }}
QDialog QPushButton:default {{
  background: {ACCENT}; color: #ffffff; border: none;
}}
QDialog QPushButton:default:hover {{ background: {ACCENT_2}; }}
"""

# --------------------------------------------------------------------------- animations

CARD_SCALE_HOVER = 1.02
CARD_SCALE_SELECTED = 1.03
FADE_DURATION_MS = 220
SLIDE_DURATION_MS = 300
PANEL_FADE_DURATION_MS = 180


def glass_panel_style(border=BORDER, radius=12):
    """Translucent frosted-glass panel style."""
    return f"""
        QFrame {{
          background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(34, 38, 46, 0.85), stop:1 rgba(26, 29, 35, 0.85));
          border: 1px solid {border}; border-radius: {radius}px;
        }}
    """


def card_style(selected=False, hovered=False):
    """Photo card border style."""
    color = ACCENT if selected or hovered else BORDER
    width = 2 if (selected or hovered) else 1
    return f"""
        QFrame {{
          border: {width}px solid {color};
          border-radius: 12px;
          background: {PANEL};
        }}
    """


def empty_state_style():
    return f"""
        QLabel {{ color: {MUTED}; font-size: 15px; font-weight: 500;
          background: transparent; padding: 24px; }}
    """


def person_color_hex(person_id, alpha=1.0):
    """Return a hex color string for person_id."""
    c = person_color(person_id, alpha)
    return c.name() if alpha >= 1.0 else f"#{c.red():02x}{c.green():02x}{c.blue():02x}{int(c.alphaF()*255):02x}"


def tag_color_hex(tag_id, alpha=1.0):
    c = tag_color(tag_id, alpha)
    return c.name() if alpha >= 1.0 else f"#{c.red():02x}{c.green():02x}{c.blue():02x}{int(c.alphaF()*255):02x}"


# --------------------------------------------------------------------------- painted icons

def _painted_icon(draw, size=20):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(TEXT), 1.6))
    painter.setBrush(Qt.NoBrush)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


def aspect_icon(key):
    """Portrait ("34") or landscape ("43") rounded rectangle."""
    if key == "34":
        rect = QRectF(5.5, 2.5, 9, 15)
    else:
        rect = QRectF(2.5, 5.5, 15, 9)
    return _painted_icon(lambda p: p.drawRoundedRect(rect, 1.5, 1.5))


def _paint_app_icon(p):
    """Camera glyph on an accent gradient tile, drawn on a 256x256 canvas."""
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(8, 8, 240, 240), 52, 52)
    grad = QLinearGradient(0, 8, 0, 248)
    grad.setColorAt(0.0, QColor("#6ea3ff"))
    grad.setColorAt(1.0, QColor("#3567d6"))
    p.fillPath(tile, grad)

    body = QPainterPath()
    body.addRoundedRect(QRectF(40, 92, 176, 116), 22, 22)
    bump = QPainterPath()
    bump.addRoundedRect(QRectF(96, 68, 64, 40), 12, 12)
    p.fillPath(body.united(bump), QColor("#f4f6f9"))

    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3567d6"))
    p.drawEllipse(QRectF(184, 106, 18, 18))
    p.setBrush(QColor("#1b2634"))
    p.drawEllipse(QPointF(128, 150), 42, 42)
    p.setBrush(QColor("#4f8cff"))
    p.drawEllipse(QPointF(128, 150), 30, 30)
    p.setBrush(QColor("#dbe7ff"))
    p.drawEllipse(QPointF(138, 140), 8, 8)


def app_icon():
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(size / 256, size / 256)
        _paint_app_icon(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def eye_icon(off=False):
    def draw(p):
        p.drawEllipse(QRectF(2, 5.5, 16, 9))
        p.drawEllipse(QRectF(7.5, 7.5, 5, 5))
        if off:
            p.drawLine(4, 17, 16, 3)

    return _painted_icon(draw)


def sort_icon(order):
    """Decreasing bars for name order, a small calendar for date order."""

    def draw(p):
        if order == "date":
            p.drawRoundedRect(QRectF(3, 4.5, 14, 12), 2, 2)
            p.drawLine(3, 8, 17, 8)
            p.drawLine(7, 2.5, 7, 6)
            p.drawLine(13, 2.5, 13, 6)
        else:
            p.drawLine(3, 5, 17, 5)
            p.drawLine(3, 10, 13, 10)
            p.drawLine(3, 15, 9, 15)

    return _painted_icon(draw)


def pin_icon(active=False):
    """Map pin; filled while the location filter is on."""

    def draw(p):
        pin = QPainterPath()
        pin.moveTo(10, 18)
        pin.cubicTo(10, 13, 16, 11.5, 16, 7.5)
        pin.arcTo(QRectF(4, 1.5, 12, 12), 0, 180)
        pin.cubicTo(4, 11.5, 10, 13, 10, 18)
        if active:
            p.fillPath(pin, QColor(ACCENT))
        p.drawPath(pin)
        p.setBrush(QColor(BG) if active else Qt.NoBrush)
        p.drawEllipse(QRectF(7.6, 5.1, 4.8, 4.8))

    return _painted_icon(draw)


def gear_icon():
    def draw(p):
        p.drawEllipse(QRectF(6.5, 6.5, 7, 7))
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0),
                       (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)):
            p.drawLine(
                QPointF(10 + dx * 5.4, 10 + dy * 5.4),
                QPointF(10 + dx * 7.6, 10 + dy * 7.6),
            )

    return _painted_icon(draw)