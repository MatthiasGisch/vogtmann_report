#!/usr/bin/env python3
"""
Wöchentlicher Fahrzeug-Monitoring-Bericht
Generiert PDF-Berichte aus InfluxDB-Daten für CAR_001 und CAR_002
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, PageBreak, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ── Konfiguration ────────────────────────────────────────────────────────────

INFLUXDB_HOST  = "http://127.0.0.1:8181"
TOKEN_FILE     = "/var/lib/admin-token.json"
DATABASE       = "sensor_data"
MEASUREMENT    = "sensor_readings"

TEMP_SENSORS = [f"temp_sensor_{i}" for i in range(8)]

VEHICLES = {
    "CAR_001": {
        "name":        "Fahrzeug 1",
        "kennzeichen": "Y-23-6862",
        "threshold":   2000,
    },
    "CAR_002": {
        "name":        "Fahrzeug 2",
        "kennzeichen": "CAR_002",
        "threshold":   2200,
    },
}

OUTPUT_FILE = f"wochenbericht_{datetime.now().strftime('%Y%m%d')}.pdf"

# ── Token laden ──────────────────────────────────────────────────────────────

def load_token(path: str) -> str:
    with open(path) as f:
        raw = f.read().strip()
    try:
        data = json.loads(raw)
        # Unterstützt {"token": "..."} und {"admin_token": "..."}
        return data.get("token") or data.get("admin_token") or list(data.values())[0]
    except json.JSONDecodeError:
        return raw  # Plaintext-Token

# ── InfluxDB-Abfragen ────────────────────────────────────────────────────────

def query_influx(client, sql: str):
    """Führt eine SQL-Abfrage aus und gibt ein pandas DataFrame zurück."""
    return client.query(sql, language="sql").to_pandas()


def get_temperature_data(client, car_id: str, days: int = 7):
    """Temperaturwerte der letzten `days` Tage, pro Stunde aggregiert."""
    sensors = ", ".join(
        f'AVG("{s}") AS "{s}"' for s in TEMP_SENSORS
    )
    sql = f"""
        SELECT
            DATE_BIN(INTERVAL '1 hour', time, '1970-01-01') AS ts,
            {sensors}
        FROM "{MEASUREMENT}"
        WHERE time >= now() - interval '{days} days'
          AND "car_id" = '{car_id}'
        GROUP BY ts
        ORDER BY ts
    """
    return query_influx(client, sql)


def get_events(client, car_id: str, threshold: int, days: int = 7):
    """Zeitpunkte, an denen Abgasdruck den Schwellwert überschritten hat."""
    sql = f"""
        SELECT
            DATE_BIN(INTERVAL '1 hour', time, '1970-01-01') AS ts,
            MAX("abgas_1_value") AS abgas_1,
            MAX("abgas_2_value") AS abgas_2
        FROM "{MEASUREMENT}"
        WHERE time >= now() - interval '{days} days'
          AND "car_id" = '{car_id}'
        GROUP BY ts
        HAVING MAX("abgas_1_value") > {threshold}
           OR  MAX("abgas_2_value") > {threshold}
        ORDER BY ts
    """
    return query_influx(client, sql)


def get_summary_stats(client, car_id: str, days: int = 7):
    """Min/Max/Avg aller Temperatursensoren."""
    aggs = ", ".join(
        f'MIN("{s}") AS "min_{s}", MAX("{s}") AS "max_{s}", AVG("{s}") AS "avg_{s}"'
        for s in TEMP_SENSORS
    )
    sql = f"""
        SELECT {aggs}
        FROM "{MEASUREMENT}"
        WHERE time >= now() - interval '{days} days'
          AND "car_id" = '{car_id}'
    """
    return query_influx(client, sql)

# ── Plot-Erzeugung ───────────────────────────────────────────────────────────

SENSOR_LABELS = {
    "temp_sensor_0": "Temp Sensor 0",
    "temp_sensor_1": "Temp Sensor 1",
    "temp_sensor_2": "Temp Sensor 2",
    "temp_sensor_3": "Temp Sensor 3",
    "temp_sensor_4": "Temp Sensor 4",
    "temp_sensor_5": "Temp Sensor 5",
    "temp_sensor_6": "Temp Sensor 6",
    "temp_sensor_7": "Temp Sensor 7",
}

COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
]


def plot_temperatures(df, vehicle_name: str, events_df=None) -> str:
    """Erstellt Temperaturplot und gibt den Dateipfad zurück."""
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    if df is not None and not df.empty and "ts" in df.columns:
        df["ts"] = df["ts"].astype("datetime64[ns]")
        for i, sensor in enumerate(TEMP_SENSORS):
            if sensor in df.columns:
                ax.plot(
                    df["ts"], df[sensor],
                    label=SENSOR_LABELS[sensor],
                    color=COLORS[i], linewidth=1.2, alpha=0.9
                )

        # Ereignis-Markierungen
        if events_df is not None and not events_df.empty and "ts" in events_df.columns:
            events_df["ts"] = events_df["ts"].astype("datetime64[ns]")
            for ts in events_df["ts"]:
                ax.axvline(x=ts, color="#ff4444", alpha=0.4, linewidth=1)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        plt.xticks(rotation=30, ha="right")
    else:
        ax.text(0.5, 0.5, "Keine Daten verfügbar",
                ha="center", va="center", color="white", fontsize=12,
                transform=ax.transAxes)

    ax.set_title(f"Temperaturverlauf – {vehicle_name}", color="white", fontsize=13, pad=10)
    ax.set_xlabel("Zeit", color="#aaaaaa")
    ax.set_ylabel("Temperatur (°C)", color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    ax.spines["bottom"].set_color("#444")
    ax.spines["left"].set_color("#444")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", fontsize=7, ncol=2,
              facecolor="#0f3460", labelcolor="white", framealpha=0.7)
    ax.grid(True, alpha=0.15, color="#ffffff")

    plt.tight_layout()
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    plt.savefig(tmp.name, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return tmp.name

# ── Ereignis-Beschreibung ────────────────────────────────────────────────────

def describe_events(events_df, threshold: int) -> list[str]:
    """Erstellt deutsche Textbeschreibungen der Ereignisse."""
    if events_df is None or events_df.empty:
        return [f"Keine Abgasdruckereignisse über {threshold} hPa im Berichtszeitraum."]

    lines = []
    events_df["ts"] = events_df["ts"].astype("datetime64[ns]")
    for _, row in events_df.iterrows():
        ts_str = row["ts"].strftime("%d.%m.%Y %H:%M")
        triggered = []
        if "abgas_1" in row and row["abgas_1"] > threshold:
            triggered.append(f"Bank 1: {row['abgas_1']:.0f} hPa")
        if "abgas_2" in row and row["abgas_2"] > threshold:
            triggered.append(f"Bank 2: {row['abgas_2']:.0f} hPa")
        if triggered:
            lines.append(f"• {ts_str} Uhr – Abgasdruck überschritten ({', '.join(triggered)})")
    return lines


def build_stats_table(stats_df):
    """Erstellt eine Tabelle mit Min/Max/Avg Werten."""
    if stats_df is None or stats_df.empty:
        return None

    header = ["Sensor", "Min (°C)", "Max (°C)", "Ø (°C)"]
    rows = [header]
    row = stats_df.iloc[0]
    for sensor in TEMP_SENSORS:
        mn = row.get(f"min_{sensor}", None)
        mx = row.get(f"max_{sensor}", None)
        av = row.get(f"avg_{sensor}", None)
        rows.append([
            SENSOR_LABELS[sensor],
            f"{mn:.1f}" if mn is not None and not np.isnan(mn) else "–",
            f"{mx:.1f}" if mx is not None and not np.isnan(mx) else "–",
            f"{av:.1f}" if av is not None and not np.isnan(av) else "–",
        ])
    return rows

# ── PDF-Aufbau ───────────────────────────────────────────────────────────────

def build_pdf(data: dict, output_path: str):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "title", parent=styles["Title"],
        fontSize=22, textColor=colors.HexColor("#1a1a2e"),
        spaceAfter=6,
    )
    style_h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"],
        fontSize=15, textColor=colors.HexColor("#0f3460"),
        spaceBefore=14, spaceAfter=6,
    )
    style_h3 = ParagraphStyle(
        "h3", parent=styles["Heading3"],
        fontSize=11, textColor=colors.HexColor("#16213e"),
        spaceBefore=10, spaceAfter=4,
    )
    style_body = ParagraphStyle(
        "body", parent=styles["Normal"],
        fontSize=9, leading=13,
        textColor=colors.HexColor("#333333"),
    )
    style_event = ParagraphStyle(
        "event", parent=styles["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor("#cc2200"),
        leftIndent=10,
    )
    style_center = ParagraphStyle(
        "center", parent=styles["Normal"],
        alignment=TA_CENTER, fontSize=9,
        textColor=colors.HexColor("#666666"),
    )

    story = []

    # ── Deckblatt ──
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("Wöchentlicher Monitoring-Bericht", style_title))
    story.append(Paragraph("Fahrzeug-Motorüberwachung", style_h2))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f3460")))
    story.append(Spacer(1, 0.5*cm))

    now = datetime.now()
    week_start = (now - timedelta(days=7)).strftime("%d.%m.%Y")
    week_end   = now.strftime("%d.%m.%Y")
    story.append(Paragraph(f"Berichtszeitraum: {week_start} – {week_end}", style_body))
    story.append(Paragraph(f"Erstellt am: {now.strftime('%d.%m.%Y %H:%M')} Uhr", style_body))
    story.append(Spacer(1, 1*cm))

    # Kurzübersicht Ereignisse
    total_events = sum(len(v["events_text"]) for v in data.values()
                       if v["events_text"] != ["Keine Abgasdruckereignisse über " +
                                                str(VEHICLES[list(data.keys())[0]]["threshold"]) +
                                                " hPa im Berichtszeitraum."])
    for car_id, vdata in data.items():
        cfg = VEHICLES[car_id]
        n = len(vdata["events_df"]) if vdata["events_df"] is not None and not vdata["events_df"].empty else 0
        story.append(Paragraph(
            f"<b>{cfg['name']} ({cfg['kennzeichen']}):</b> "
            f"{n} Abgasdruckereignis(se) über {cfg['threshold']} hPa",
            style_body
        ))

    story.append(PageBreak())

    # ── Pro Fahrzeug ──
    for car_id, vdata in data.items():
        cfg = VEHICLES[car_id]

        story.append(Paragraph(f"{cfg['name']} – {cfg['kennzeichen']}", style_h2))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        story.append(Spacer(1, 0.3*cm))

        # Temperaturplot
        story.append(Paragraph("Temperaturverlauf (letzte 7 Tage)", style_h3))
        if vdata["plot_path"]:
            story.append(Image(vdata["plot_path"], width=16*cm, height=6*cm))
            n_events = len(vdata["events_df"]) if vdata["events_df"] is not None and not vdata["events_df"].empty else 0
            if n_events > 0:
                story.append(Paragraph(
                    f"Rote Markierungen = {n_events} Abgasdruckereignis(se) über {cfg['threshold']} hPa",
                    style_center
                ))
        story.append(Spacer(1, 0.4*cm))

        # Statistik-Tabelle
        story.append(Paragraph("Statistik Temperatursensoren", style_h3))
        table_data = vdata["stats_table"]
        if table_data:
            t = Table(table_data, colWidths=[5*cm, 3*cm, 3*cm, 3*cm])
            t.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0f3460")),
                ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
                ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1),
                 [colors.HexColor("#f5f5f5"), colors.white]),
                ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
                ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING",    (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("Keine Statistikdaten verfügbar.", style_body))

        story.append(Spacer(1, 0.5*cm))

        # Ereignisse
        story.append(Paragraph(f"Abgasdruckereignisse (Schwellwert: {cfg['threshold']} hPa)", style_h3))
        for line in vdata["events_text"]:
            if line.startswith("•"):
                story.append(Paragraph(line, style_event))
            else:
                story.append(Paragraph(line, style_body))

        story.append(PageBreak())

    doc.build(story)
    print(f"✅ Bericht erstellt: {output_path}")

# ── Hauptprogramm ────────────────────────────────────────────────────────────

def main():
    try:
        from influxdb_client_3 import InfluxDBClient3
    except ImportError:
        print("❌ influxdb3-python nicht installiert.")
        print("   Bitte ausführen: pip install influxdb3-python --break-system-packages")
        sys.exit(1)

    token = load_token(TOKEN_FILE)
    client = InfluxDBClient3(host=INFLUXDB_HOST, token=token, database=DATABASE)

    data = {}
    tmp_files = []

    for car_id, cfg in VEHICLES.items():
        print(f"📊 Verarbeite {cfg['name']} ({car_id})...")

        temp_df   = get_temperature_data(client, car_id)
        events_df = get_events(client, car_id, cfg["threshold"])
        stats_df  = get_summary_stats(client, car_id)

        plot_path = plot_temperatures(temp_df, cfg["name"], events_df)
        tmp_files.append(plot_path)

        data[car_id] = {
            "events_df":   events_df,
            "events_text": describe_events(events_df, cfg["threshold"]),
            "stats_table": build_stats_table(stats_df),
            "plot_path":   plot_path,
        }

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE)
    build_pdf(data, output_path)

    # Temporäre Plot-Dateien aufräumen
    for f in tmp_files:
        try:
            os.unlink(f)
        except Exception:
            pass

    client.close()


if __name__ == "__main__":
    main()