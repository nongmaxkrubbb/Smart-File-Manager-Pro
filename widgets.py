"""Custom-painted widgets: score ring, bar list, cards, two-line table cells, toast, drop-enabled path box."""
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QPropertyAnimation, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QLinearGradient, QPainterPath, QBrush, QFont,
                           QPixmap, QPolygonF, QIcon, QFontMetrics, QIntValidator)
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QLabel, QLineEdit, QGraphicsOpacityEffect,
                               QPushButton, QCheckBox, QTableWidgetItem, QStyledItemDelegate, QStyle,
                               QStyleOptionViewItem, QApplication, QSizePolicy)

import themes
from i18n import tr

SORT_ROLE = Qt.UserRole + 1
SUB_ROLE = Qt.UserRole + 2        # second (muted) line under the main text of a table cell


class TLabel(QLabel):
    """QLabel that runs its text through tr() (also for later setText calls)."""
    def __init__(self, text=""):
        super().__init__(tr(text))

    def setText(self, text):
        super().setText(tr(text))


class TButton(QPushButton):
    def __init__(self, text=""):
        super().__init__(tr(text))

    def setText(self, text):
        super().setText(tr(text))


class TCheck(QCheckBox):
    def __init__(self, text=""):
        super().__init__(tr(text))


class SortItem(QTableWidgetItem):
    """Table item that sorts by a numeric key (stored in SORT_ROLE) when one is provided."""
    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text().lower() < other.text().lower()   # (super().__lt__ would recurse into this override)


class Ring(QWidget):
    """Progress ring with a big number in the middle (the tidiness score)."""

    def __init__(self, diameter=140, thickness=16):
        super().__init__()
        self.value, self.text, self.thickness = 0.0, "—", thickness
        self.setFixedSize(diameter, diameter)

    def set(self, value, text):
        self.value, self.text = max(0.0, min(100.0, float(value))), text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.thickness
        rect = QRectF(t / 2, t / 2, self.width() - t, self.height() - t)
        track = QPen(QColor(themes.color("track")), t)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        if self.value > 0:
            pen = QPen(QColor(themes.color("accent_btn")), t)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, -int(360 * 16 * self.value / 100))
        p.setPen(QColor(themes.color("text")))
        f = QFont(self.font())
        f.setPixelSize(round(self.width() * 0.31))
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, self.text)


class BarList(QWidget):
    """Rows of  label | bar | value  separated by thin lines (folders to create, space by type...)."""
    ROW = 42

    def __init__(self, empty_text="No data yet", label_w=112, value_w=84):
        super().__init__()
        self.items, self.empty_text = [], empty_text
        self.label_w, self.value_w = label_w, value_w
        self.setMinimumHeight(self.ROW)

    def set_items(self, items):
        """items: [(label, value, value_text)]"""
        self.items = list(items)
        self.setMinimumHeight(max(self.ROW, len(self.items) * self.ROW))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.items:
            p.setPen(QColor(themes.color("muted")))
            p.drawText(self.rect().adjusted(0, 0, 0, -self.height() // 3), Qt.AlignCenter, self.empty_text)
            return
        peak = max(v for _, v, _ in self.items) or 1
        w = self.width()
        bar_x = self.label_w + 16
        bar_w = max(w - bar_x - self.value_w - 16, 20)
        fm = p.fontMetrics()
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() * 0.92, 7))
        for i, (label, value, vtext) in enumerate(self.items):
            y = i * self.ROW
            mid = y + self.ROW / 2
            p.setPen(QColor(themes.color("text")))
            p.setFont(self.font())
            p.drawText(QRectF(0, y, self.label_w, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(label, Qt.ElideRight, self.label_w))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(themes.color("track")))
            p.drawRoundedRect(QRectF(bar_x, mid - 5, bar_w, 10), 5, 5)
            p.setBrush(QColor(themes.color("accent_btn")))
            p.drawRoundedRect(QRectF(bar_x, mid - 5, max(bar_w * value / peak, 10), 10), 5, 5)
            p.setPen(QColor(themes.color("muted")))
            p.setFont(small)
            p.drawText(QRectF(w - self.value_w, y, self.value_w, self.ROW), Qt.AlignVCenter | Qt.AlignRight, vtext)
            if i < len(self.items) - 1:
                p.setPen(QPen(QColor(themes.color("divider")), 1))
                p.drawLine(0, y + self.ROW - 1, w, y + self.ROW - 1)


class Card(QFrame):
    """White rounded card with an optional bold title (and hint) and a thin line under it. Put content in .body."""

    def __init__(self, title="", hint="", margins=(18, 18, 18, 18), divider=True):
        super().__init__()
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # equal-width cards whatever their content
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*margins)
        outer.setSpacing(0)
        self.title_label = None
        if title:
            self.title_label = TLabel(title)
            self.title_label.setObjectName("cardTitle")
            outer.addWidget(self.title_label)
            if hint:
                h = TLabel(hint)
                h.setObjectName("cardHint")
                outer.addSpacing(2)
                outer.addWidget(h)
            outer.addSpacing(6)
            if divider:
                line = QFrame()
                line.setObjectName("hline")
                line.setFixedHeight(1)
                outer.addWidget(line)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        outer.addLayout(self.body, 1)


class StatCard(QFrame):
    """Small card: muted label on top, big bold value below."""

    def __init__(self, label, value="—"):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(0)
        self.label = TLabel(label)
        self.label.setObjectName("statLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        lay.addWidget(self.label)
        lay.addWidget(self.value_label)

    def set_value(self, text):
        self.value_label.setText(text)

    def set_label(self, text):
        self.label.setText(text)


class TwoLineDelegate(QStyledItemDelegate):
    """Table cell with the normal text plus a smaller muted second line (SUB_ROLE) - used with checkboxes."""

    def paint(self, painter, option, index):
        sub = index.data(SUB_ROLE)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = opt.text
        opt.text = ""
        opt.state &= ~QStyle.State_HasFocus
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        if not title and not sub:
            return
        r = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget).adjusted(6, 0, -4, 0)
        painter.save()
        painter.setClipRect(r)
        f1 = QFont(opt.font)
        fm1 = QFontMetrics(f1)
        painter.setPen(QColor(themes.color("text")))
        painter.setFont(f1)
        if sub:
            f2 = QFont(opt.font)
            f2.setPointSizeF(max(f1.pointSizeF() * 0.82, 7))
            fm2 = QFontMetrics(f2)
            total = fm1.height() + fm2.height()
            top = r.top() + (r.height() - total) / 2
            painter.drawText(QRectF(r.left(), top, r.width(), fm1.height()), Qt.AlignLeft | Qt.AlignVCenter,
                             fm1.elidedText(title, Qt.ElideMiddle, r.width()))
            painter.setFont(f2)
            painter.setPen(QColor(themes.color("muted")))
            painter.drawText(QRectF(r.left(), top + fm1.height(), r.width(), fm2.height()), Qt.AlignLeft | Qt.AlignVCenter,
                             fm2.elidedText(sub, Qt.ElideMiddle, r.width()))
        else:
            painter.drawText(QRectF(r), Qt.AlignLeft | Qt.AlignVCenter, fm1.elidedText(title, Qt.ElideMiddle, r.width()))
        painter.restore()


class IntField(QLineEdit):
    """Borderless integer box with the QSpinBox bits the app uses (value / setValue / setRange / valueChanged)."""
    valueChanged = Signal(int)

    def __init__(self, lo=0, hi=999999, value=0):
        super().__init__(str(value))
        self.lo, self.hi, self._v = lo, hi, value
        self.setValidator(QIntValidator(lo, hi, self))
        self.setStyleSheet("QLineEdit { border: none; background: transparent; padding: 8px 4px; }")
        self.textChanged.connect(self._changed)

    def _changed(self, text):
        try:
            v = max(self.lo, min(self.hi, int(text)))
        except ValueError:
            return
        if v != self._v:
            self._v = v
            self.valueChanged.emit(v)

    def value(self):
        return self._v

    def setValue(self, v):
        self.setText(str(v))

    def setRange(self, lo, hi):
        self.lo, self.hi = lo, hi
        self.setValidator(QIntValidator(lo, hi, self))


class BandDelegate(QStyledItemDelegate):
    """Paints a cell's BackgroundRole (QSS would otherwise hide it) - used for group header rows."""

    def paint(self, painter, option, index):
        brush = index.data(Qt.BackgroundRole)
        if brush is not None:
            painter.fillRect(option.rect, brush)
        super().paint(painter, option, index)


def swatch_icon(theme_name, size=24):
    """Diagonal two-colour square used on the theme buttons in Settings."""
    t = themes.THEMES[theme_name]
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size * 2
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, s, s), s * 0.28, s * 0.28)
    p.setClipPath(clip)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(t["bg"]))
    p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(s, 0), QPointF(0, s)]))
    p.setBrush(QColor(t["accent_btn"] if theme_name == "Light" else t["accent"]))
    p.drawPolygon(QPolygonF([QPointF(s, 0), QPointF(s, s), QPointF(0, s)]))
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.hide()
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade_out)
        self._hiding = False
        self.anim.finished.connect(self._after_anim)

    def show_message(self, text, kind="info"):
        key = {"ok": "green", "error": "red", "warn": "amber"}.get(kind, "accent")
        self.setStyleSheet(f"background: {themes.color('panel2')}; color: {themes.color('text')};"
                           f"border: 1px solid {themes.color(key)}; border-left: 4px solid {themes.color(key)};"
                           "border-radius: 10px; padding: 11px 18px; font-weight: 600;")
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move(par.width() - self.width() - 24, par.height() - self.height() - 24)
        self._hiding = False
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setDuration(160)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.timer.start(2800)

    def _fade_out(self):
        self._hiding = True
        self.anim.setDuration(350)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def _after_anim(self):
        if self._hiding:
            self.hide()


class DropLineEdit(QLineEdit):
    """Path box that accepts a folder (or a file -> its folder) dropped onto it."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            p = Path(urls[0].toLocalFile())
            self.setText(str(p if p.is_dir() else p.parent))


def build_arrows(color):
    """Draw small up/down arrows to temp PNGs so the stylesheet can use them. -> (up_url, down_url, check_url)"""
    d = Path(tempfile.gettempdir()) / "sfm_arrows"
    d.mkdir(exist_ok=True)
    urls = []
    for name in ("up", "down"):
        pm = QPixmap(10, 6)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        pts = [QPointF(0.5, 5.5), QPointF(9.5, 5.5), QPointF(5, 0.5)] if name == "up" else \
              [QPointF(0.5, 0.5), QPointF(9.5, 0.5), QPointF(5, 5.5)]
        p.drawPolygon(QPolygonF(pts))
        p.end()
        f = d / f"{name}_{color.lstrip('#')}.png"
        pm.save(str(f))
        urls.append(f.as_posix())
    pm = QPixmap(14, 14)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("white"), 2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(2.5, 7.5), QPointF(5.5, 10.5), QPointF(11.5, 3.5)]))
    p.end()
    f = d / "check.png"
    pm.save(str(f))
    urls.append(f.as_posix())
    return urls[0], urls[1], urls[2]


def make_app_pixmap(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#7c5cff"))
    g.setColorAt(1, QColor("#22d3ee"))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), size * 0.22, size * 0.22)
    c, r = size / 2, size * 0.27
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([QPointF(c, c - r), QPointF(c + r, c), QPointF(c, c + r), QPointF(c - r, c)]))
    p.setBrush(QColor("#0b1020"))
    k = r * 0.45
    p.drawPolygon(QPolygonF([QPointF(c, c - k), QPointF(c + k, c), QPointF(c, c + k), QPointF(c - k, c)]))
    p.end()
    return pm
