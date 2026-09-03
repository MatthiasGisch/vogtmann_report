#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Produktiver Einstiegspunkt. Wird vom Cron-Job aufgerufen.

    python3 run_report.py                 # letzte 7 Tage, Bericht + Versand
    python3 run_report.py --no-mail       # nur PDF erzeugen, nicht versenden
    python3 run_report.py --end 2026-08-17

Rückgabewert: 0 = alles gut, 1 = Bericht erstellt aber nicht versendet,
2 = kein Bericht erstellt. Damit lässt sich der Cron-Lauf überwachen.
"""
import argparse
import os
import sys
import traceback
from datetime import datetime

import pandas as pd

from config import VEHICLES, OUTPUT_DIR, STATUS


def log(msg=""):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", help="Ende des Berichtszeitraums (YYYY-MM-DD)")
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--mock", action="store_true",
                    help="synthetische Daten statt InfluxDB (Layouttest)")
    args = ap.parse_args()

    report_end = (pd.Timestamp(args.end) if args.end
                  else pd.Timestamp.now().normalize())
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR,
                       f"Analysebericht_{report_end:%Y-%m-%d}.pdf")

    log(f"Start · Berichtsende {report_end:%d.%m.%Y} · {len(VEHICLES)} Fahrzeuge")

    # ------------------------------------------------------------ Daten ---
    try:
        if args.mock:
            import fake_data
            raw = fake_data.build_all(report_end)
        else:
            import influx_source
            raw = influx_source.build_all(report_end, VEHICLES, log=log)
    except Exception:                                  # noqa: BLE001
        log("ABBRUCH beim Laden der Daten:")
        traceback.print_exc()
        return 2

    # --------------------------------------------------------- Bericht ---
    try:
        import build_report
        results, ctx = build_report.build(raw, report_end, out, log=log)
    except Exception:                                  # noqa: BLE001
        log("ABBRUCH beim Erstellen des Berichts:")
        traceback.print_exc()
        return 2
    log(f"Bericht erstellt: {out} ({os.path.getsize(out) / 1024:.0f} kB)")

    if args.no_mail:
        log("Versand übersprungen (--no-mail)")
        return 0

    # --------------------------------------------------------- Versand ---
    try:
        import mailer
        statuses = [(r["veh"]["name"],
                     STATUS.get(r["status"], {}).get("label", "nicht bewertet"))
                    for r in results]
        mailer.send(out, ctx["period"], statuses, log=log)
    except Exception:                                  # noqa: BLE001
        # Das PDF liegt auf der Platte - der Lauf war nicht umsonst.
        log("Versand fehlgeschlagen, das PDF liegt unter " + out)
        traceback.print_exc()
        return 1

    log("Fertig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
