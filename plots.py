# -*- coding: utf-8 -*-
"""
Plotgestaltung nach Konzept Abschnitt 13.

Regeln, die hier verdrahtet sind:
  - helle Druckfläche, Farbe nur für Daten
  - geprüfte Serienfarben, Beschriftungstext immer in Tinte, nie in Serienfarbe
  - Vorfälle als schattierte Bänder hinter den Daten, nicht als harte Linien
  - beide Bänke identisch skaliert
  - nie zwei Y-Achsen

Betriebszeit-Achse
------------------
Der Motor läuft pro Woche nur in rund 15 kurzen Einsätzen. Auf einer echten
Kalenderachse wird daraus 15-mal ein senkrechter Strich und der Verlauf ist
nicht lesbar. Deshalb werden die Stillstandszeiten herausgenommen: die X-Achse
zeigt Betriebszeit, Tageswechsel sind beschriftet, Einsatzgrenzen als feine
Trennlinie markiert.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import (SERIES, BANK_COLOR, STATUS, INK_PRIMARY, INK_SECONDARY,
                    INK_MUTED, RULE, BAND_NORMAL, BIN_MINUTES, SENSOR_LABELS)

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "font.family": "DejaVu Sans",
    "font.size": 7.2,
    "axes.edgecolor": RULE,
    "axes.labelcolor": INK_SECONDARY,
    "text.color": INK_PRIMARY,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "xtick.labelsize": 6.2,
    "ytick.labelsize": 6.2,
    "axes.titlesize": 7.8,
    "legend.fontsize": 6.4,
    "lines.linewidth": 1.3,
})

CM = 1 / 2.54
DAY_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


class OpAxis:
    """Bildet Zeitstempel auf Betriebszeit-Positionen ab."""

    def __init__(self, index):
        self.index = pd.DatetimeIndex(index)
        self.pos = pd.Series(np.arange(len(self.index)), index=self.index)
        gaps = self.index.to_series().diff() > pd.Timedelta(minutes=BIN_MINUTES * 1.5)
        self.breaks = np.flatnonzero(gaps.values)
        days = self.index.normalize()
        first = ~pd.Series(days).duplicated().values
        self.all_ticks = np.flatnonzero(first)
        self.day_labels = [(DAY_DE[d.weekday()], d.strftime("%d.%m."))
                           for d in self.index[self.all_ticks]]

    def _thin(self, long_labels):
        """Tageswechsel ausduennen, bis sich die Beschriftungen nicht mehr
        ueberlappen. Ein Tag mit wenig Betriebszeit belegt auf der
        Betriebszeit-Achse kaum Platz - ohne Ausduennung ueberschreiben sich
        die Labels benachbarter Tage."""
        # geschaetzter Platzbedarf eines Labels als Anteil der Achsenbreite
        share = 0.105 if long_labels else 0.048
        min_gap = max(len(self.index) * share, 1)
        keep = []
        for t in self.all_ticks:
            if not keep or t - keep[-1] >= min_gap:
                keep.append(t)
        idx = [list(self.all_ticks).index(t) for t in keep]
        labels = [f"{self.day_labels[i][0]} {self.day_labels[i][1]}" if long_labels
                  else self.day_labels[i][0] for i in idx]
        return np.array(keep, dtype=int), labels

    def y(self, series):
        """Serie auf die Achse ziehen; fehlende Bins werden zu NaN."""
        return series.reindex(self.index).values.astype(float)

    def span(self, t0, t1):
        lo = self.pos.index.searchsorted(t0)
        hi = self.pos.index.searchsorted(t1)
        return lo, max(hi, lo + 1)

    def apply(self, ax, long_labels=True):
        ticks, labels = self._thin(long_labels)
        ax.set_xlim(-2, len(self.index) + 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels)
        for lab in ax.get_xticklabels():
            lab.set_ha("left")
        for b in self.breaks:
            ax.axvline(b - 0.5, color=RULE, linewidth=0.5, zorder=1)


def _frame(ax, title=None, ylabel=None):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(RULE)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(axis="y", color=RULE, linewidth=0.5, alpha=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    if ylabel:
        ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, loc="left", color=INK_PRIMARY, pad=14,
                     fontweight="bold")


def _legend(ax, ncol=3):
    """Legende ueber der Achse - bei voller Breite passt sie immer in eine Zeile."""
    leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=ncol,
                    frameon=False, handlelength=1.5, columnspacing=1.1,
                    borderpad=0, handletextpad=0.4)
    for t in leg.get_texts():
        t.set_color(INK_SECONDARY)
    return leg


def _note(ax, s):
    ax.annotate(s, xy=(1, 1), xycoords="axes fraction", xytext=(0, 4),
                textcoords="offset points", ha="right", va="bottom",
                color=INK_MUTED, fontsize=6.0)


# ------------------------------------------------------------------ Plots ---

def bank_temps(df, sensors, axis, ylim, excluded, path, title,
               width_cm=16.6, height_cm=4.5):
    fig, ax = plt.subplots(figsize=(width_cm * CM, height_cm * CM))
    for i, s in enumerate(sensors):
        y = axis.y(df[f"{s}_mean"])
        short = SENSOR_LABELS[s][1]
        if s in excluded:
            ax.plot(y, color=INK_MUTED, linestyle=(0, (2, 2)), linewidth=1.0,
                    label=f"{short} ungültig", zorder=2)
        else:
            ax.plot(y, color=SERIES[i], label=short, zorder=3)
    ax.set_ylim(*ylim)
    _frame(ax, title, "°C")
    axis.apply(ax)
    _legend(ax)
    fig.tight_layout(pad=0.4)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def pressure(df, axis, threshold, evs, path, width_cm=16.6, height_cm=4.5):
    fig, ax = plt.subplots(figsize=(width_cm * CM, height_cm * CM))

    # Vorfälle als blasses Band hinter den Daten, bewusst in einem Ton, der
    # mit keiner der beiden Serienfarben verwechselbar ist
    for e in evs:
        lo, hi = axis.span(e["start"], e["end"])
        ax.axvspan(lo - 0.5, hi - 0.5, color=STATUS["rot"]["color"], alpha=0.13,
                   linewidth=0, zorder=1)

    for i, col in enumerate(("abgas_1_value_max", "abgas_2_value_max")):
        ax.plot(axis.y(df[col]), color=BANK_COLOR[i + 1],
                label=f"Bank {i + 1}", zorder=3, linewidth=1.1)

    ax.axhline(threshold, color=INK_SECONDARY, linestyle=(0, (4, 3)),
               linewidth=1.0, zorder=4)
    ax.annotate(f"Grenzwert {threshold}", xy=(len(axis.index), threshold),
                xytext=(-2, 3), textcoords="offset points", ha="right",
                va="bottom", color=INK_SECONDARY, fontsize=6.0,
                bbox=dict(facecolor="white", edgecolor="none", pad=1.0))

    _frame(ax, "Abgasgegendruck", "mbar")
    axis.apply(ax)
    _legend(ax, ncol=2)
    fig.tight_layout(pad=0.4)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def side_delta(delta, axis, band_high, path, width_cm=16.6, height_cm=4.5):
    """Eine Kurve gegen die Nulllinie, Toleranzband grau (Konzept 13.4)."""
    fig, ax = plt.subplots(figsize=(width_cm * CM, height_cm * CM))
    y = axis.y(delta)

    if np.isfinite(band_high):
        ax.axhspan(-band_high, band_high, color=BAND_NORMAL, zorder=1, linewidth=0)

    x = np.arange(len(y))
    ax.fill_between(x, 0, y, where=y > 0, color=BANK_COLOR[1], alpha=0.45,
                    linewidth=0, zorder=2, interpolate=True)
    ax.fill_between(x, 0, y, where=y < 0, color=BANK_COLOR[2], alpha=0.45,
                    linewidth=0, zorder=2, interpolate=True)
    ax.plot(x, y, color=INK_SECONDARY, linewidth=0.8, zorder=3)
    ax.axhline(0, color=INK_SECONDARY, linewidth=0.8, zorder=4)

    _frame(ax, "Unterschied zwischen den Bänken", "K")
    axis.apply(ax)
    _note(ax, "grau = Normalbereich · über 0 = Bank 1 wärmer")
    fig.tight_layout(pad=0.4)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def daily_hours(cur, prev, path, width_cm=8.1, height_cm=5.0):
    fig, ax = plt.subplots(figsize=(width_cm * CM, height_cm * CM))
    x = np.arange(7)
    ax.bar(x - 0.19, prev, width=0.36, color="#dcdcd8", label="Vorwoche", zorder=2)
    ax.bar(x + 0.19, cur, width=0.36, color=BANK_COLOR[1], label="Diese Woche",
           zorder=3)
    ax.set_xticks(x)
    ax.set_xticklabels(DAY_DE)
    _frame(ax, "Betriebszeit je Tag", "Stunden")
    _legend(ax, ncol=2)
    fig.tight_layout(pad=0.4)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def event_timeline(evs, segs, start, end, path):
    """Kalenderzeitstrahl: wann lief der Motor, wann gab es Auffälligkeiten."""
    fig, ax = plt.subplots(figsize=(16.6 * CM, 2.0 * CM))
    span = (end - start).total_seconds()
    to_x = lambda t: (t - start).total_seconds() / span

    for s in segs:
        ax.axvspan(to_x(s["start"]), to_x(s["end"]), ymin=0.34, ymax=0.66,
                   color="#c9d9f2", linewidth=0)
    for e in evs:
        col = {"leicht": STATUS["gelb"], "deutlich": STATUS["orange"],
               "stark": STATUS["rot"]}[e["severity"]]["color"]
        a, b = to_x(e["start"]), to_x(e["end"])
        b = max(b, a + 0.0035)                      # Mindestbreite für die Sichtbarkeit
        ax.axvspan(a, b, ymin=0.12, ymax=0.88, color=col, linewidth=0)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(False)
    ax.tick_params(length=0)

    days = [start] + [d for d in pd.date_range(start.normalize(), end, freq="D")
                      if start < d <= end]
    ticks = [to_x(d) for d in days]
    labels = [f"{DAY_DE[d.weekday()]} {d.strftime('%d.%m.')}" for d in days]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    for lab in ax.get_xticklabels():
        lab.set_ha("left")
    fig.tight_layout(pad=0.3)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fleet_hours(names, cur, prev, path):
    fig, ax = plt.subplots(figsize=(16.6 * CM, 3.4 * CM))
    y = np.arange(len(names))
    ax.barh(y + 0.18, prev, height=0.34, color="#dcdcd8", label="Vorwoche", zorder=2)
    ax.barh(y - 0.18, cur, height=0.34, color=BANK_COLOR[1], label="Diese Woche",
            zorder=3)
    for i, v in enumerate(cur):
        ax.annotate(f"{v:.1f} h".replace(".", ","), xy=(v, i - 0.18),
                    xytext=(4, 0), textcoords="offset points", va="center",
                    color=INK_SECONDARY, fontsize=6.6)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlabel("Betriebsstunden")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(RULE)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(axis="x", color=RULE, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.set_xlim(0, max(max(cur), max(prev)) * 1.25)
    leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2,
                    frameon=False, handlelength=1.4)
    for t in leg.get_texts():
        t.set_color(INK_SECONDARY)
    fig.tight_layout(pad=0.4)
    fig.savefig(path, dpi=200)
    plt.close(fig)
