#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Messung statt Annahme: zeigt, was die Sensoren tatsächlich liefern.

    python3 check_signals.py            # letzte 4 Wochen, alle Fahrzeuge
    python3 check_signals.py --weeks 8

Hintergrund: Die Auswertung braucht eine Antwort auf "wann lief der Motor".
Ein Betriebszustandssignal gibt es nicht; der Abgasgegendruck ist der beste
Ersatz, weil er nur bei laufendem Motor entsteht. Dieses Skript liest die
tatsächliche Werteverteilung aus und schlägt daraus einen Schwellwert vor -
statt einen zu erfinden.
"""
import argparse

import numpy as np
import pandas as pd

import influx_source as I
from config import (VEHICLES, BANK1_TEMPS, BANK2_TEMPS, FLAP_TEMPS,
                    PRESSURES, BIN_MINUTES)

QS = [0.01, 0.05, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 1.00]


def line(s=""):
    print(s, flush=True)


def verteilung(name, serie):
    s = serie.dropna()
    if s.empty:
        line(f"  {name:<28} keine Werte")
        return
    werte = " ".join(f"{s.quantile(q):>9.0f}" for q in QS)
    line(f"  {name:<28}{werte}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=4)
    args = ap.parse_args()

    end = pd.Timestamp.now().normalize()
    client = I.connect()
    try:
        for v in VEHICLES:
            line()
            line("=" * 100)
            line(f"{v['car_id']} — {v['name']} — letzte {args.weeks} Wochen")
            line("=" * 100)
            df = I.load_vehicle(client, v["car_id"],
                                end - pd.Timedelta(weeks=args.weeks), end,
                                log=lambda *a: None)
            if df.empty:
                line("  keine Daten")
                continue

            line(f"  Bins gesamt: {len(df)}   "
                 f"Messwerte gesamt: {int(df['samples'].sum()):,}")
            hz = df["samples"].sum() / (len(df) * BIN_MINUTES * 60)
            line(f"  Schreibfrequenz: {hz:.3f} Hz  "
                 f"(= alle {1/hz:.1f} s, Doku behauptet 1 s)")
            line(f"  Belegung: {len(df)} von "
                 f"{args.weeks * 7 * 24 * 60 // BIN_MINUTES} moeglichen Bins "
                 f"= {100*len(df)/(args.weeks*7*24*60//BIN_MINUTES):.0f} %")

            line()
            line("  Werteverteilung (Perzentile)")
            line(f"  {'':<28}{'p1':>9}{'p5':>9}{'p25':>9}{'p50':>9}"
                 f"{'p75':>9}{'p90':>9}{'p95':>9}{'p99':>9}{'max':>9}")
            for p in PRESSURES:
                verteilung(f"Druck {p} (max)", df[f"{p}_max"])
            for s in BANK1_TEMPS + BANK2_TEMPS + FLAP_TEMPS:
                verteilung(f"Temp {s} (mean)", df[f"{s}_mean"])

            # ---------------------------------------------- Betriebsschwelle --
            line()
            line("  Betriebserkennung ueber den Abgasgegendruck")
            pmax = df[[f"{p}_max" for p in PRESSURES]].max(axis=1)
            ruhe = float(pmax.median())
            line(f"  Ruheniveau (Median ueber alles): {ruhe:.0f}")
            line(f"  {'Schwellwert':>12}{'Bins':>10}{'Anteil':>9}"
                 f"{'Betrieb/Woche':>16}")
            kandidaten = sorted({int(ruhe + x) for x in
                                 (50, 100, 200, 300, 500, 800, 1200, 2000)})
            for t in kandidaten:
                n = int((pmax > t).sum())
                h = n * BIN_MINUTES / 60 / args.weeks
                line(f"  {t:>12}{n:>10}{100*n/len(df):>8.1f}%{h:>14.1f} h")

            # Vorschlag: groesste Luecke zwischen Ruhe und Betrieb
            oben = pmax[pmax > ruhe + 20]
            if len(oben) > 10:
                line()
                line(f"  Werte ueber Ruheniveau: {len(oben)} Bins")
                line(f"  davon p5={oben.quantile(.05):.0f} "
                     f"p50={oben.quantile(.50):.0f} "
                     f"p95={oben.quantile(.95):.0f} max={oben.max():.0f}")

            # ------------------------------------------- Temperatur im Betrieb -
            for t in (kandidaten[1], kandidaten[4]):
                m = pmax > t
                if m.sum() < 5:
                    continue
                tb = df.loc[m, [f"{s}_mean" for s in BANK1_TEMPS + BANK2_TEMPS]]
                line()
                line(f"  Temperaturen bei Druck > {t} ({int(m.sum())} Bins): "
                     f"min {tb.min().min():.0f} / median {tb.median().median():.0f} "
                     f"/ max {tb.max().max():.0f} °C")
    finally:
        try:
            client.close()
        except Exception:
            pass

    line()
    line("Diese Ausgabe bitte an die Auswertung zurueckmelden — daraus werden")
    line("Betriebsschwelle und Plausibilitaetsgrenzen gesetzt.")


if __name__ == "__main__":
    main()
