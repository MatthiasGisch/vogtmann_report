# -*- coding: utf-8 -*-
"""
Musterbericht-Generator.

Seitenaufbau des PDF-Berichts. Die Datenquelle wird von aussen hereingereicht
(run_report.py), dieses Modul kennt weder InfluxDB noch die synthetischen Daten.

    build(raw, report_end, out_path)  ->  (results, ctx)
"""
import os
import shutil
import numpy as np
import pandas as pd

import analysis as A
import plots as P
import pdf as D
from config import (VEHICLES, BANK1_TEMPS, BANK2_TEMPS, STATUS,
                    INK_PRIMARY, INK_SECONDARY, INK_MUTED, RULE, SURFACE_SOFT,
                    BIN_MINUTES, TOL_GREEN, TOL_YELLOW, MIN_BAND_K,
                    REF_MIN_OPERATING_HOURS)
from pdf import M, PW, PH, CONTENT_W, de, text, wrap, rule, box, table, \
    status_dot, page_frame, kpi_tile, wrap_lines, F_REG, F_BOLD

# Hoehe der gestapelten Diagramme auf der Messwerteseite (cm).
# Vier Stueck plus Vorfallstabelle muessen auf eine A4-Seite passen.
PLOT_H_CM = 4.5

TMP = "/tmp/vogtmann_plots"
OUT = "/home/claude/vogtmann/Musterbericht_Wochenanalyse.pdf"
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# ------------------------------------------------------------ Auswertung ----

def _empty_like():
    """Leerer DataFrame mit dem erwarteten Schema und Zeitindex."""
    from config import FLAP_TEMPS, PRESSURES
    cols = ["samples"]
    for s in BANK1_TEMPS + BANK2_TEMPS + FLAP_TEMPS:
        cols += [f"{s}_mean", f"{s}_max", f"{s}_min", f"{s}_std"]
    for pcol in PRESSURES:
        cols += [f"{pcol}_mean", f"{pcol}_max"]
    return pd.DataFrame(columns=cols,
                        index=pd.DatetimeIndex([], name="ts")).astype(float)

def analyse(df, veh, report_start, report_end):
    # Ein Fahrzeug kann ganz fehlen (Abfrage gescheitert) oder einen leeren
    # DataFrame ohne Zeitindex liefern - beides darf nicht abstuerzen.
    if df is None or len(df) == 0 or not isinstance(df.index, pd.DatetimeIndex):
        df = _empty_like()
    week = df[(df.index >= report_start) & (df.index < report_end)]
    prev = df[(df.index >= report_start - pd.Timedelta(days=7)) & (df.index < report_start)]
    ref_raw = df[df.index < report_start - pd.Timedelta(days=7)]

    quality, excluded, coverage = A.data_quality(week)
    th = veh["pressure_threshold"]

    ref_weeks = []
    for w in range(4):
        a = report_start - pd.Timedelta(days=7 * (w + 2))
        b = a + pd.Timedelta(days=7)
        chunk = ref_raw[(ref_raw.index >= a) & (ref_raw.index < b)]
        if not chunk.empty:
            # Mit derselben Ausschlussliste wie die Berichtswoche, sonst wird
            # eine Kennzahl aus drei Sensoren gegen eine aus zwei verglichen.
            ref_weeks.append(A.weekly_kpis(chunk, th, excluded))
    ref = A.reference(ref_weeks)

    kpi = A.weekly_kpis(week, th, excluded)
    kpi_prev = A.weekly_kpis(prev, th) if not prev.empty else {"operating_hours": 0.0}
    rows, worst, reason = A.assess(kpi, ref, quality, coverage)

    hours = lambda d: (d.groupby(d.index.dayofweek)["samples"].sum() / 3600.0)
    cur_h = hours(week).reindex(range(7), fill_value=0.0).tolist()
    prev_h = hours(prev).reindex(range(7), fill_value=0.0).tolist() if not prev.empty else [0] * 7

    return {"veh": veh, "week": week, "kpi": kpi, "kpi_prev": kpi_prev,
            "ref": ref, "rows": rows, "status": worst, "reason": reason,
            "quality": quality, "excluded": excluded, "coverage": coverage,
            "segments": A.segments(week), "daily": cur_h, "daily_prev": prev_h,
            # Ein Fahrzeug kann eine Woche stehen. Dann gibt es nichts zu
            # zeichnen - der Bericht muss das aushalten und darf nicht abbrechen.
            "has_data": not kpi.get("empty", False) and len(A.valid(week)) > 5}


def make_plots(r, start, end):
    if not r["has_data"]:
        return None
    v = r["veh"]["car_id"]
    w = A.valid(r["week"])
    axis = P.OpAxis(w.index)

    # Y-Bereich aus den WARMEN Bins, nicht aus allen: jeder Einsatz startet beim
    # Warmlauf bei Umgebungstemperatur. Nimmt man die mit in die Skalierung, laeuft
    # die Achse von 0 bis 700 und der Betriebsbereich - in dem die drei Messstellen
    # auseinandergehalten werden sollen - wird auf ein Drittel der Plothoehe
    # gestaucht. Die Warmlauframpen laufen so unten aus dem Bild; darauf weist der
    # Anhang hin.
    cols = [f"{s}_mean" for s in BANK1_TEMPS + BANK2_TEMPS if s not in r["excluded"]]
    warm_bins = A.warm(w, r["excluded"])
    src = warm_bins if len(warm_bins) > 20 else w
    lo = float(src[cols].quantile(0.01).min())
    hi = float(src[cols].max().max())
    pad = (hi - lo) * 0.10
    ylim = (max(0.0, lo - pad), hi + pad)        # beide Bänke identisch skaliert

    p = lambda n: os.path.join(TMP, f"{v}_{n}.png")
    P.bank_temps(w, BANK1_TEMPS, axis, ylim, r["excluded"], p("b1"),
                 "Temperaturen Bank 1", height_cm=PLOT_H_CM)
    P.bank_temps(w, BANK2_TEMPS, axis, ylim, r["excluded"], p("b2"),
                 "Temperaturen Bank 2", height_cm=PLOT_H_CM)
    P.pressure(w, axis, r["veh"]["pressure_threshold"],
               r["kpi"].get("_events", []), p("press"), height_cm=PLOT_H_CM)

    band = next((x["high"] for x in r["rows"] if x["key"] == "bank_delta_p95"),
                float("nan"))
    P.side_delta(r["kpi"]["_delta_series"], axis, band, p("delta"),
                 height_cm=PLOT_H_CM)
    P.daily_hours(r["daily"], r["daily_prev"], p("hours"), height_cm=6.0)
    P.event_timeline(r["kpi"].get("_events", []), r["segments"], start, end, p("tl"))
    return p


def metric_table(c, y, r):
    cols = [("Kennzahl", 196, "l"), ("Messwert", 62, "r"), ("Einheit", 42, "l"),
            ("Normalbereich", 96, "r"), ("Abweichung", 60, "r"),
            ("Bewertung", 56, "l")]
    rows = []
    for x in r["rows"]:
        d = x["digits"]
        if x["ref"] and x["ref"] == x["ref"] and x["ref"] != 0:
            dev = (x["value"] / x["ref"] - 1.0) * 100.0
            sign = "" if abs(dev) < 0.5 else ("+" if dev > 0 else "-")
            dev_s = f"{sign}{de(abs(dev), 0)} %"
        else:
            dev_s = "-"
        band = (f"{de(x['low'], d)} bis {de(x['high'], d)}"
                if x["high"] == x["high"] else "wird aufgebaut")
        st = x["status"]
        label = {"gruen": "normal", "gelb": "beobachten",
                 "orange": "prüfen", "rot": "prüfen"}.get(st, "-")
        col = STATUS[st]["color"] if st in STATUS else INK_MUTED
        rows.append([x["label"], de(x["value"], d), x["unit"], band, dev_s,
                     (label, col)])
    return table(c, M, y, cols, rows)


# ------------------------------------------------------------- Seiten -------

def page_overview(c, results, ctx, page_no, total, chart):
    page_frame(c, ctx, page_no, total, "Wöchentlicher Analysebericht",
               "Fahrzeug- und Motorüberwachung · Übersicht")
    y = 88
    text(c, M, y, "Status der Fahrzeuge", 10.5, F_BOLD)
    y += 14

    for r in results:
        h = 62 + len(wrap_lines(c, r["reason"], CONTENT_W - 45, 7.8)) * 10
        box(c, M, y, CONTENT_W, h, fill="#ffffff", stroke=RULE)
        status_dot(c, M + 12, y + 14, r["status"], r=5.5)
        text(c, M + 30, y + 24, f"{r['veh']['name']}", 11.5, F_BOLD)
        text(c, M + 30 + c.stringWidth(r['veh']['name'], F_BOLD, 11.5) + 8, y + 24,
             r["veh"]["plate"], 8.5, F_REG, INK_MUTED)
        st = STATUS.get(r["status"])
        text(c, PW - M - 12, y + 24, st["label"] if st else "nicht bewertet",
             9, F_BOLD, st["color"] if st else INK_MUTED, align="r")

        stats = [("Betriebsstunden", f"{de(r['kpi']['operating_hours'], 1)} h"),
                 ("Einsätze", str(r["kpi"].get("segments", 0))),
                 ("Auffälligkeiten", str(len(r["kpi"].get("_events", []))))]
        sx = M + 30
        for label, val in stats:
            text(c, sx, y + 40, label, 6.6, F_REG, INK_MUTED)
            text(c, sx, y + 51, val, 9.5, F_BOLD)
            sx += 95
        wrap(c, M + 30, y + 66, r["reason"], CONTENT_W - 45, 7.8,
             color=INK_SECONDARY, leading=10)
        y += h + 10

    y += 6
    c.drawImage(chart, M, D.top(y + 3.4 * 28.35), width=16.6 * 28.35,
                height=3.4 * 28.35, mask="auto")
    y += 3.4 * 28.35 + 14

    # Direktvergleich der Fahrzeuge - fuellt die Uebersichtsseite und macht
    # Unterschiede zwischen den Fahrzeugen ohne Blaettern sichtbar
    text(c, M, y, "Kennzahlen im Direktvergleich", 10, F_BOLD)
    y += 8
    keys = ["bank_delta_p95", "press_p95", "spread1_p95", "spread2_p95"]
    name_w = 200
    col_w = (CONTENT_W - name_w) / max(len(results), 1)
    cols = [("Kennzahl", name_w, "l")] + [(r["veh"]["name"], col_w, "r")
                                          for r in results]
    trows = [["Betriebsstunden"] + [f"{de(r['kpi']['operating_hours'], 1)} h"
                                    for r in results]]
    for key in keys:
        label, unit, dig = None, "", 1
        cells = []
        for r in results:
            m = next((x for x in r["rows"] if x["key"] == key), None)
            if m is None:
                cells.append("-"); continue
            label, unit, dig = m["label"], m["unit"], m["digits"]
            # nur Abweichungen einfaerben - sonst ist die halbe Tabelle bunt
            # und die Hervorhebung verliert ihre Bedeutung
            col = (STATUS[m["status"]]["color"]
                   if m["status"] in STATUS and m["status"] != "gruen"
                   else INK_PRIMARY)
            cells.append((f"{de(m['value'], dig)} {unit}".strip(), col))
        # Faellt die Kennzahl bei allen Fahrzeugen aus, lieber die Zeile
        # weglassen als den rohen Feldnamen in den Kundenbericht zu setzen
        if label:
            trows.append([label] + cells)
    table(c, M, y, cols, trows)
    c.showPage()


def page_condition(c, r, ctx, page_no, total, p):
    v = r["veh"]
    plate = f"Kennzeichen {v['plate']} · " if v["plate"] not in ("—", "-", "") else ""
    page_frame(c, ctx, page_no, total, f"{v['name']} – Zustand",
               f"{plate}Seite 1 von 2")
    y = 86

    st = STATUS.get(r["status"])
    n_lines = len(wrap_lines(c, r["reason"], CONTENT_W - 45, 8))
    bh = 30 + n_lines * 10
    box(c, M, y, CONTENT_W, bh, fill="#ffffff", stroke=st["color"] if st else RULE)
    status_dot(c, M + 12, y + 15, r["status"], r=5.5)
    text(c, M + 30, y + 23, st["label"] if st else "nicht bewertet", 11, F_BOLD,
         st["color"] if st else INK_MUTED)
    wrap(c, M + 30, y + 35, r["reason"], CONTENT_W - 45, 8, color=INK_SECONDARY,
         leading=10)
    y += bh + 12

    if not r["has_data"]:
        box(c, M, y, CONTENT_W, 74, fill=SURFACE_SOFT)
        text(c, M + 12, y + 20, "Keine Betriebsdaten in diesem Zeitraum", 10, F_BOLD)
        wrap(c, M + 12, y + 36,
             "Für dieses Fahrzeug wurden im Berichtszeitraum keine verwertbaren "
             "Messdaten aufgezeichnet. Das ist zu erwarten, wenn das Fahrzeug "
             "nicht im Einsatz war. Bleibt der Zustand über mehrere Wochen "
             "bestehen, sollte die Datenverbindung geprüft werden.",
             CONTENT_W - 24, 8, color=INK_SECONDARY, leading=10)
        c.showPage()
        return

    tiles = [x for x in r["rows"] if x["key"] in
             ("bank_delta_p95", "press_p95", "spread1_p95", "spread2_p95")]
    tw = (CONTENT_W - 3 * 9) / 4
    for i, row in enumerate(tiles):
        kpi_tile(c, M + i * (tw + 9), y, tw, 92, row)
    y += 104

    ch = 6.0 * 28.35
    c.drawImage(p("hours"), M, D.top(y + ch), width=8.1 * 28.35, height=ch,
                mask="auto")
    bx = M + 8.1 * 28.35 + 16
    bw = CONTENT_W - (8.1 * 28.35) - 16
    box(c, bx, y, bw, ch, fill=SURFACE_SOFT)
    text(c, bx + 12, y + 18, "Einsatzprofil", 8.5, F_BOLD)
    segs = r["segments"]
    longest = max((s["seconds"] for s in segs), default=0) / 3600.0
    prev_h = r["kpi_prev"].get("operating_hours", 0)
    delta_h = r["kpi"]["operating_hours"] - prev_h
    facts = [("Einsätze in der Woche", str(len(segs))),
             ("Längster Einsatz", f"{de(longest, 1)} h"),
             ("Betriebsstunden gesamt", f"{de(r['kpi']['operating_hours'], 1)} h"),
             ("Vorwoche", f"{de(prev_h, 1)} h"),
             ("Veränderung", f"{'+' if delta_h >= 0 else '-'}{de(abs(delta_h), 1)} h"),
             ("Auffälligkeiten", str(len(r["kpi"].get("_events", []))))]
    fy = y + 36
    for label, val in facts:
        text(c, bx + 12, fy, label, 7.4, F_REG, INK_SECONDARY)
        text(c, bx + bw - 12, fy, val, 7.8, F_BOLD, INK_PRIMARY, align="r")
        fy += 14
    y += ch + 18

    text(c, M, y, "Betrieb und Auffälligkeiten im Wochenverlauf", 9.5, F_BOLD)
    y += 6
    th = 2.0 * 28.35
    c.drawImage(p("tl"), M, D.top(y + th), width=16.6 * 28.35, height=th,
                mask="auto")
    y += th + 6
    legend = [("#c9d9f2", "Motor läuft"), (STATUS["gelb"]["color"], "leicht"),
              (STATUS["orange"]["color"], "deutlich"), (STATUS["rot"]["color"], "stark")]
    lx = M
    for col, lab in legend:
        c.setFillColor(col)
        c.rect(lx, D.top(y + 6), 8, 6, stroke=0, fill=1)
        text(c, lx + 12, y + 5.5, lab, 7, F_REG, INK_SECONDARY)
        lx += c.stringWidth(lab, F_REG, 7) + 30
    y += 22

    text(c, M, y, "Alle Kennzahlen im Vergleich zum Normalbereich", 9.5, F_BOLD)
    y = metric_table(c, y + 8, r) + 14

    findings = r["quality"]
    if findings:
        txt = " ".join(f["text"] for f in findings)
    else:
        txt = (f"Die Messdaten sind vollständig ({de(r['coverage'] * 100, 0)} % der "
               f"erwarteten Messpunkte). Alle Messstellen haben durchgehend "
               f"plausible Werte geliefert.")
    # Hoehe aus dem Text ableiten, sonst laeuft die Box bei mehreren Befunden ueber
    nl = len(wrap_lines(c, txt, CONTENT_W - 24, 7.6))
    box(c, M, y, CONTENT_W, 24 + nl * 9.6 + 8, fill=SURFACE_SOFT)
    text(c, M + 12, y + 16, "Datenqualität", 8.5, F_BOLD)
    wrap(c, M + 12, y + 29, txt, CONTENT_W - 24, 7.6, color=INK_SECONDARY, leading=9.6)
    c.showPage()


def page_measurements(c, r, ctx, page_no, total, p):
    """Vier Diagramme in voller Breite untereinander.

    Nebeneinander waren die Kurven auf 8 cm zu gedraengt, um die drei
    Messstellen einer Bank auseinanderzuhalten. Volle Breite verdoppelt die
    horizontale Aufloesung; die geringere Hoehe faellt bei Zeitreihen kaum ins
    Gewicht, weil die Information in der Zeitrichtung liegt.
    """
    v = r["veh"]
    plate = f"Kennzeichen {v['plate']} · " if v["plate"] not in ("—", "-", "") else ""
    page_frame(c, ctx, page_no, total, f"{v['name']} – Messwerte",
               f"{plate}Seite 2 von 2")
    y = 82
    if not r["has_data"]:
        text(c, M, y + 10, "Keine Messwerte im Berichtszeitraum vorhanden.",
             9, F_REG, INK_SECONDARY)
        c.showPage()
        return
    w_pt = 16.6 * 28.35
    h_pt = PLOT_H_CM * 28.35

    for name in ("b1", "b2", "press", "delta"):
        c.drawImage(p(name), M, D.top(y + h_pt), width=w_pt, height=h_pt,
                    mask="auto")
        y += h_pt + 8

    # Der Anhang mit den Erklaerungen entfaellt - dieser eine Satz bleibt, weil
    # die Betriebszeit-Achse ohne Hinweis falsch gelesen werden kann.
    text(c, M, y + 2, "Zeitachse = Betriebszeit; Stillstandszeiten sind "
         "herausgenommen, feine senkrechte Linien trennen die Einsätze. Der "
         "Warmlauf zu Beginn eines Einsatzes läuft unten aus dem Bild.",
         6.8, F_REG, INK_MUTED)
    y += 18

    evs = r["kpi"].get("_events", [])
    text(c, M, y, f"Auffälligkeiten beim Abgasgegendruck ({len(evs)} in der Woche)",
         9.5, F_BOLD)
    y += 8
    ecols = [("Beginn", 108, "l"), ("Dauer", 52, "r"), ("Bank", 62, "l"),
             ("Höchstwert", 66, "r"), ("über Grenzwert", 74, "r"),
             ("Temperatur dabei", 76, "r"), ("Stärke", 74, "l")]
    erows = []
    for e in sorted(evs, key=lambda e: -e["over_pct"])[:6]:
        col = {"leicht": STATUS["gelb"], "deutlich": STATUS["orange"],
               "stark": STATUS["rot"]}[e["severity"]]["color"]
        erows.append([
            e["start"].strftime("%d.%m. %H:%M") + " Uhr",
            f"{de(e['duration_min'], 0)} min",
            f"Bank {e['side']}",
            f"{de(e['peak'], 0)} mbar",
            f"+{de(e['over_pct'], 0)} %",
            f"{de(e['temp_max'], 0)} °C",
            (e["severity"], col)])
    if erows:
        y = table(c, M, y, ecols, erows) + 6
        if len(evs) > len(erows):
            text(c, M, y + 8, f"Angezeigt sind die {len(erows)} stärksten von "
                 f"{len(evs)} Auffälligkeiten.", 7, F_REG, INK_MUTED)
            y += 10
    else:
        text(c, M, y + 12, "Keine Überschreitungen des Grenzwerts in dieser Woche.",
             8, F_REG, INK_SECONDARY)
        y += 16
    c.showPage()


# ------------------------------------------------------------------ Main ----

def build(raw, report_end, out_path, log=print):
    """Bericht aus fertigen Bin-Daten erzeugen. Datenquelle-unabhängig.

    Ein Fahrzeug, das aus der Reihe tanzt, darf den ganzen Bericht nicht
    verhindern - lieber ein Bericht mit einer Fehlermeldung auf einer Seite als
    gar keine Mail am Montagmorgen.
    """
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    report_start = report_end - pd.Timedelta(days=7)

    results, paths, failed = [], [], []
    for v in VEHICLES:
        try:
            r = analyse(raw.get(v["car_id"], pd.DataFrame()), v,
                        report_start, report_end)
            results.append(r)
            paths.append(make_plots(r, report_start, report_end))
        except Exception as exc:                      # noqa: BLE001
            log(f"  FEHLER bei {v['car_id']}: {exc}")
            failed.append((v, exc))

    if not results:
        raise RuntimeError("Kein einziges Fahrzeug konnte ausgewertet werden.")

    fleet = os.path.join(TMP, "fleet.png")
    P.fleet_hours([r["veh"]["name"] for r in results],
                  [r["kpi"]["operating_hours"] for r in results],
                  [r["kpi_prev"].get("operating_hours", 0) for r in results], fleet)

    ctx = {
        "period": f"{report_start.strftime('%d.%m.%Y')} – "
                  f"{(report_end - pd.Timedelta(days=1)).strftime('%d.%m.%Y')}",
        "footer_left": "Wöchentlicher Analysebericht · Motorüberwachung · "
                       "automatisch erstellt",
    }

    total = 1 + 2 * len(results) + (1 if failed else 0)
    c = D.new_canvas(out_path)
    page_overview(c, results, ctx, 1, total, fleet)
    n = 2
    for r, p in zip(results, paths):
        page_condition(c, r, ctx, n, total, p); n += 1
        page_measurements(c, r, ctx, n, total, p); n += 1
    if failed:
        page_errors(c, ctx, n, total, failed)
    c.save()

    for r in results:
        log(f"  {r['veh']['car_id']}: {r['status']} · "
            f"{r['kpi']['operating_hours']:.1f} h · "
            f"{len(r['kpi'].get('_events', []))} Auffälligkeiten"
            + ("" if r["has_data"] else " · keine Betriebsdaten"))
    return results, ctx


def page_errors(c, ctx, page_no, total, failed):
    """Fahrzeuge, die nicht ausgewertet werden konnten - sichtbar statt still."""
    page_frame(c, ctx, page_no, total, "Hinweis",
               "Nicht auswertbare Fahrzeuge")
    y = 92
    wrap(c, M, y, "Für die folgenden Fahrzeuge konnte in diesem Zeitraum keine "
         "Auswertung erstellt werden. Die übrigen Fahrzeuge im Bericht sind "
         "davon nicht betroffen.", CONTENT_W, 9, color=INK_SECONDARY, leading=12)
    y += 34
    for v, exc in failed:
        text(c, M, y, v["name"], 10, F_BOLD)
        y = wrap(c, M, y + 13, str(exc)[:400], CONTENT_W, 8,
                 color=INK_SECONDARY, leading=10) + 10
    c.showPage()


def main():
    """Musterbericht aus synthetischen Daten."""
    report_end = pd.Timestamp("2026-08-17 06:00")      # Montag 06:00

    # >>> DATENQUELLE — produktiv: influx_source.build_all(report_end, VEHICLES)
    import fake_data
    raw = fake_data.build_all(report_end)
    # <<<

    build(raw, report_end, OUT)
    print("->", OUT)


if __name__ == "__main__":
    main()
