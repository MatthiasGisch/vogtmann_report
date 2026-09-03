# -*- coding: utf-8 -*-
"""
E-Mail-Versand des fertigen Berichts.

Zugangsdaten kommen aus einer .env-Datei neben dem Skript und stehen damit
nicht mehr im Quelltext (offener Punkt aus der Doku, Abschnitt 6).

    /home/princess_donut/influx/.env
        SMTP_USER=beispiel@gmail.com
        SMTP_PASSWORD=xxxx xxxx xxxx xxxx
        EMAIL_FROM=beispiel@gmail.com
        EMAIL_TO=erste@firma.de,zweite@firma.de

Anschließend Rechte einschränken:  chmod 600 .env
Die Datei gehört in .gitignore und wird beim Update per curl nicht überschrieben.
"""
import os
import smtplib
from email.message import EmailMessage

from config import BASE_DIR

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def load_env(path=None):
    """Minimaler .env-Leser - kein python-dotenv nötig, das ist auf dem Server
    nicht installiert und soll nicht extra dazukommen."""
    path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f".env nicht gefunden: {path}\n"
            f"Anlegen mit SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO.")
    env = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    missing = [k for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")
               if not env.get(k)]
    if missing:
        raise ValueError(f"In {path} fehlen: {', '.join(missing)}")
    return env


def send(pdf_path, period, statuses, env=None, log=print):
    """statuses: Liste von (Fahrzeugname, Statuslabel) für den Mailtext."""
    env = env or load_env()
    recipients = [a.strip() for a in env["EMAIL_TO"].split(",") if a.strip()]

    msg = EmailMessage()
    # Betreff ohne Umlaute - so hielt es der bestehende Bericht auch
    msg["Subject"] = f"Woechentlicher Analysebericht {period}"
    msg["From"] = env["EMAIL_FROM"]
    msg["To"] = ", ".join(recipients)

    lines = [f"Analysebericht fuer den Zeitraum {period}.", ""]
    for name, status in statuses:
        lines.append(f"  {name}: {status}")
    lines += ["", "Der vollstaendige Bericht haengt als PDF an.",
              "Diese Nachricht wurde automatisch erstellt."]
    msg.set_content("\n".join(lines))

    with open(pdf_path, "rb") as fh:
        msg.add_attachment(fh.read(), maintype="application", subtype="pdf",
                           filename=os.path.basename(pdf_path))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
        smtp.send_message(msg)
    log(f"  E-Mail versendet an: {', '.join(recipients)}")
