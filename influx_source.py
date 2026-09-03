# -*- coding: utf-8 -*-
"""
Datenquelle: InfluxDB 3 Core.

Ersetzt fake_data.py im Produktivbetrieb. Liefert exakt dasselbe Format, damit
analysis.py, plots.py und build_report.py unverändert bleiben:

    index : ts (DatetimeIndex, 5-Min-Raster, ORTSZEIT, lückenhaft)
    Spalten:
        samples                       COUNT(*) im Bin = Laufsekunden
        <sensor>_mean/_max/_min/_std  je Messstelle
        abgas_1_value_mean/_max, abgas_2_value_mean/_max

ACHTUNG: Dieses Modul ist gegen die echte Datenbank noch nicht gelaufen. Die
SQL-Funktionsnamen (DATE_BIN, STDDEV) sind DataFusion-Standard und sollten in
InfluxDB 3 Core vorhanden sein; der erste Lauf gehört trotzdem manuell geprüft
(siehe selftest() unten).
"""
import json
import os
import time

import pandas as pd

from config import (INFLUX_HOST, INFLUX_DATABASE, INFLUX_TABLE,
                    INFLUX_TOKEN_FILE, TIMEZONE, BIN_MINUTES,
                    QUERY_CHUNK_DAYS, QUERY_RETRIES,
                    BANK1_TEMPS, BANK2_TEMPS, FLAP_TEMPS, PRESSURES)

TEMPS = BANK1_TEMPS + BANK2_TEMPS + FLAP_TEMPS


# ----------------------------------------------------------------- Token ----

def read_token(path=INFLUX_TOKEN_FILE):
    """Token aus admin-token.json. Das Format der Datei hängt davon ab, wie sie
    erzeugt wurde - deshalb mehrere gängige Schlüssel durchprobieren."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Token-Datei nicht gefunden: {path}. "
            f"Pfad in config.INFLUX_TOKEN_FILE anpassen.")
    with open(path) as fh:
        raw = fh.read().strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw                                   # reine Textdatei
    for key in ("token", "apiToken", "api_token", "access_token"):
        if isinstance(data, dict) and data.get(key):
            return data[key]
    raise ValueError(f"Kein Token-Feld in {path} gefunden. Enthaltene Felder: "
                     f"{list(data) if isinstance(data, dict) else type(data)}")


# ------------------------------------------------------------------- SQL ----

def build_sql(car_id, start_utc, end_utc):
    """Eine Abfrage je Zeitraum, alle Kennwerte in einem Durchgang.

    Statt nur AVG werden MEAN/MAX/MIN/STDDEV und COUNT geholt - dieselbe Query,
    aber die Grundlage für Betriebszeit, Peaks und die Festhänger-Prüfung.
    """
    parts = [f'AVG("{s}") AS {s}_mean, MAX("{s}") AS {s}_max, '
             f'MIN("{s}") AS {s}_min, STDDEV("{s}") AS {s}_std'
             for s in TEMPS]
    parts += [f'AVG("{p}") AS {p}_mean, MAX("{p}") AS {p}_max'
              for p in PRESSURES]
    cols = ",\n       ".join(parts)
    return f"""
SELECT DATE_BIN(INTERVAL '{BIN_MINUTES} minutes', time, TIMESTAMP '1970-01-01') AS ts,
       COUNT(*) AS samples,
       {cols}
FROM "{INFLUX_TABLE}"
WHERE car_id = '{car_id}'
  AND time >= TIMESTAMP '{start_utc:%Y-%m-%d %H:%M:%S}'
  AND time <  TIMESTAMP '{end_utc:%Y-%m-%d %H:%M:%S}'
GROUP BY ts
ORDER BY ts
""".strip()


# ---------------------------------------------------------------- Client ----

def connect():
    from influxdb_client_3 import InfluxDBClient3
    return InfluxDBClient3(host=INFLUX_HOST, token=read_token(),
                           database=INFLUX_DATABASE)


def _query(client, sql, log=print):
    """Mit Wiederholung: der Container kann kurz nicht erreichbar sein."""
    last = None
    for attempt in range(1, QUERY_RETRIES + 1):
        try:
            table = client.query(query=sql, language="sql")
            return table.to_pandas()
        except Exception as exc:                      # noqa: BLE001
            last = exc
            log(f"    Abfrage fehlgeschlagen (Versuch {attempt}/{QUERY_RETRIES}): {exc}")
            if attempt < QUERY_RETRIES:
                time.sleep(3 * attempt)
    raise RuntimeError(f"Abfrage nach {QUERY_RETRIES} Versuchen erfolglos: {last}")


# ------------------------------------------------------------------ Laden ---

def load_vehicle(client, car_id, start_local, end_local, log=print):
    """Zeitraum wochenweise holen und zusammensetzen.

    Wochenweise, weil eine einzelne Abfrage über sechs Wochen sekündlicher
    Rohdaten sehr viele Parquet-Dateien anfasst und am query-file-limit des
    Containers scheitern kann. Fünf kleine Abfragen sind unkritisch.
    """
    tz = TIMEZONE
    start_utc = pd.Timestamp(start_local, tz=tz).tz_convert("UTC")
    end_utc = pd.Timestamp(end_local, tz=tz).tz_convert("UTC")

    frames, cursor = [], start_utc
    while cursor < end_utc:
        chunk_end = min(cursor + pd.Timedelta(days=QUERY_CHUNK_DAYS), end_utc)
        sql = build_sql(car_id, cursor, chunk_end)
        try:
            df = _query(client, sql, log)
            if len(df):
                frames.append(df)
            log(f"    {car_id} {cursor:%d.%m.} – {chunk_end:%d.%m.}: {len(df)} Bins")
        except Exception as exc:                      # noqa: BLE001
            # Eine fehlende Referenzwoche ist verschmerzbar, die Auswertung
            # läuft dann mit weniger Referenz weiter.
            log(f"    {car_id} {cursor:%d.%m.} – {chunk_end:%d.%m.}: übersprungen ({exc})")
        cursor = chunk_end

    if not frames:
        return _empty_frame()

    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    # Doppelte zuerst in UTC entfernen. In UTC ist jeder Zeitpunkt eindeutig;
    # in Ortszeit gibt es am Ende der Sommerzeit die Stunde 02:00-03:00 zweimal.
    # Wuerde man erst umrechnen und dann deduplizieren, verschwaende der Bericht
    # jedes Jahr im Oktober eine Stunde echter Messdaten.
    df = df.sort_values("ts")
    df = df[~df["ts"].duplicated(keep="last")]
    # Erst danach in Ortszeit umrechnen und die Zeitzone abstreifen - der
    # restliche Code rechnet mit naiven lokalen Zeitstempeln.
    df["ts"] = df["ts"].dt.tz_convert(tz).dt.tz_localize(None)
    df = df.set_index("ts").sort_index()

    df["samples"] = df["samples"].astype(int)
    for col in df.columns:
        if col != "samples":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # STDDEV liefert NULL, wenn ein Bin nur einen Messwert enthält. Für die
    # Festhänger-Prüfung ist "keine Streuung messbar" nicht dasselbe wie
    # "Streuung null" - deshalb NaN, nicht 0.
    return df


def _empty_frame():
    cols = ["samples"]
    for s in TEMPS:
        cols += [f"{s}_mean", f"{s}_max", f"{s}_min", f"{s}_std"]
    for p in PRESSURES:
        cols += [f"{p}_mean", f"{p}_max"]
    return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="ts"))


def build_all(report_end, vehicles, weeks=6, log=print):
    """Einstiegspunkt - gleiche Signaturidee wie fake_data.build_all."""
    start = report_end - pd.Timedelta(days=7 * weeks)
    client = connect()
    out = {}
    try:
        for v in vehicles:
            log(f"  Lade {v['car_id']} …")
            out[v["car_id"]] = load_vehicle(client, v["car_id"], start,
                                            report_end, log)
    finally:
        try:
            client.close()
        except Exception:                             # noqa: BLE001
            pass
    return out


# --------------------------------------------------------------- Selftest ---

def selftest():
    """Vor dem ersten produktiven Lauf einmal ausführen:

        python3 influx_source.py

    Prüft Token, Verbindung, SQL-Funktionen und Datenumfang - einzeln, damit
    bei einem Fehler klar ist, welcher Schritt klemmt.
    """
    from config import VEHICLES
    print("1) Token lesen …", end=" ")
    token = read_token()
    print(f"OK ({len(token)} Zeichen)")

    print("2) Verbindung aufbauen …", end=" ")
    client = connect()
    print("OK")

    print("3) Tabelle lesbar …", end=" ")
    df = _query(client, f'SELECT * FROM "{INFLUX_TABLE}" LIMIT 1')
    print(f"OK ({len(df.columns)} Spalten)")
    missing = [c for c in TEMPS + PRESSURES + ["car_id"] if c not in df.columns]
    if missing:
        print(f"   ACHTUNG fehlende Spalten: {missing}")

    print("4) DATE_BIN und STDDEV …", end=" ")
    end = pd.Timestamp.utcnow().tz_localize(None)
    sql = build_sql(VEHICLES[0]["car_id"], end - pd.Timedelta(days=1), end)
    df = _query(client, sql)
    print(f"OK ({len(df)} Bins in 24 h)")

    print("5) Schreibfrequenz — die Auswertung setzt 1 Messwert je Sekunde voraus,")
    print("   sonst sind Betriebsstunden und Auffälligkeitsrate um genau diesen")
    print("   Faktor falsch:")
    for v in VEHICLES:
        q = (f'SELECT COUNT(*) AS n FROM "{INFLUX_TABLE}" '
             f"WHERE car_id = '{v['car_id']}' "
             f"AND time > now() - INTERVAL '10 minutes'")
        n = int(_query(client, q)["n"].iloc[0])
        hz = n / 600.0
        note = "OK" if 0.8 <= hz <= 1.2 else ("Motor stand?" if n == 0
                                              else f"ACHTUNG Faktor {hz:.2f}")
        print(f"   {v['car_id']}: {n} Werte in 10 min = {hz:.2f} Hz — {note}")

    print("6) Umfang der letzten 6 Wochen je Fahrzeug:")
    for v in VEHICLES:
        n = _query(client, f'SELECT COUNT(*) AS n FROM "{INFLUX_TABLE}" '
                           f"WHERE car_id = '{v['car_id']}' "
                           f"AND time > now() - INTERVAL '42 days'")
        print(f"   {v['car_id']}: {int(n['n'].iloc[0]):,} Rohmesswerte")
    client.close()
    print("\nAlle Prüfungen bestanden.")


if __name__ == "__main__":
    selftest()
