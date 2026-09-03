# -*- coding: utf-8 -*-
"""
PDF-Aufbau nach Konzept Teil C.

Seitenarchitektur bei n Fahrzeugen: 1 Überblick + 2n Fahrzeugseiten
(plus eine Hinweisseite, falls ein Fahrzeug nicht auswertbar war)
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas

from config import (STATUS, INK_PRIMARY, INK_SECONDARY, INK_MUTED, RULE,
                    SURFACE_SOFT, BAND_NORMAL)

PW, PH = A4
M = 2 * cm
CONTENT_W = PW - 2 * M

F_REG, F_BOLD = "Helvetica", "Helvetica-Bold"


# --------------------------------------------------------------- Helfer -----

def de(x, nd=1):
    """Deutsche Zahlendarstellung."""
    if x is None or x != x:
        return "–"
    s = f"{x:,.{nd}f}"
    return s.replace(",", " ").replace(".", ",").replace(" ", ".")


def top(y):
    """Koordinate von oben statt von unten."""
    return PH - y


def text(c, x, y, s, size=9, font=F_REG, color=INK_PRIMARY, align="l"):
    c.setFont(font, size)
    c.setFillColor(color)
    if align == "r":
        c.drawRightString(x, top(y), s)
    elif align == "c":
        c.drawCentredString(x, top(y), s)
    else:
        c.drawString(x, top(y), s)


def wrap_lines(c, s, width, size, font=F_REG):
    """Zeilen, die der Umbruch erzeugen wuerde - fuer die Hoehenberechnung."""
    lines, line = [], []
    for w in s.split():
        if c.stringWidth(" ".join(line + [w]), font, size) <= width:
            line.append(w)
        else:
            lines.append(" ".join(line)); line = [w]
    if line:
        lines.append(" ".join(line))
    return lines or [""]


def wrap(c, x, y, s, width, size=9, font=F_REG, color=INK_SECONDARY, leading=12):
    """Einfacher Blocksatz-Umbruch."""
    c.setFont(font, size)
    c.setFillColor(color)
    words, line, cur = s.split(), [], y
    for w in words:
        trial = " ".join(line + [w])
        if c.stringWidth(trial, font, size) <= width:
            line.append(w)
        else:
            c.drawString(x, top(cur), " ".join(line))
            cur += leading
            line = [w]
    if line:
        c.drawString(x, top(cur), " ".join(line))
        cur += leading
    return cur


def rule(c, y, x0=M, x1=None, color=RULE, w=0.6):
    c.setStrokeColor(color)
    c.setLineWidth(w)
    c.line(x0, top(y), x1 or (PW - M), top(y))


def box(c, x, y, w, h, fill=SURFACE_SOFT, stroke=None, radius=4):
    c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.6)
        c.roundRect(x, top(y + h), w, h, radius, stroke=1, fill=1)
    else:
        c.roundRect(x, top(y + h), w, h, radius, stroke=0, fill=1)


def status_dot(c, x, y, key, r=4.2):
    c.setFillColor(STATUS[key]["color"] if key in STATUS else INK_MUTED)
    c.circle(x + r, top(y + r), r, stroke=0, fill=1)


def page_frame(c, ctx, page_no, total, title, subtitle=None):
    text(c, M, 42, title, 15, F_BOLD)
    if subtitle:
        text(c, M, 58, subtitle, 9, F_REG, INK_SECONDARY)
    text(c, PW - M, 42, ctx["period"], 8, F_REG, INK_MUTED, align="r")
    rule(c, 68)
    rule(c, PH - 46)
    text(c, M, PH - 36, ctx["footer_left"], 7.2, F_REG, INK_MUTED)
    text(c, PW - M, PH - 36, f"Seite {page_no} von {total}", 7.2, F_REG,
         INK_MUTED, align="r")


# ------------------------------------------------------------ KPI-Kachel ----

def kpi_tile(c, x, y, w, h, row):
    """Wert · Normalbereich · Bewertung (Konzept 12.).

    Der Normalbereich steht als graues Band direkt unter dem Wert - das ist der
    Teil, der den Bericht ohne Fachwissen lesbar macht.
    """
    box(c, x, y, w, h, fill="#ffffff", stroke=RULE)
    pad = 8
    inner = w - 2 * pad

    # Bezeichnung, bis zu zwei Zeilen
    c.setFont(F_REG, 6.5)
    words, lines, line = row["label"].split(), [], []
    for wd in words:
        if c.stringWidth(" ".join(line + [wd]), F_REG, 6.5) <= inner:
            line.append(wd)
        else:
            lines.append(" ".join(line)); line = [wd]
    if line:
        lines.append(" ".join(line))
    if len(lines) > 2:
        lines = lines[:2]
        while c.stringWidth(lines[1] + " ...", F_REG, 6.5) > inner and " " in lines[1]:
            lines[1] = lines[1].rsplit(" ", 1)[0]
        lines[1] += " ..."
    for i, ln in enumerate(lines[:2]):
        text(c, x + pad, y + 13 + i * 8, ln, 6.5, F_REG, INK_MUTED)

    val = de(row["value"], row["digits"])
    text(c, x + pad, y + 42, val, 15, F_BOLD, INK_PRIMARY)
    vw = c.stringWidth(val, F_BOLD, 15)
    if row["unit"]:
        text(c, x + pad + vw + 3, y + 42, row["unit"], 8, F_REG, INK_SECONDARY)

    bx, by, bh = x + pad, y + 52, 5
    lo, hi = row["low"], row["high"]
    if lo == lo and hi == hi and hi > lo:
        span_lo = min(lo, row["value"]) - (hi - lo) * 0.45
        span_hi = max(hi, row["value"]) + (hi - lo) * 0.45
        rng = span_hi - span_lo or 1.0
        c.setFillColor("#f0f0ec")
        c.rect(bx, top(by + bh), inner, bh, stroke=0, fill=1)
        c.setFillColor(BAND_NORMAL)
        c.rect(bx + (lo - span_lo) / rng * inner, top(by + bh),
               (hi - lo) / rng * inner, bh, stroke=0, fill=1)
        mx = bx + (row["value"] - span_lo) / rng * inner
        c.setFillColor(STATUS.get(row["status"], {"color": INK_MUTED})["color"])
        c.rect(mx - 1.1, top(by + bh + 2), 2.2, bh + 4, stroke=0, fill=1)
        text(c, bx, y + 70, f"normal {de(lo, row['digits'])} bis {de(hi, row['digits'])}",
             6.2, F_REG, INK_MUTED)
    else:
        text(c, bx, y + 70, "Normalbereich wird aufgebaut", 6.2, F_REG, INK_MUTED)

    st = row["status"]
    label = {"gruen": "im Normalbereich", "gelb": "beobachten",
             "orange": "prüfen", "rot": "prüfen"}.get(st, "nicht bewertet")
    status_dot(c, x + pad, y + 76, st, r=3.2)
    text(c, x + pad + 11, y + 83, label, 7, F_REG, INK_SECONDARY)


# ---------------------------------------------------------------- Tabelle ---

def table(c, x, y, cols, rows, row_h=14, head_h=15, zebra=True):
    """cols: [(Titel, Breite, 'l'|'r')]  rows: Liste von Listen (Text, optional color)"""
    total_w = sum(w for _, w, _ in cols)
    c.setFillColor("#efefeb")
    c.rect(x, top(y + head_h), total_w, head_h, stroke=0, fill=1)
    cx = x
    for title, w, al in cols:
        tx = cx + w - 5 if al == "r" else cx + 5
        text(c, tx, y + head_h - 4.5, title, 6.8, F_BOLD, INK_SECONDARY,
             align="r" if al == "r" else "l")
        cx += w
    cur = y + head_h
    for i, r in enumerate(rows):
        if zebra and i % 2 == 1:
            c.setFillColor("#fafaf8")
            c.rect(x, top(cur + row_h), total_w, row_h, stroke=0, fill=1)
        cx = x
        for (title, w, al), cell in zip(cols, r):
            val, col = (cell if isinstance(cell, tuple) else (cell, INK_PRIMARY))
            tx = cx + w - 5 if al == "r" else cx + 5
            text(c, tx, cur + row_h - 4, str(val), 7.4, F_REG, col,
                 align="r" if al == "r" else "l")
            cx += w
        cur += row_h
    rule(c, cur, x, x + total_w)
    return cur


def new_canvas(path):
    c = rl_canvas.Canvas(path, pagesize=A4)
    c.setTitle("Wöchentlicher Analysebericht")
    return c
