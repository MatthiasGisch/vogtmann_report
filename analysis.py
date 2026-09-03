# -*- coding: utf-8 -*-
"""
Auswertungslogik des Analyseberichts (Konzept Teil B).

Bewusst frei von Datenbank- und PDF-Code: hier steckt nur Rechnung. Damit ist
dieser Teil unverändert übernehmbar, sobald die echten InfluxDB-Daten im
gleichen Bin-Format ankommen.

Konsistenzregel für die Toleranzbänder
--------------------------------------
Für jede Kennzahl wird EIN Wochenwert mit einer festen Statistik gebildet, und
der Referenzwert ist der Median genau dieses Wochenwerts über die vier
Referenzwochen. Beide Seiten des Vergleichs sind damit dieselbe Größe.

  Typ A (Niveau)     Wochenwert = laufzeitgewichteter Mittelwert bzw. Perzentil
                     Band       = +/- 10 % / 15 % vom Referenzmedian
  Typ B (Differenz)  Wochenwert = p95 der Bin-Werte innerhalb der Woche
                     Band       = Referenzmedian + 10 % / 15 %, einseitig,
                                  mindestens aber MIN_BAND_K
"""
import numpy as np
import pandas as pd

from config import (BANK1_TEMPS, BANK2_TEMPS, FLAP_TEMPS, BIN_MINUTES,
                    SEGMENT_GAP_MINUTES, EVENT_MERGE_MINUTES, MIN_VALID_SECONDS,
                    WARMUP_LIMIT_C, TOL_GREEN, TOL_YELLOW, MIN_BAND_K,
                    MIN_ABS_BAND, REF_MIN_OPERATING_HOURS, RANGE_LIMITS, STATUS,
                    SENSOR_LABELS, RED_FACTOR, PRESSURES, MIN_RATE_HOURS)


# ----------------------------------------------------------- Grundgerüst ----

def segments(df):
    """Einsätze: zusammenhängende Blöcke, getrennt durch Lücken > 5 min."""
    if df.empty:
        return []
    gaps = df.index.to_series().diff() > pd.Timedelta(minutes=BIN_MINUTES + SEGMENT_GAP_MINUTES)
    group = gaps.cumsum()
    out = []
    for _, g in df.groupby(group):
        out.append({
            "start": g.index[0],
            "end": g.index[-1] + pd.Timedelta(minutes=BIN_MINUTES),
            "seconds": int(g["samples"].sum()),
        })
    return out


def valid(df):
    """Bins mit ausreichender Laufzeit."""
    return df[df["samples"] >= MIN_VALID_SECONDS]


def warm(df, excluded=()):
    """Bins ohne Kaltstartphase (beide Bänke über der Warmlaufgrenze)."""
    b1 = _bank_mean(df, BANK1_TEMPS, excluded)
    b2 = _bank_mean(df, BANK2_TEMPS, excluded)
    return df[(b1 > WARMUP_LIMIT_C) & (b2 > WARMUP_LIMIT_C)]


def _cols(sensors, excluded, suffix="_mean"):
    return [f"{s}{suffix}" for s in sensors if s not in excluded]


def _bank_mean(df, sensors, excluded=()):
    return df[_cols(sensors, excluded)].mean(axis=1)


def _flap_delta(w, excluded):
    """Stauklappendifferenz - nur wenn beide Messstellen gültig sind."""
    a, b = FLAP_TEMPS
    if a in excluded or b in excluded:
        return float("nan")
    return float((w[f"{a}_mean"] - w[f"{b}_mean"]).abs().quantile(0.95))


def _wmean(series, weights):
    w = weights.reindex(series.index).astype(float)
    if w.sum() == 0:
        return float("nan")
    return float(np.average(series.astype(float), weights=w))


# ------------------------------------------------------------ Kennzahlen ----

def weekly_kpis(df, threshold, excluded=()):
    """Alle Wochenkennzahlen aus einem Bin-DataFrame einer Woche."""
    v = valid(df)
    w = warm(v, excluded)
    hours = float(df["samples"].sum()) / 3600.0
    segs = segments(df)

    if w.empty:
        return {"operating_hours": hours, "segments": len(segs), "empty": True}

    b1 = _bank_mean(w, BANK1_TEMPS, excluded)
    b2 = _bank_mean(w, BANK2_TEMPS, excluded)
    delta = (b1 - b2).abs()

    sp1 = w[_cols(BANK1_TEMPS, excluded)].max(axis=1) - w[_cols(BANK1_TEMPS, excluded)].min(axis=1)
    sp2 = w[_cols(BANK2_TEMPS, excluded)].max(axis=1) - w[_cols(BANK2_TEMPS, excluded)].min(axis=1)

    press = pd.concat([w["abgas_1_value_max"], w["abgas_2_value_max"]])
    ev = events(df, threshold, excluded)
    sw = w["samples"]

    return {
        "empty": False,
        "operating_hours": hours,
        "segments": len(segs),
        # Typ A
        "bank1_mean": _wmean(b1, sw),
        "bank2_mean": _wmean(b2, sw),
        "temp_max": float(w[_cols(BANK1_TEMPS + BANK2_TEMPS, excluded, "_max")].max().max()),
        "press_p50": float(press.quantile(0.50)),
        "press_p95": float(press.quantile(0.95)),
        # Unter MIN_RATE_HOURS ist die Rate statistisch wertlos: eine
        # Urlaubswoche mit zwei Betriebsstunden und einer Druckspitze ergaebe
        # sonst 5 Vorfaelle je 10 Stunden und damit einen Fehlalarm.
        "event_rate": (len(ev) / (hours / 10.0)
                       if hours >= MIN_RATE_HOURS else float("nan")),
        # Typ B
        "bank_delta_p95": float(delta.quantile(0.95)),
        "spread1_p95": float(sp1.quantile(0.95)),
        "spread2_p95": float(sp2.quantile(0.95)),
        "flap_delta_p95": _flap_delta(w, excluded),
        # Rohserien für die Plots
        "_bank1_series": b1,
        "_bank2_series": b2,
        "_delta_series": (b1 - b2),
        "_events": ev,
    }


# --------------------------------------------------------------- Vorfälle ---

SEVERITY = ["leicht", "deutlich", "stark"]


def events(df, threshold, excluded=()):
    """Zusammenhängende Überschreitungen als einzelne Vorfälle."""
    v = valid(df)
    if v.empty:
        return []
    # Bins mit unplausiblem Druck ausschliessen: ein defekter Sensor mit
    # Dauervollausschlag wuerde sonst als "starker Vorfall" in der Tabelle des
    # Kundenberichts landen. Der Defekt wird separat als Datenqualitaetsbefund
    # gemeldet.
    plo, phi = RANGE_LIMITS["pressure"]
    plausible = ((v["abgas_1_value_max"].between(plo, phi)) &
                 (v["abgas_2_value_max"].between(plo, phi)))
    v = v[plausible]
    if v.empty:
        return []
    over = (v["abgas_1_value_max"] > threshold) | (v["abgas_2_value_max"] > threshold)
    hits = v[over]
    if hits.empty:
        return []

    merged, cur = [], None
    for ts, row in hits.iterrows():
        if cur is None:
            cur = {"start": ts, "end": ts, "rows": [row]}
        elif ts - cur["end"] <= pd.Timedelta(minutes=EVENT_MERGE_MINUTES):
            cur["end"] = ts
            cur["rows"].append(row)
        else:
            merged.append(cur)
            cur = {"start": ts, "end": ts, "rows": [row]}
    merged.append(cur)

    out = []
    for m in merged:
        block = pd.DataFrame(m["rows"])
        p1, p2 = block["abgas_1_value_max"].max(), block["abgas_2_value_max"].max()
        peak, side = (p1, 1) if p1 >= p2 else (p2, 2)
        dur = (m["end"] - m["start"]).total_seconds() / 60.0 + BIN_MINUTES
        pct = (peak / threshold - 1.0) * 100.0
        # Staerke aus Dauer und Hoehe der Ueberschreitung (Konzept 7.)
        score = int(dur >= 20) + int(pct >= 10) + int(dur >= 45) + int(pct >= 20)
        bank = BANK1_TEMPS if side == 1 else BANK2_TEMPS
        ctx = block[_cols(bank, excluded)]
        out.append({
            "start": m["start"], "end": m["end"] + pd.Timedelta(minutes=BIN_MINUTES),
            "duration_min": dur, "peak": float(peak), "side": side,
            "over_pct": float(pct),
            "temp_mean": float(ctx.mean(axis=1).mean()),
            "temp_max": float(ctx.max(axis=1).max()),
            "severity": SEVERITY[min(int(score) // 2, 2)],
        })
    return out


# ------------------------------------------------------------ Referenzen ----

TYPE_A = ["bank1_mean", "bank2_mean", "press_p50", "press_p95", "event_rate"]
TYPE_B = ["bank_delta_p95", "spread1_p95", "spread2_p95", "flap_delta_p95"]

# Kennzahlen, bei denen nur eine Ueberschreitung nach oben relevant ist
ONE_SIDED = set(TYPE_B) | {"event_rate"}


def reference(week_kpis):
    """Median jeder Kennzahl über die Referenzwochen."""
    hours = sum(k["operating_hours"] for k in week_kpis)
    ref = {"_operating_hours": hours,
           "_sufficient": hours >= REF_MIN_OPERATING_HOURS,
           "_weeks": len(week_kpis)}
    for key in TYPE_A + TYPE_B:
        vals = [k[key] for k in week_kpis if not k.get("empty") and key in k]
        # nanmedian: ein einzelner Ausfall in einer Referenzwoche darf die
        # Kennzahl nicht auf Dauer stummschalten (NaN-Referenz = "nicht bewertet")
        vals = [v for v in vals if np.isfinite(v)]
        ref[key] = float(np.median(vals)) if vals else float("nan")
    return ref


def evaluate(key, value, ref):
    """Bewertet eine Kennzahl gegen ihr Toleranzband. -> (status, low, high)

    Prozentband und absolutes Mindestband werden kombiniert: es gilt immer das
    weitere von beiden. Ohne diese Regel würde ein Referenzwert nahe null jede
    normale Streuung zur Abweichung erklaeren.
    """
    r = ref.get(key, float("nan"))
    if not np.isfinite(r) or not np.isfinite(value) or not ref["_sufficient"]:
        return "keine", float("nan"), float("nan")

    floor = MIN_ABS_BAND.get(key, 0.0)
    w_green = max(abs(r) * TOL_GREEN, floor)
    w_yellow = max(abs(r) * TOL_YELLOW, floor * 1.5)

    # Ab dem Doppelten des Gelb-Bandes ist es keine Beobachtung mehr, sondern
    # ein Befund. Ohne diese Stufe waere "rot" unerreichbar und die vierte
    # Ampelstufe reine Dekoration.
    w_red = w_yellow * RED_FACTOR

    if key in ONE_SIDED:
        hi_g, hi_y, hi_r, lo_g = r + w_green, r + w_yellow, r + w_red, 0.0
        if value <= hi_g:
            return "gruen", lo_g, hi_g
        if value <= hi_y:
            return "gelb", lo_g, hi_g
        return ("orange" if value <= hi_r else "rot"), lo_g, hi_g

    lo_g, hi_g = r - w_green, r + w_green
    if lo_g <= value <= hi_g:
        return "gruen", lo_g, hi_g
    if r - w_yellow <= value <= r + w_yellow:
        return "gelb", lo_g, hi_g
    if r - w_red <= value <= r + w_red:
        return "orange", lo_g, hi_g
    return "rot", lo_g, hi_g


# --------------------------------------------------------- Datenqualität ----

def data_quality(df):
    """Vier Prüfungen aus Konzept 10. Liefert Befunde und Ausschlussliste."""
    findings, excluded = [], []
    v = valid(df)
    if v.empty:
        return ([{"kind": "Ausfall", "text": "Keine gültigen Messdaten im Zeitraum."}],
                [], 0.0)

    # Erwartete Bins = Bins, die innerhalb der Einsatzfenster liegen müssten
    expected = sum(max((s["end"] - s["start"]).total_seconds() / (BIN_MINUTES * 60), 1)
                   for s in segments(df))
    coverage = min(len(v) / expected, 1.0) if expected else 0.0

    for s in BANK1_TEMPS + BANK2_TEMPS + FLAP_TEMPS:
        side, spot = SENSOR_LABELS[s]
        mean = v[f"{s}_mean"]

        # Abriss: die Messstelle liefert gar nichts, waehrend die anderen messen.
        # Ohne diese Pruefung faellt ein Totalausfall durch: STDDEV ist dann NaN
        # (kein Festhaenger), Min/Max sind NaN (keine Bereichsverletzung), und
        # der Bankmittelwert rechnet stillschweigend mit den restlichen Sensoren
        # weiter - der Bericht meldet gruen und "Messdaten vollstaendig".
        good = int(mean.notna().sum())
        if good == 0:
            excluded.append(s)
            findings.append({
                "kind": "Abriss",
                "text": f"{side}, {spot}: hat im gesamten Zeitraum keine Werte "
                        f"geliefert. Diese Messstelle wurde aus der Auswertung "
                        f"ausgeschlossen.",
            })
            continue
        if good < len(v) * 0.5:
            excluded.append(s)
            findings.append({
                "kind": "Abriss",
                "text": f"{side}, {spot}: nur {good} von {len(v)} Messpunkten "
                        f"vorhanden. Diese Messstelle wurde aus der Auswertung "
                        f"ausgeschlossen.",
            })
            continue

        # Nur innerhalb eines Einsatzes zaehlen: sonst werden drei ruhige Bins
        # am Montag und drei am Mittwoch zu "30 Minuten unveraendert"
        # zusammengezaehlt und ein gesunder Sensor faelschlich ausgeschlossen.
        std = v[f"{s}_std"]
        gap = v.index.to_series().diff() > pd.Timedelta(minutes=BIN_MINUTES * 1.5)
        block = gap.cumsum()
        stuck = (std < 0.05).astype(int)
        grp = (stuck != stuck.shift()).cumsum().astype(str) + "_" + block.astype(str)
        run = stuck.groupby(grp).transform("size") * stuck
        if run.max() >= 6:                       # >= 30 Minuten konstant
            excluded.append(s)
            findings.append({
                "kind": "Festhänger",
                "text": f"{side}, {spot}: über {int(run.max()) * BIN_MINUTES} Minuten "
                        f"unveränderter Wert bei laufendem Motor. Diese Messstelle "
                        f"wurde aus der Auswertung ausgeschlossen; die übrigen Werte "
                        f"sind davon nicht betroffen.",
            })
            continue

        lo, hi = RANGE_LIMITS["temp"]
        bad = int(((v[f"{s}_max"] > hi) | (v[f"{s}_min"] < lo)).sum())
        if bad:
            findings.append({"kind": "Bereich",
                             "text": f"{side}, {spot}: {bad} Messwerte außerhalb des "
                                     f"plausiblen Bereichs."})

    # Drucksensoren: bisher ungeprueft. Ein defekter Sensor (Dauervollausschlag)
    # erzeugt sonst einen "starken Vorfall" in der Kundentabelle.
    plo, phi = RANGE_LIMITS["pressure"]
    for pcol, label in zip(PRESSURES, ("Bank 1", "Bank 2")):
        bad = int(((v[f"{pcol}_max"] > phi) | (v[f"{pcol}_mean"] < plo)).sum())
        if bad:
            findings.append({
                "kind": "Bereich-Druck",
                "text": f"Abgasgegendruck {label}: {bad} Messwerte außerhalb des "
                        f"plausiblen Bereichs ({plo:.0f}–{phi:.0f} mbar). "
                        f"Auffälligkeiten aus diesem Zeitraum sind nicht belastbar.",
            })

    return findings, excluded, coverage


# ------------------------------------------------------------- Gesamtampel --

METRIC_LABELS = {
    "bank1_mean":     ("Temperaturniveau Bank 1", "°C", 0),
    "bank2_mean":     ("Temperaturniveau Bank 2", "°C", 0),
    "bank_delta_p95": ("Unterschied zwischen den Bänken", "K", 1),
    "spread1_p95":    ("Abweichung innerhalb Bank 1", "K", 1),
    "spread2_p95":    ("Abweichung innerhalb Bank 2", "K", 1),
    "flap_delta_p95": ("Unterschied der Stauklappentemperaturen", "K", 1),
    "press_p50":      ("Abgasgegendruck typisch", "mbar", 0),
    "press_p95":      ("Abgasgegendruck hoch", "mbar", 0),
    "event_rate":     ("Auffälligkeiten je 10 Betriebsstunden", "", 1),
}

# Kennzahlen, die im Bericht als Tabelle erscheinen.
# "event_rate" wird weiterhin berechnet und fliesst in die Ampel ein, wird aber
# nicht mehr ausgewiesen - die absolute Zahl der Auffaelligkeiten steht bereits
# auf der Uebersichtsseite und ueber der Vorfallstabelle.
REPORT_METRICS = ["bank_delta_p95", "press_p95", "spread1_p95", "spread2_p95",
                  "bank1_mean", "bank2_mean", "flap_delta_p95", "press_p50"]

# Zusaetzlich bewertet, aber nicht ausgewiesen. Anzeige und Bewertung sind
# bewusst getrennt: verschwindet eine Kennzahl aus der Tabelle, soll sie nicht
# stillschweigend auch aus der Ampel fallen.
SILENT_METRICS = ["event_rate"]


def assess(kpi, ref, quality, coverage):
    """Ampel je Fahrzeug plus Begründungssatz aus Textbausteinen."""

    def judge(key):
        st, lo, hi = evaluate(key, kpi[key], ref)
        return {"key": key, "value": kpi[key], "ref": ref.get(key),
                "status": st, "low": lo, "high": hi,
                "label": METRIC_LABELS[key][0],
                "unit": METRIC_LABELS[key][1],
                "digits": METRIC_LABELS[key][2]}

    rows = [judge(k) for k in REPORT_METRICS if k in kpi]      # im Bericht sichtbar
    silent = [judge(k) for k in SILENT_METRICS if k in kpi]    # nur bewertet

    worst = "gruen"
    bewertbar = [r for r in rows + silent if r["status"] in STATUS]
    if not ref["_sufficient"] or not bewertbar:
        # Keine einzige Kennzahl konnte gegen eine Referenz geprueft werden.
        # "unauffaellig" waere hier eine Aussage, die die Daten nicht hergeben.
        worst = "keine"
    else:
        for r in rows + silent:
            if r["status"] in STATUS and STATUS[r["status"]]["rank"] > STATUS[worst]["rank"]:
                worst = r["status"]
        if coverage < 0.90:
            worst = "orange" if STATUS[worst]["rank"] < 2 else worst
        # Jede ausgeschlossene Messstelle, nicht nur ein Festhaenger:
        # eine fehlende Messstelle macht die Bank-Kennzahlen unvollstaendig.
        if any(f["kind"] in ("Festhänger", "Abriss") for f in quality):
            worst = "orange" if STATUS[worst]["rank"] < 2 else worst
        # Ein unplausibler Drucksensor macht die Vorfallserkennung blind -
        # "unauffällig" hiesse dann nur, dass nichts gemessen werden konnte.
        if any(f["kind"] == "Bereich-Druck" for f in quality):
            worst = "orange" if STATUS[worst]["rank"] < 2 else worst
        elif any(f["kind"] == "Bereich" for f in quality):
            worst = "gelb" if STATUS[worst]["rank"] < 1 else worst

    # Begründungssatz
    if worst == "keine":
        if not ref["_sufficient"]:
            reason = ("Für dieses Fahrzeug liegen noch zu wenige Betriebsstunden "
                      "vor. Der Normalbereich wird derzeit aufgebaut, eine "
                      "Bewertung erfolgt ab der nächsten vollständigen "
                      "Referenzphase.")
        else:
            reason = ("In diesem Zeitraum konnte keine Kennzahl ausgewertet "
                      "werden. Mögliche Gründe: das Fahrzeug war nicht im "
                      "Einsatz, oder die Messdaten sind unvollständig.")
    else:
        off = [r for r in rows if r["status"] in ("gelb", "orange", "rot")]
        off.sort(key=lambda r: -STATUS[r["status"]]["rank"])
        parts = []
        for r in off[:2]:
            dev = (r["value"] / r["ref"] - 1.0) * 100.0 if r["ref"] else 0.0
            parts.append(f"{r['label']} {abs(dev):.0f} % "
                         f"{'über' if dev > 0 else 'unter'} dem Normalbereich")
        # nicht ausgewiesene Kennzahlen im Klartext, nie als nackte Zahl
        for r in silent:
            if r["status"] in ("gelb", "orange", "rot") and r["key"] == "event_rate":
                parts.append("mehr Auffälligkeiten je Betriebsstunde als üblich")
        if any(f["kind"] in ("Festhänger", "Abriss") for f in quality):
            parts.append("eine Messstelle liefert keine verwertbaren Werte")
        if any(f["kind"] == "Bereich-Druck" for f in quality):
            parts.append("die Druckmessung ist zeitweise unplausibel")
        if coverage < 0.90:
            parts.append(f"die Messdaten sind nur zu {coverage * 100:.0f} % vollständig")
        if parts:
            joined = ", ".join(parts)
            rest = len(off) - 2
            if rest > 0:
                tail = (f"; {rest} weitere Kennzahl liegt außerhalb des "
                        f"Normalbereichs (siehe Tabelle)." if rest == 1 else
                        f"; {rest} weitere Kennzahlen liegen außerhalb des "
                        f"Normalbereichs (siehe Tabelle).")
            else:
                tail = "; übrige Werte unauffällig."
            reason = joined[0].upper() + joined[1:] + tail
        else:
            reason = ("Alle Kennzahlen liegen im Normalbereich, die Messdaten sind "
                      "vollständig.")
    return rows, worst, reason
