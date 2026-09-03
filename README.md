# Wöchentlicher Analysebericht – Motorüberwachung

Ablösung des bisherigen `report.py` nach Konzept v2. Läuft produktiv gegen
InfluxDB oder für Layouttests gegen synthetische Daten.

```bash
python3 run_report.py                 # produktiv: Bericht + Versand
python3 run_report.py --no-mail       # nur PDF
python3 run_report.py --mock --no-mail  # ohne Datenbank, für Layouttests
```

## Aufbau

| Datei | Inhalt |
|---|---|
| `run_report.py` | **Einstiegspunkt für den Cron-Job** |
| `config.py` | Fahrzeuge, Messstellen, Toleranzmodell, Farben, Serverpfade |
| `influx_source.py` | InfluxDB-Abfrage (produktive Datenquelle) |
| `fake_data.py` | synthetische Daten für Layouttests (`--mock`) |
| `analysis.py` | Kennzahlen, Vorfälle, Referenz, Toleranzbänder, Ampel |
| `plots.py` | alle Diagramme |
| `pdf.py` | Layoutbausteine |
| `build_report.py` | Seitenaufbau |
| `mailer.py` | Versand, Zugangsdaten aus `.env` |
| `update.sh` | Deployment auf den Server |

## Installation auf dem Server

Ohne SSH, ein Befehl in der VNC-Konsole:

```bash
cd /home/princess_donut/influx && \
curl -fsSLO https://raw.githubusercontent.com/MatthiasGisch/vogtmann_report/main/update.sh && \
bash update.sh
```

Danach ist jedes weitere Update nur noch `bash update.sh`. Das Skript lädt alle
Module, prüft die Pakete, legt `berichte/` und eine leere `.env` an – und rührt
eine vorhandene `.env` nicht an.

Reihenfolge für die Inbetriebnahme:

```bash
python3 influx_source.py          # 1) Verbindung und SQL prüfen
python3 run_report.py --no-mail   # 2) PDF erzeugen, noch nicht versenden
python3 run_report.py             # 3) Testlauf mit Versand
```

Cron-Eintrag (ersetzt die alte Zeile):

```
0 6 * * 1 cd /home/princess_donut/influx && /usr/bin/python3 run_report.py >> /home/princess_donut/influx/report.log 2>&1
```

Es werden **sechs** Wochen geladen: Berichtswoche, Vorwoche und vier
Referenzwochen. Mit fünf bliebe die älteste Referenzwoche leer.

`run_report.py` liefert einen Rückgabewert: `0` alles gut, `1` Bericht erstellt
aber Versand fehlgeschlagen, `2` kein Bericht. Damit lässt sich der Lauf
überwachen, statt das Log lesen zu müssen.

## Gemessen

| | |
|---|---|
| Laufzeit (2 Fahrzeuge, 6 Wochen) | ~2 s |
| Spitzenspeicher | ~145 MB |
| Neue Pakete gegenüber heute | keine (numpy kommt mit pandas) |

## Datenformat

Die Abfrage liefert je Fahrzeug einen DataFrame:

- Index `ts`: 5-Minuten-Bins in **Ortszeit**, lückenhaft (fehlender Bin = Motor stand)
- `samples` – `COUNT(*)` je Bin, entspricht den Laufsekunden
- je Messstelle `<sensor>_mean`, `_max`, `_min`, `_std`
- `abgas_1_value_mean/_max`, `abgas_2_value_mean/_max`

Die sechs Wochen werden **wochenweise** abgefragt, nicht in einem Rutsch: eine
einzelne Abfrage über sechs Wochen sekündlicher Rohdaten fasst sehr viele
Parquet-Dateien an und kann am `--query-file-limit` des Containers scheitern.

## Bezeichnungen

Übernommen aus dem bestehenden `report.py` und der Doku:

| Feld | Im Bericht |
|---|---|
| `temp_sensor_0/1/2` | Bank 1 – vorne / Mitte / hinten |
| `temp_sensor_3/4/5` | Bank 2 – vorne / Mitte / hinten |
| `temp_sensor_6/7` | Stauklappe Bank 1 / Bank 2 |
| `abgas_1_value/abgas_2_value` | Abgasgegendruck Bank 1 / Bank 2 |

Alles an einer Stelle: `SENSOR_LABELS` in `config.py`.

## Bewusst offene Stellen

- **Einbauposition** – die Zuordnung `temp_sensor_0/1/2` → vorne / Mitte / hinten
  ist eine Annahme (die Doku nennt nur die Bank). Falls anders: eine Zeile ändern
- **Toleranzbänder Typ B**: das absolute Mindestband (`MIN_BAND_K = 5 K`)
  dominiert derzeit bei allen Differenzkennzahlen über die 10-/15-%-Regel –
  gemessen liegt die Alarmschwelle dadurch bei ~18 K statt bei ~15 K. Nach der
  ersten echten Referenzphase gehört das nachjustiert
- **`RANGE_LIMITS`** – Plausibilitätsgrenzen fachlich prüfen
- **`MIN_ABS_BAND`** – absolute Mindestbänder je Kennzahl fachlich prüfen
- **Referenzphase** wird bei jedem Lauf neu berechnet; sie sollte einmal
  bestimmt, festgeschrieben und versioniert werden (Konzept 8.3)
- **`influx_source.py` ist noch nie gegen die echte Datenbank gelaufen.** SQL und
  Funktionsnamen sind DataFusion-Standard, aber ungeprüft – deshalb der
  Selbsttest `python3 influx_source.py`, der Token, Verbindung, Spalten,
  `DATE_BIN`/`STDDEV` und den Datenumfang einzeln prüft
