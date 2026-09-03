# -*- coding: utf-8 -*-
"""
Synthetische Sensordaten für den Musterbericht.

NUR FÜR DIE VORSCHAU. In der Produktivfassung wird dieses Modul durch die
InfluxDB-Abfrage ersetzt; das Ausgabeformat (ein DataFrame mit 5-Minuten-Bins)
bleibt identisch, damit der Rest der Pipeline unverändert bleibt.

Rückgabeformat je Fahrzeug:
    index : ts (DatetimeIndex, 5-Min-Raster, lückenhaft = Motor stand)
    Spalten:
        samples                      Laufsekunden im Bin   (= COUNT(*))
        <sensor>_mean/_max/_min/_std je Messstelle
        abgas_1_value_max/_mean, abgas_2_value_max/_mean
"""
import zlib

import numpy as np
import pandas as pd

from config import BANK1_TEMPS, BANK2_TEMPS, FLAP_TEMPS, BIN_MINUTES

RNG = np.random.default_rng(20260820)


def _daily_segments(day, rng):
    """1-3 Einsätze je Werktag, sonntags meist keiner."""
    if day.weekday() == 6 and rng.random() < 0.8:
        return []
    n = rng.integers(1, 4)
    starts = sorted(rng.choice(np.arange(6 * 60, 18 * 60, 30), size=n, replace=False))
    segs = []
    for s in starts:
        dur = int(rng.integers(45, 220))
        beg = day + pd.Timedelta(minutes=int(s))
        end = beg + pd.Timedelta(minutes=dur)
        if segs and beg < segs[-1][1] + pd.Timedelta(minutes=20):
            continue
        segs.append((beg, end))
    return segs


def generate(car_id, start, weeks, bank_offset=0.0, drift_per_week=0.0,
             pressure_base=1650.0, pressure_drift=0.0, event_rate=0.02):
    """
    bank_offset      Grundunterschied Bank 1 - Bank 2 in K
    drift_per_week   zusätzlicher Zuwachs dieses Unterschieds je Woche
    pressure_drift   Zuwachs des Gegendrucks je Woche
    event_rate       Wahrscheinlichkeit einer Druckspitze je Bin
    """
    # zlib.crc32 statt hash(): hash() ist pro Prozess randomisiert und
    # der Musterbericht waere sonst bei jedem Lauf ein anderer
    rng = np.random.default_rng(zlib.crc32(car_id.encode()))
    rows = []
    days = pd.date_range(start, periods=weeks * 7, freq="D")

    for day in days:
        week_idx = (day - days[0]).days // 7
        drift = bank_offset + drift_per_week * week_idx
        p_base = pressure_base + pressure_drift * week_idx

        for beg, end in _daily_segments(day, rng):
            bins = pd.date_range(beg.floor(f"{BIN_MINUTES}min"), end,
                                 freq=f"{BIN_MINUTES}min")
            if len(bins) < 3:
                continue
            n = len(bins)
            t = np.arange(n)

            # Lastprofil: Anlauframpe, dann schwankende Last
            warm = 1 - np.exp(-t / 4.0)
            load = 0.55 + 0.30 * np.sin(t / 7.0 + rng.random() * 6) \
                        + 0.12 * rng.standard_normal(n)
            load = np.clip(load, 0.05, 1.15)

            base1 = 25 + (430 + 190 * load) * warm
            base2 = base1 - drift * warm

            row = {}
            for i, s in enumerate(BANK1_TEMPS):
                row[s] = base1 + rng.normal(i * 15 - 15, 5.0, n)
            for i, s in enumerate(BANK2_TEMPS):
                row[s] = base2 + rng.normal(i * 15 - 15, 5.0, n)
            row[FLAP_TEMPS[0]] = base1 * 0.94 + rng.normal(0, 5, n)
            row[FLAP_TEMPS[1]] = base2 * 0.94 + rng.normal(0, 5, n)

            press = p_base * (0.55 + 0.55 * load) + rng.normal(0, 55, n)
            # Druckspitzen als zusammenhaengende Phasen, nicht als Einzelpunkte -
            # sonst haetten alle Vorfaelle im Musterbericht dieselbe Dauer
            bump = np.zeros(n)
            for k in range(n):
                if rng.random() < event_rate:
                    ln = int(rng.integers(2, 9))
                    amp = rng.uniform(320, 760)
                    sl = slice(k, min(k + ln, n))
                    shape = np.hanning(max(2 * (sl.stop - sl.start), 3))[
                        :sl.stop - sl.start]
                    bump[sl] = np.maximum(bump[sl], amp * (0.55 + 0.45 * shape))
            press = np.clip(press + bump, 0, None)

            samples = np.full(n, BIN_MINUTES * 60)
            samples[0] = int(rng.integers(60, BIN_MINUTES * 60))
            samples[-1] = int(rng.integers(60, BIN_MINUTES * 60))

            for k in range(n):
                r = {"ts": bins[k], "samples": int(samples[k])}
                for s in BANK1_TEMPS + BANK2_TEMPS + FLAP_TEMPS:
                    v = row[s][k]
                    r[f"{s}_mean"] = v
                    r[f"{s}_max"] = v + abs(rng.normal(0, 3))
                    r[f"{s}_min"] = v - abs(rng.normal(0, 3))
                    r[f"{s}_std"] = abs(rng.normal(2.5, 0.8))
                p1 = press[k]
                p2 = press[k] * rng.uniform(0.94, 1.06)
                r["abgas_1_value_mean"] = p1
                r["abgas_1_value_max"] = p1 + abs(rng.normal(0, 40))
                r["abgas_2_value_mean"] = p2
                r["abgas_2_value_max"] = p2 + abs(rng.normal(0, 40))
                rows.append(r)

    df = pd.DataFrame(rows).set_index("ts").sort_index()
    return df


def _inject_stuck(df, sensor, when, minutes=75):
    """Simuliert eine festhängende Messstelle, damit die Datenqualitätsprüfung
    im Musterbericht etwas zu zeigen hat."""
    end = when + pd.Timedelta(minutes=minutes)
    mask = (df.index >= when) & (df.index <= end)
    if not mask.any():
        return df
    held = float(df.loc[mask, f"{sensor}_mean"].iloc[0])
    for suf in ("_mean", "_max", "_min"):
        df.loc[mask, f"{sensor}{suf}"] = held
    df.loc[mask, f"{sensor}_std"] = 0.0
    return df


def build_all(report_end, weeks=6):
    """Sechs Wochen: Berichtswoche, Vorwoche und vier Referenzwochen.

    Sechs, nicht fünf: die Referenzwochen liegen bei report_start - 14 bis
    - 35 Tagen, also bis 42 Tage vor Berichtsende. Mit fünf Wochen faellt die
    aelteste Referenzwoche leer aus und der Median wird aus drei statt vier
    Wochen gebildet.
    """
    start = (report_end - pd.Timedelta(days=7 * weeks)).normalize()
    car1 = generate("CAR_001", start, weeks,
                    bank_offset=7.0, drift_per_week=0.4,
                    pressure_base=1600, pressure_drift=5,
                    event_rate=0.005)
    # wachsender Seitenunterschied + steigender Gegendruck
    car2 = generate("CAR_002", start, weeks,
                    bank_offset=9.0, drift_per_week=3.2,
                    pressure_base=1780, pressure_drift=42,
                    event_rate=0.030)

    week_start = report_end - pd.Timedelta(days=7)
    later = car2.index[car2.index > week_start + pd.Timedelta(days=2)]
    if len(later):
        car2 = _inject_stuck(car2, "temp_sensor_4", later[0])

    return {"CAR_001": car1, "CAR_002": car2}
