#!/usr/bin/env python3
"""
Wöchentlicher Fahrzeug-Monitoring-Bericht
Generiert PDF-Berichte aus InfluxDB-Daten für CAR_001 und CAR_002
und versendet diese per E-Mail.
"""

import json
import os
import sys
import tempfile
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta

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
from reportlab.lib.enums import TA_CENTER

# ── Konfiguration ────────────────────────────────────────────────────────────

INFLUXDB_HOST = "http://127.0.0.1:8181"
TOKEN_FILE    = "/home/princess_donut/influx/admin_token.json"
DATABASE      = "sensor_data"
MEASUREMENT   = "sensor_readings"

# E-Mail Konfiguration
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = "gischmatthias@gmail.com"
SMTP_PASSWORD = "hxvp qpha lcms jbww"
EMAIL_FROM    = "gischmatthias@gmail.com"
EMAIL_TO      = ["matthiasgisch@example.com"]
EMAIL_SUBJECT = "Woechentlicher Monitoring-Bericht"

VEHICLES = {
    "CAR_001": {
        "name":        "Fahrzeug 1",
        "kennzeichen": "Y-23-6862",
        "threshold":   2000,
    },
    "CAR_002": {
        "name":        "Fahrzeug 2",
        "kennzeichen": "Y-49-3779",
        "threshold":   2200,
    },
}

# Plot-Gruppen
PLOT_GROUPS = [
    {
        "title":   "Temperatur Bank 1",
        "sensors": ["temp_sensor_0", "temp_sensor_1", "temp_sensor_2"],
        "ylabel":  "Temperatur (°C)",
    },
    {
        "title":   "Temperatur Bank 2",
        "sensors": ["temp_sensor_3", "temp_sensor_4", "temp_sensor_5"],
        "ylabel":  "Temperatur (°C)",
    },
    {
        "title":   "Stauklappe Bank 1",
        "sensors": ["temp_sensor_6"],
        "ylabel":  "Temperatur (°C)",
    },
    {
        "title":   "Stauklappe Bank 2",
        "sensors": ["temp_sensor_7"],
        "ylabel":  "Temperatur (°C)",
    },
]

ALL_SENSORS = [s for g in PLOT_GROUPS for s in g["sensors"]]
OUTPUT_FILE = f"wochenbericht_{datetime.now().strftime('%Y%m%d')}.pdf"

COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
          "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]

# ── Token laden ──────────────────────────────────────────────────────────────

def load_token(path):
    with open(path) as f:
        raw = f.read().strip()
    try:
        data = json.loads(raw)
        return data.get("token") or data.get("admin_token") or list(data.values())[0]
    except json.JSONDecodeError:
        return raw

# ── InfluxDB-Abfragen ────────────────────────────────────────────────────────

def query_influx(client, sql):
    return client.query(sql, language="sql").to_pandas()

def get_temperature_data(client, car_id, days=7):
    sensors = ", ".join(f'AVG("{s}") AS "{s}"' for s in ALL_SENSORS)
    sql = f"""
        SELECT DATE_BIN(INTERVAL '1 hour', time, '1970-01-01') AS ts, {sensors}
        FROM "{MEASUREMENT}"
        WHERE time >= now() - interval '{days} days'
          AND "car_id" = '{car_id}'
        GROUP BY ts ORDER BY ts
    """
    return query_influx(client, sql)

def get_events(client, car_id, threshold, days=7):
    sql = f"""
        SELECT DATE_BIN(INTERVAL '1 hour', time, '1970-01-01') AS ts,
               MAX("abgas_1_value") AS abgas_1, MAX("abgas_2_value") AS abgas_2
        FROM "{MEASUREMENT}"
        WHERE time >= now() - interval '{days} days'
          AND "car_id" = '{car_id}'
        GROUP BY ts
        HAVING MAX("abgas_1_value") > {threshold} OR MAX("abgas_2_value") > {threshold}
        ORDER BY ts
    """
    return query_influx(client, sql)

def get_summary_stats(client, car_id, days=7):
    aggs = ", ".join(
        f'MIN("{s}") AS "min_{s}", MAX("{s}") AS "max_{s}", AVG("{s}") AS "avg_{s}"'
        for s in ALL_SENSORS
    )
    sql = f"""
        SELECT {aggs} FROM "{MEASUREMENT}"
        WHERE time >= now() - interval '{days} days' AND "car_id" = '{car_id}'
    """
    return query_influx(client, sql)

# ── Plot ─────────────────────────────────────────────────────────────────────

def make_plot(df, group, vehicle_name, events_df=None):
    """Erstellt einen Plot für eine Sensor-Gruppe."""
    fig, ax = plt.subplots(figsize=(14, 4))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    if df is not None and not df.empty and "ts" in df.columns:
        df["ts"] = df["ts"].astype("datetime64[ns]")
        for i, sensor in enumerate(group["sensors"]):
            if sensor in df.columns:
                ax.plot(df["ts"], df[sensor], label=sensor.replace("_", " ").title(),
                        color=COLORS[i % len(COLORS)], linewidth=1.2, alpha=0.9)

        if events_df is not None and not events_df.empty and "ts" in events_df.columns:
            events_df["ts"] = events_df["ts"].astype("datetime64[ns]")
            for ts in events_df["ts"]:
                ax.axvline(x=ts, color="#ff4444", alpha=0.4, linewidth=1)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        plt.xticks(rotation=30, ha="right")
    else:
        ax.text(0.5, 0.5, "Keine Daten verfügbar", ha="center", va="center",
                color="white", fontsize=12, transform=ax.transAxes)

    ax.set_title(f"{group['title']} – {vehicle_name}", color="white", fontsize=12, pad=8)
    ax.set_xlabel("Zeit", color="#aaaaaa")
    ax.set_ylabel(group["ylabel"], color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    for spine in ["top", "right"]: ax.spines[spine].set_visible(False)
    for spine in ["bottom", "left"]: ax.spines[spine].set_color("#444")
    if len(group["sensors"]) > 1:
        ax.legend(loc="upper left", fontsize=8, facecolor="#0f3460",
                  labelcolor="white", framealpha=0.7)
    ax.grid(True, alpha=0.15, color="#ffffff")
    plt.tight_layout()

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    plt.savefig(tmp.name, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return tmp.name

# ── Ereignisse & Statistik ───────────────────────────────────────────────────

def describe_events(events_df, threshold):
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
    if stats_df is None or stats_df.empty:
        return None
    rows = [["Sensor", "Min (°C)", "Max (°C)", "Ø (°C)"]]
    row = stats_df.iloc[0]
    for sensor in ALL_SENSORS:
        mn = row.get(f"min_{sensor}")
        mx = row.get(f"max_{sensor}")
        av = row.get(f"avg_{sensor}")
        rows.append([
            sensor.replace("_", " ").title(),
            f"{mn:.1f}" if mn is not None and not np.isnan(float(mn)) else "–",
            f"{mx:.1f}" if mx is not None and not np.isnan(float(mx)) else "–",
            f"{av:.1f}" if av is not None and not np.isnan(float(av)) else "–",
        ])
    return rows

# ── PDF ──────────────────────────────────────────────────────────────────────

def build_pdf(data, output_path):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    s_title = ParagraphStyle("t", parent=styles["Title"], fontSize=22,
        textColor=colors.HexColor("#1a1a2e"), spaceAfter=6)
    s_h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=15,
        textColor=colors.HexColor("#0f3460"), spaceBefore=14, spaceAfter=6)
    s_h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=11,
        textColor=colors.HexColor("#16213e"), spaceBefore=10, spaceAfter=4)
    s_body = ParagraphStyle("b", parent=styles["Normal"], fontSize=9,
        leading=13, textColor=colors.HexColor("#333333"))
    s_event = ParagraphStyle("e", parent=styles["Normal"], fontSize=9,
        leading=14, textColor=colors.HexColor("#cc2200"), leftIndent=10)
    s_center = ParagraphStyle("c", parent=styles["Normal"],
        alignment=TA_CENTER, fontSize=9, textColor=colors.HexColor("#666666"))

    story = []
    now = datetime.now()
    week_start = (now - timedelta(days=7)).strftime("%d.%m.%Y")
    week_end   = now.strftime("%d.%m.%Y")

    # Deckblatt
    story += [
        Spacer(1, 3*cm),
        Paragraph("Wöchentlicher Monitoring-Bericht", s_title),
        Paragraph("Fahrzeug-Motorüberwachung", s_h2),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f3460")),
        Spacer(1, 0.5*cm),
        Paragraph(f"Berichtszeitraum: {week_start} – {week_end}", s_body),
        Paragraph(f"Erstellt am: {now.strftime('%d.%m.%Y %H:%M')} Uhr", s_body),
        Spacer(1, 1*cm),
    ]
    for car_id, vdata in data.items():
        cfg = VEHICLES[car_id]
        n = len(vdata["events_df"]) if vdata["events_df"] is not None and not vdata["events_df"].empty else 0
        story.append(Paragraph(
            f"<b>{cfg['name']} ({cfg['kennzeichen']}):</b> {n} Ereignis(se) über {cfg['threshold']} hPa",
            s_body))
    story.append(PageBreak())

    # Pro Fahrzeug
    for car_id, vdata in data.items():
        cfg = VEHICLES[car_id]
        story += [
            Paragraph(f"{cfg['name']} – {cfg['kennzeichen']}", s_h2),
            HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")),
            Spacer(1, 0.3*cm),
        ]

        # 4 Plots
        for i, (group, plot_path) in enumerate(zip(PLOT_GROUPS, vdata["plot_paths"])):
            story.append(Paragraph(group["title"], s_h3))
            story.append(Image(plot_path, width=16*cm, height=5*cm))
            n = len(vdata["events_df"]) if vdata["events_df"] is not None and not vdata["events_df"].empty else 0
            if n > 0:
                story.append(Paragraph(
                    f"Rote Linien = Abgasdruckereignisse über {cfg['threshold']} hPa", s_center))
            story.append(Spacer(1, 0.3*cm))

        # Statistik
        story += [Spacer(1, 0.3*cm), Paragraph("Statistik Temperatursensoren", s_h3)]
        td = vdata["stats_table"]
        if td:
            t = Table(td, colWidths=[5*cm, 3*cm, 3*cm, 3*cm])
            t.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0f3460")),
                ("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("FONTSIZE",(0,0),(-1,-1),8),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#f5f5f5"),colors.white]),
                ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#cccccc")),
                ("ALIGN",(1,0),(-1,-1),"CENTER"),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("TOPPADDING",(0,0),(-1,-1),4),
                ("BOTTOMPADDING",(0,0),(-1,-1),4),
            ]))
            story.append(t)

        # Ereignisse
        story += [Spacer(1, 0.5*cm),
                  Paragraph(f"Abgasdruckereignisse (Schwellwert: {cfg['threshold']} hPa)", s_h3)]
        for line in vdata["events_text"]:
            story.append(Paragraph(line, s_event if line.startswith("•") else s_body))
        story.append(PageBreak())

    doc.build(story)
    print(f"✅ PDF erstellt: {output_path}")

# ── E-Mail ───────────────────────────────────────────────────────────────────

def send_email(pdf_path):
    print("📧 Sende E-Mail...")
    now = datetime.now()
    msg = MIMEMultipart()
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(EMAIL_TO)
    msg["Subject"] = EMAIL_SUBJECT
    body = (f"Hallo,\n\nanbei der wöchentliche Monitoring-Bericht "
            f"vom {now.strftime('%d.%m.%Y')}.\n\n"
            f"Berichtszeitraum: {(now-timedelta(days=7)).strftime('%d.%m.%Y')} – "
            f"{now.strftime('%d.%m.%Y')}\n\n"
            f"Mit freundlichen Grüßen\nIhr Monitoring-System")
    msg.attach(MIMEText(body, "plain"))
    with open(pdf_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    f"attachment; filename={os.path.basename(pdf_path)}")
    msg.attach(part)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print(f"✅ E-Mail gesendet an: {', '.join(EMAIL_TO)}")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    try:
        from influxdb_client_3 import InfluxDBClient3
    except ImportError:
        print("❌ pip3 install influxdb3-python --break-system-packages")
        sys.exit(1)

    token  = load_token(TOKEN_FILE)
    client = InfluxDBClient3(host=INFLUXDB_HOST, token=token, database=DATABASE)
    data, tmp_files = {}, []

    for car_id, cfg in VEHICLES.items():
        print(f"📊 Verarbeite {cfg['name']} ({car_id})...")
        temp_df   = get_temperature_data(client, car_id)
        events_df = get_events(client, car_id, cfg["threshold"])
        stats_df  = get_summary_stats(client, car_id)

        plot_paths = []
        for group in PLOT_GROUPS:
            path = make_plot(temp_df, group, cfg["name"], events_df)
            plot_paths.append(path)
            tmp_files.append(path)

        data[car_id] = {
            "events_df":   events_df,
            "events_text": describe_events(events_df, cfg["threshold"]),
            "stats_table": build_stats_table(stats_df),
            "plot_paths":  plot_paths,
        }

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE)
    build_pdf(data, output_path)
    send_email(output_path)

    for f in tmp_files:
        try: os.unlink(f)
        except: pass
    client.close()

if __name__ == "__main__":
    main()