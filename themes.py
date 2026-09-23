"""Color themes and the global stylesheet.

One layout for every theme (rounded cards, outlined buttons, a slim icon sidebar); the themes only change colours.
"""

THEMES = {
    "Steam": dict(bg="#1b2838", sidebar="#171d25", panel="#16202d", panel2="#22364a", input="#101822",
                  alt="#192536", text="#dfe3e6", muted="#8f98a0", accent="#66c0f4", accent_btn="#2f7ab8",
                  accent2="#a4d007", green="#a4d007", amber="#e5b143", red="#e8585a", border="#2a3f55",
                  hover="#2a475e", navsel="#2a475e", primary_top="#75b022", primary_hover_top="#8ed629"),
    "Neon Violet": dict(bg="#0b1020", sidebar="#080d19", panel="#121a2e", panel2="#17223a", input="#0b1325",
                        alt="#101a30", text="#eef2ff", muted="#9aa8c7", accent="#a78bfa", accent_btn="#6d4ed8",
                        accent2="#22d3ee", green="#34d399", amber="#fbbf24", red="#fb7185", border="#273653",
                        hover="#202d4a", navsel="#241d46"),
    "Cyber Cyan": dict(bg="#06121a", sidebar="#040c12", panel="#0c1f2c", panel2="#10293a", input="#07161f",
                       alt="#0a1b27", text="#e6fbff", muted="#8fb4c2", accent="#22d3ee", accent_btn="#0891b2",
                       accent2="#a3e635", green="#34d399", amber="#fbbf24", red="#fb7185", border="#1b4458",
                       hover="#143549", navsel="#0e3445"),
    "Crimson Night": dict(bg="#140a0e", sidebar="#0e070a", panel="#22121a", panel2="#2b1722", input="#180c12",
                          alt="#1c0f16", text="#ffeef2", muted="#c9a0ad", accent="#fb7185", accent_btn="#be123c",
                          accent2="#fbbf24", green="#34d399", amber="#fbbf24", red="#f43f5e", border="#4a2433",
                          hover="#3a1d2a", navsel="#3b1522"),
    "Light": dict(bg="#f4f6fb", sidebar="#ffffff", panel="#ffffff", panel2="#eef1f8", input="#ffffff",
                  alt="#f6f8fc", text="#14182b", muted="#5b6585", accent="#6d4ed8", accent_btn="#6d4ed8",
                  accent2="#0891b2", green="#059669", amber="#d97706", red="#e11d48", border="#d5dbea",
                  hover="#eeeafb", navsel="#e4defa", primary_hover_top="#5a3fc0",
                  divider="#e4e8f3", track="#e4defa", accent_text="#4b32a8", warn_text="#b45309",
                  danger_text="#b4173f"),
}
THEME_ORDER = ("Steam", "Neon Violet", "Cyber Cyan", "Crimson Night", "Light")

for _t in THEMES.values():
    _t.setdefault("primary_top", _t["accent_btn"])
    _t.setdefault("primary_hover_top", _t["accent"])
    _t.setdefault("divider", _t["border"])         # thin lines between list rows
    _t.setdefault("track", _t["navsel"])           # empty part of a bar / progress ring
    _t.setdefault("accent_text", _t["accent"])     # accent-coloured text (selected chip, "will move")
    _t.setdefault("warn_text", _t["amber"])
    _t.setdefault("danger_text", _t["red"])

R_CARD, R_BTN, R_INPUT = 16, 10, 12                # corner radii (px)

name = "Light"
current = dict(THEMES[name])
font = {"family": "", "size": 10}
defaults = {"family": "Segoe UI", "size": 10}      # language-aware (Thai: Leelawadee UI, 11 pt)
BUNDLED = "Instrument Sans"        # shipped in fonts/ and loaded at startup (Latin only; Thai falls back to the stack below)
FALLBACKS = "'Leelawadee UI', 'Tahoma', 'Noto Sans Thai', 'Noto Looped Thai', 'Noto Sans', 'Segoe UI', sans-serif"


def set_font(family="", size=10):
    font["family"] = (family or "").strip()
    font["size"] = max(8, min(20, int(size)))


def font_stack():
    first = font["family"] or defaults["family"]
    return f"'{first}', '{BUNDLED}', {FALLBACKS}"


def set_theme(new_name):
    global name
    if new_name not in THEMES:
        new_name = "Light"
    name = new_name
    current.clear()
    current.update(THEMES[new_name])


def color(key):
    return current[key]


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{int(alpha * 255)})"


def qss(up_arrow="", down_arrow="", check=""):
    p = current
    fs = font["size"]
    rc, rb, ri = R_CARD, R_BTN, R_INPUT
    small, tiny = max(round(fs * 0.9), 8), max(round(fs * 0.82), 7)
    return f"""
    QWidget {{ color: {p['text']}; font-family: {font_stack()}; font-size: {fs}pt; }}
    QWidget#root, QDialog, QMainWindow, QMessageBox {{ background: {p['bg']}; }}
    QLabel {{ background: transparent; }}
    QLabel#muted {{ color: {p['muted']}; }}
    QLabel#note {{ color: {p['muted']}; font-size: {small}pt; }}
    QLabel#pageTitle {{ font-size: {fs * 1.9:.0f}pt; font-weight: 700; }}
    QLabel#pageDesc {{ color: {p['muted']}; font-size: {small}pt; }}
    QLabel#cardTitle {{ font-size: {fs * 1.05:.1f}pt; font-weight: 700; }}
    QLabel#cardHint {{ color: {p['muted']}; font-size: {tiny}pt; }}
    QLabel#fieldLabel {{ color: {p['muted']}; font-size: {tiny}pt; }}
    QLabel#statLabel {{ color: {p['muted']}; font-size: {tiny}pt; }}
    QLabel#statValue {{ font-size: {fs * 1.9:.0f}pt; font-weight: 700; }}
    QLabel#rowTitle {{ font-size: {fs}pt; }}
    QLabel#rowSub {{ color: {p['muted']}; font-size: {tiny}pt; }}
    QLabel#greeting {{ font-size: {fs * 1.9:.0f}pt; font-weight: 700; }}
    QLabel#scoreCaption {{ font-size: {fs}pt; font-weight: 600; }}
    QLabel#version {{ color: {p['muted']}; font-size: {tiny}pt; }}
    QFrame#sidebar {{ background: {p['sidebar']}; border: none; border-right: 1px solid {p['border']}; }}
    QFrame#sidebar QFrame#navline {{ background: {p['divider']}; border: none; }}
    QLabel#logo {{ background: {p['accent_btn']}; color: #ffffff; border-radius: 12px; font-weight: 700; font-size: {fs}pt; }}
    QPushButton#navbtn {{ background: transparent; color: {p['muted']}; border: none; border-radius: 12px; padding: 0px;
                         font-size: {fs * 1.1:.0f}pt; }}
    QPushButton#navbtn:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QPushButton#navbtn:checked {{ background: {p['accent_btn']}; color: #ffffff; }}
    QFrame#card, QFrame#panel {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {rc}px; }}
    QFrame#hline {{ background: {p['divider']}; border: none; }}
    QFrame#footline {{ background: {p['divider']}; border: none; }}
    QPushButton {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: {rb}px;
                  padding: 8px 18px; }}
    QPushButton:hover {{ background: {p['navsel']}; color: {p['accent_text']}; }}
    QPushButton:pressed {{ background: {rgba(p['accent'], 0.25)}; }}
    QPushButton:disabled {{ background: {p['panel2']}; color: {p['muted']}; border-color: {p['border']}; }}
    QPushButton#primary {{ background: {p['primary_top']}; color: #ffffff; border: 1px solid {p['primary_top']}; font-weight: 700; }}
    QPushButton#primary:hover {{ background: {p['primary_hover_top']}; border-color: {p['primary_hover_top']}; color: #ffffff; }}
    QPushButton#primary:disabled {{ background: {p['panel2']}; color: {p['muted']}; border-color: {p['border']}; }}
    QPushButton#danger {{ background: {p['panel']}; color: {p['danger_text']}; border: 1px solid {p['border']}; }}
    QPushButton#danger:hover {{ background: {rgba(p['red'], 0.12)}; color: {p['danger_text']}; }}
    QPushButton#flat {{ background: transparent; color: {p['muted']}; border: none; padding: 6px 12px; }}
    QPushButton#flat:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QPushButton#chip {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: {rb}px;
                       padding: 7px 16px; }}
    QPushButton#chip:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QPushButton#chip:checked {{ background: {p['navsel']}; color: {p['accent_text']}; border: 1px solid {p['accent_btn']}; font-weight: 700; }}
    QLineEdit, QComboBox, QSpinBox, QTextEdit {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: {ri}px;
                       padding: 8px 14px; selection-background-color: {p['accent_btn']}; selection-color: #ffffff; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {{ border-color: {p['accent']}; }}
    QLineEdit#searchbar {{ border-radius: 22px; padding: 10px 22px; }}
    QFrame#pairfield {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: {ri}px; }}
    QFrame#pairfield QSpinBox {{ border: none; background: transparent; padding: 8px 0px 8px 4px; }}
    QSpinBox QLineEdit {{ padding: 0px; border: none; background: transparent; border-radius: 0px; }}
    QFrame#pairfield QSpinBox::up-button, QFrame#pairfield QSpinBox::down-button {{ width: 0px; }}
    QSpinBox[nobuttons="true"]::up-button, QSpinBox[nobuttons="true"]::down-button {{ width: 0px; }}
    QFrame#pairfield QLabel {{ color: {p['muted']}; }}
    QComboBox QAbstractItemView {{ background: {p['input']}; border: 1px solid {p['border']}; outline: none;
                       selection-background-color: {p['navsel']}; selection-color: {p['accent_text']}; }}
    QComboBox::drop-down {{ border: none; width: 30px; }}
    QComboBox::down-arrow {{ image: url({down_arrow}); width: 10px; height: 6px; }}
    QListWidget {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: {rb}px; outline: none; }}
    QListWidget::item {{ padding: 9px; border-radius: {rb}px; }}
    QListWidget::item:selected {{ background: {p['navsel']}; color: {p['accent_text']}; }}
    QTableWidget {{ background: {p['panel']}; alternate-background-color: {p['alt']}; gridline-color: {p['divider']};
                    border: 1px solid {p['border']}; border-radius: {rb}px; outline: none; }}
    QTableWidget[bare="true"] {{ background: transparent; border: none; border-radius: 0px; }}
    QTableWidget::item {{ padding: 0px 8px; border-bottom: 1px solid {p['divider']}; }}
    QTableWidget::item:selected {{ background: {p['navsel']}; color: {p['text']}; }}
    QTableWidget[bare="true"]::item:selected {{ background: transparent; color: {p['text']}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{ background: {p['panel2']}; color: {p['muted']}; padding: 10px 14px; border: none; font-weight: 700; }}
    QTableCornerButton::section {{ background: {p['panel2']}; border: none; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 5px; min-width: 28px; }}
    QScrollBar::handle:horizontal:hover {{ background: {p['accent']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {p['panel2']}; color: {p['text']}; border: 1px solid {p['border']}; padding: 4px; }}
    QMenu {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {rb}px; padding: 6px; }}
    QMenu::item {{ padding: 8px 22px 8px 14px; border-radius: 8px; background: transparent; }}
    QMenu::item:selected {{ background: {p['navsel']}; color: {p['accent_text']}; }}
    QCheckBox {{ spacing: 10px; background: transparent; }}
    QCheckBox::indicator, QTableView::indicator {{ width: 18px; height: 18px; border: 1px solid {p['muted']};
                       border-radius: 5px; background: {p['input']}; }}
    QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {p['accent']}; }}
    QCheckBox::indicator:checked, QTableView::indicator:checked {{ background: {p['accent_btn']};
                       border-color: {p['accent_btn']}; image: url({check}); }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
    QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; }}
    QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; }}
    QSpinBox::up-arrow {{ image: url({up_arrow}); width: 10px; height: 6px; }}
    QSpinBox::down-arrow {{ image: url({down_arrow}); width: 10px; height: 6px; }}
    """
