"""Tiny translation layer. tr("English template", name=value) -> text in the active language."""
import re

lang = "en"
LANGS = {"en": "English", "th": "ไทย"}
TH = {}          # filled by i18n_th.py
PATTERNS = []    # (compiled regex, template) for progress text coming from core.py


def set_lang(code):
    global lang
    lang = code if code in LANGS else "en"
    if lang == "th" and not TH:
        from i18n_th import TH as table, PATTERNS as pats
        TH.update(table)
        PATTERNS.extend((re.compile(p), t) for p, t in pats)


def tr(text, **kw):
    out = text
    if lang == "th" and text:
        if text in TH:
            out = TH[text]
        else:
            for rx, tpl in PATTERNS:
                m = rx.match(text)
                if m:
                    out = m.expand(tpl)
                    break
            else:
                if "\n" in text:
                    out = "\n".join(tr(x) for x in text.split("\n"))
    if kw:
        try:
            out = out.format(**kw)
        except (KeyError, IndexError, ValueError):
            out = text.format(**kw)
    return out
