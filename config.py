# -*- coding: utf-8 -*-
"""
Zentrale Konfiguration des Analyseberichts.

In der Produktivfassung wandert der Inhalt von VEHICLES in eine vehicles.yaml
(Konzept Abschnitt 14). Struktur bleibt identisch.
"""

# ------------------------------------------------------------------ Betrieb ---
import os

BASE_DIR   = os.environ.get("VOGTMANN_DIR", "/home/princess_donut/influx")
OUTPUT_DIR = os.path.join(BASE_DIR, "berichte")

# InfluxDB 3 Core - nur lokal erreichbar
INFLUX_HOST     = os.environ.get("INFLUX_HOST", "http://127.0.0.1:8181")
INFLUX_DATABASE = "sensor_data"
INFLUX_TABLE    = "sensor_readings"
INFLUX_TOKEN_FILE = os.path.join(BASE_DIR, "admin_token.json")

# Die Datenbank speichert UTC. Alle Zeitangaben im Bericht sollen Ortszeit sein,
# sonst stimmen die Uhrzeiten in der Vorfallstabelle im Sommer um zwei Stunden
# nicht.
TIMEZONE = "Europe/Berlin"

# Die Wochendaten werden wochenweise abgefragt statt in einem Rutsch. Fuenf
# kleine Queries belasten das query-file-limit deutlich weniger als eine grosse
# ueber fuenf Wochen sekuendlicher Rohdaten.
QUERY_CHUNK_DAYS = 7
QUERY_RETRIES    = 3

# ------------------------------------------------------------- Auflösungen ---
BIN_MINUTES         = 5      # Analysebasis (Konzept 3.)
SEGMENT_GAP_MINUTES = 5      # größere Lücke = neuer Einsatz
EVENT_MERGE_MINUTES = 10     # Vorfälle mit kleinerer Lücke verschmelzen
MIN_VALID_SECONDS   = 60     # Bins mit weniger Laufzeit fließen nicht in Statistik
WARMUP_LIMIT_C      = 80     # darunter gilt der Motor als kalt

# --------------------------------------------------------- Toleranzmodell ----
# Konzept 8.2 - Typ A auf Median, Typ B auf p95 der Referenzphase
TOL_GREEN  = 0.10
TOL_YELLOW = 0.15
MIN_BAND_K = 5.0             # absolutes Mindestband für Temperaturdifferenzen

# Absolutes Mindestband je Kennzahl (Konzept 8.2, Schutzregel 2).
# Verhindert, dass ein sehr kleiner oder null-naher Referenzwert dazu führt,
# dass jede normale Streuung als Abweichung gewertet wird.
MIN_ABS_BAND = {
    "bank1_mean":     8.0,    # K
    "bank2_mean":     8.0,    # K
    "press_p50":     60.0,    # Rohwert
    "press_p95":     60.0,    # Rohwert
    "event_rate":     1.0,    # Vorfälle je 10 Betriebsstunden
    "bank_delta_p95": MIN_BAND_K,
    "spread1_p95":    MIN_BAND_K,
    "spread2_p95":    MIN_BAND_K,
    "flap_delta_p95": MIN_BAND_K,
}
REF_MIN_OPERATING_HOURS = 20 # darunter wird nicht bewertet

# Ab dem RED_FACTOR-fachen des Gelb-Bandes gilt eine Abweichung als deutlich
# (Ampelstufe rot). Ohne diese Stufe waere Rot nie erreichbar.
RED_FACTOR = 2.0

# Mindest-Betriebszeit der Berichtswoche, damit die Auffaelligkeitsrate
# ueberhaupt gebildet wird (sonst NaN = nicht bewertet).
MIN_RATE_HOURS = 5.0

# Mindest-Betriebszeit der Berichtswoche fuer eine Bewertung ueberhaupt.
# Darunter stehen die Kennzahlen auf einer Handvoll Bins - der Bericht weist
# die Messwerte dann aus, bewertet sie aber nicht.
MIN_WEEK_OPERATING_HOURS = 2.0

# ------------------------------------------------------------- Messstellen ---
# Bezeichnungen wie im bestehenden Bericht und im Grafana-Dashboard:
# "Bank 1 / Bank 2" bleibt der fuehrende Begriff. Die drei Messstellen je Bank
# werden nach Einbauposition benannt.
#
# ACHTUNG - noch fachlich zu bestaetigen:
#   Die Zuordnung sensor_0/1/2 -> vorne/Mitte/hinten ist eine Annahme. Die
#   Dokumentation nennt nur die Bank-Zugehoerigkeit, nicht die Einbauposition.
#   Falls die Reihenfolge anders ist, hier tauschen - sonst nirgends.
SENSOR_LABELS = {
    "temp_sensor_0": ("Bank 1", "vorne"),
    "temp_sensor_1": ("Bank 1", "Mitte"),
    "temp_sensor_2": ("Bank 1", "hinten"),
    "temp_sensor_3": ("Bank 2", "vorne"),
    "temp_sensor_4": ("Bank 2", "Mitte"),
    "temp_sensor_5": ("Bank 2", "hinten"),
    "temp_sensor_6": ("Bank 1", "Stauklappe"),
    "temp_sensor_7": ("Bank 2", "Stauklappe"),
}
BANK1_TEMPS = ["temp_sensor_0", "temp_sensor_1", "temp_sensor_2"]
BANK2_TEMPS = ["temp_sensor_3", "temp_sensor_4", "temp_sensor_5"]
FLAP_TEMPS  = ["temp_sensor_6", "temp_sensor_7"]
PRESSURES   = ["abgas_1_value", "abgas_2_value"]

# Plausibilitätsgrenzen für die Bereichsprüfung (Konzept 10.) - fachlich zu prüfen
# Plausibilitaetsgrenzen der Bereichspruefung.
#
# Temperatur: Die Thermoelemente sind laut Herstellerangebot (HAIVEMIND,
# 16.12.2025) auf -200 bis +350 °C spezifiziert. Die frueheren Grenzen
# (-40/+900) stammten aus der Annahme, es handle sich um Abgastemperaturen im
# Stroemungskanal - das trifft nicht zu.
#
# Druck: Die Einheit ist UNBEKANNT (Rohwerte). Gemessen wurden im Stillstand
# Werte nahe null und beim Anlassen ~13.000. Die frueher angesetzte Obergrenze
# von 6000 hat genau die echten Ereignisse als "Sensorfehler" verworfen. Bis der
# Hersteller die Skalierung nennt, ist die Grenze bewusst sehr weit - sie faengt
# nur noch offensichtlichen Unsinn ab.
RANGE_LIMITS = {
    "temp": (-200.0, 350.0),
    "pressure": (-5000.0, 100000.0),
}

# Ab welchem Abgasgegendruck gilt der Motor als laufend.
#
# Es gibt kein Betriebszustandssignal. Der Abgasgegendruck ist der beste Ersatz,
# weil er nur bei laufendem Motor entsteht - anders als die Temperatur, die nach
# dem Abstellen noch lange nachlaeuft.
#
# Geeicht am 28.09.2026 mit check_signals.py ueber vier Wochen:
#
#   CAR_002 (Box sendet durchgehend): Schwelle 1142 -> 11,1 h/Woche
#   CAR_001 (Box sendet nur bei Betrieb): 531 Bins  -> 11,1 h/Woche
#
# Zwei unabhaengige Verfahren, dasselbe Ergebnis, und es deckt sich mit der
# Erfahrung aus dem Betrieb (10-12 h/Woche). Der Wert steht je Fahrzeug in
# VEHICLES, weil die Drucksensoren unterschiedliche Nullpunkte haben:
# CAR_001 ruht bei +1513, CAR_002 bei -58.
DEFAULT_OPERATING_THRESHOLD = 1150

# Fallback, falls ein Fahrzeug kein "continuous" gesetzt hat: unterhalb dieses
# Belegungsgrades gilt die Box als "sendet nur bei Betrieb". Die Automatik ist
# bewusst nur Rueckfallebene - sie kann bei verstreuten Einsaetzen danebenliegen.
# Massgeblich ist das Feld "continuous" je Fahrzeug, ablesbar mit check_signals.py.
OCCUPANCY_OPERATING_LIMIT = 0.50

# Fehlerwerte der Sensorik. Ein Thermoelement meldet Fuehlerbruch nicht als
# Luecke, sondern mit einem Sentinel - gemessen wurde -999. Solche Werte muessen
# vor jeder Rechnung zu NaN werden, sonst wandern sie in Mittelwerte und
# Spreizungen.
SENTINEL_VALUES = (-999.0, -9999.0, 999.9)

# --------------------------------------------------------------- Fahrzeuge ---
VEHICLES = [
    {
        "car_id": "CAR_001",
        "name": "Fahrzeug 1",
        "plate": "Y-23-6862",
        "pressure_threshold": 2000,
        # Box sendet nur bei laufendem Motor (Belegung 7 %), gemessen 28.09.2026.
        # Jeder vorhandene Bin ist damit bereits Betrieb.
        "continuous": False,
        # Ruheniveau +1513 - nur relevant, falls die Box auf Dauerbetrieb
        # umgestellt wird
        "operating_threshold": 2700,
    },
    {
        "car_id": "CAR_002",
        "name": "Fahrzeug 2",
        "plate": "Y-49-3779",
        "pressure_threshold": 2200,
        # Box sendet durchgehend (Belegung 100 %), gemessen 28.09.2026.
        # Betrieb muss deshalb ueber den Abgasgegendruck erkannt werden.
        "continuous": True,
        # Ruheniveau -58, geeicht auf 11,1 h/Woche
        "operating_threshold": 1150,
    },
]

# ------------------------------------------------------------------ Farben ---
# Geprüft gegen weiße Druckfläche, alle Paare, inkl. Farbfehlsichtigkeit
# (Konzept 13.2). Nicht ohne erneute Prüfung ändern.
SERIES = ["#2a78d6", "#eb6834", "#4a3aa7"]      # Messstelle A / B / C
BANK_COLOR = {1: "#2a78d6", 2: "#eb6834"}        # Bank 1 / Bank 2

STATUS = {
    "gruen":  {"color": "#0ca30c", "label": "Unauffällig",                            "rank": 0},
    "gelb":   {"color": "#fab219", "label": "Beobachtung empfohlen",                  "rank": 1},
    "orange": {"color": "#ec835a", "label": "Auffälligkeit – Prüfung empfohlen",      "rank": 2},
    "rot":    {"color": "#d03b3b", "label": "Deutliche Auffälligkeit – zeitnah prüfen","rank": 3},
}

INK_PRIMARY   = "#111111"
INK_SECONDARY = "#4a4a48"
INK_MUTED     = "#7a7a76"
RULE          = "#d8d8d4"
SURFACE_SOFT  = "#f4f4f1"
BAND_NORMAL   = "#e6e6e2"   # graues Normalbereichsband
