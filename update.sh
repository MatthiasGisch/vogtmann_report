#!/usr/bin/env bash
# Aktualisiert den Analysebericht auf dem Server.
#
# Holt EIN Archiv statt zehn Einzeldateien: ein Abbruch mitten im Download
# hinterlaesst so keine halb aktualisierte Installation, und es ist ein
# einziger Request statt zehn.
#
# Erstinstallation (einmal in der VNC-Konsole eintippen):
#
#   cd /home/princess_donut/influx && \
#   curl -fsSL https://raw.githubusercontent.com/MatthiasGisch/vogtmann_report/main/update.sh -o update.sh && \
#   bash update.sh
#
# Danach genuegt:  bash update.sh
#
# Trockenlauf (zeigt nur, was passieren wuerde, aendert nichts):
#   bash update.sh --dry-run
#
# Das Skript schreibt ausschliesslich in $DIR und /tmp. Es fuehrt keine
# Datenbankbefehle aus, loescht keine Bestandsdateien und fasst weder
# compose.yaml noch admin-token.json noch die InfluxDB-Volumes an.
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1 && echo "TROCKENLAUF — es wird nichts geaendert."
run() { if [ "$DRY" = "1" ]; then echo "    [wuerde ausfuehren] $*"; else "$@"; fi; }

REPO_USER="${REPO_USER:-MatthiasGisch}"
REPO_NAME="${REPO_NAME:-vogtmann_report}"
BRANCH="${BRANCH:-main}"
TARBALL="${TARBALL:-https://codeload.github.com/$REPO_USER/$REPO_NAME/tar.gz/refs/heads/$BRANCH}"
DIR="${VOGTMANN_DIR:-/home/princess_donut/influx}"

# Diese Dateien werden aus dem Archiv uebernommen. Alles andere im Repo
# (z.B. das alte report.py) bleibt unangetastet.
FILES=(
  config.py
  analysis.py
  plots.py
  pdf.py
  build_report.py
  influx_source.py
  fake_data.py
  mailer.py
  run_report.py
)

echo "Zielverzeichnis: $DIR"
run mkdir -p "$DIR/berichte"

# --------------------------------------------------------- Abhaengigkeiten --
echo
echo "Pruefe Python-Pakete …"
MISSING=()
for mod in pandas numpy matplotlib reportlab influxdb_client_3; do
  python3 -c "import $mod" 2>/dev/null || MISSING+=("$mod")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  # Nur die tatsaechlich fehlenden Pakete, nie pauschal alle: ein
  # "pip3 install pandas" auf einem System, das pandas schon hat, kann
  # Abhaengigkeiten hochziehen und andere Python-Anwendungen auf dem Server
  # beschaedigen. Kein -U, also werden vorhandene Versionen nicht angefasst.
  PKGS=()
  for m in "${MISSING[@]}"; do
    case "$m" in
      influxdb_client_3) PKGS+=("influxdb3-python") ;;
      *)                 PKGS+=("$m") ;;
    esac
  done
  echo "  Fehlend: ${MISSING[*]}"
  echo "  Installiere: ${PKGS[*]}"
  run pip3 install "${PKGS[@]}" --break-system-packages
else
  echo "  Alle vorhanden — es wird nichts installiert."
fi

# ------------------------------------------------------------- Archiv ------
echo
echo "Lade Archiv …"
TMP="$(mktemp -d)"
[ -n "$TMP" ] && [ -d "$TMP" ] || { echo "  FEHLER: kein Temporaerverzeichnis"; exit 1; }
# Absicherung: nur ein echtes mktemp-Verzeichnis unter /tmp wird geloescht
trap '[ -n "${TMP:-}" ] && [ -d "$TMP" ] && case "$TMP" in /tmp/*) rm -rf "$TMP";; esac' EXIT

if ! curl -fsSL "$TARBALL" -o "$TMP/src.tar.gz"; then
  echo "  FEHLER: Archiv nicht ladbar."
  echo "  Geprueft wurde: $TARBALL"
  echo "  Moegliche Ursachen: Repo ist privat, Branch heisst nicht '$BRANCH',"
  echo "  oder der Server kommt nicht ins Netz."
  exit 1
fi
tar -xzf "$TMP/src.tar.gz" -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name "$REPO_NAME-*" | head -1)"
[ -n "$SRC" ] || { echo "  FEHLER: unerwartete Archivstruktur"; exit 1; }

# Erst pruefen, ob alles da ist, dann erst ersetzen.
for f in "${FILES[@]}"; do
  [ -f "$SRC/$f" ] || { echo "  FEHLER: $f fehlt im Archiv — Abbruch, nichts geaendert."; exit 1; }
done
for f in "${FILES[@]}"; do
  if [ -f "$DIR/$f" ] && cmp -s "$SRC/$f" "$DIR/$f"; then
    printf '  %-20s unveraendert\n' "$f"
  else
    run cp "$SRC/$f" "$DIR/$f"
    printf '  %-20s aktualisiert\n' "$f"
  fi
done

# update.sh zuletzt und separat: ein Skript, das sich waehrend der eigenen
# Ausfuehrung ersetzt, kann still abbrechen.
if [ -f "$SRC/update.sh" ] && ! cmp -s "$SRC/update.sh" "$DIR/update.sh"; then
  run cp "$SRC/update.sh" "$DIR/update.sh.new"
  echo "  update.sh: neue Fassung liegt als update.sh.new bereit"
  echo "             uebernehmen mit:  mv $DIR/update.sh.new $DIR/update.sh"
fi

# .env wird nie angefasst - dort stehen die Zugangsdaten.
if [ ! -f "$DIR/.env" ]; then
  if [ "$DRY" = "1" ]; then
    echo "    [wuerde anlegen] $DIR/.env"
  else
    cat > "$DIR/.env" <<'ENV'
SMTP_USER=
SMTP_PASSWORD=
EMAIL_FROM=
EMAIL_TO=
ENV
  fi
  run chmod 600 "$DIR/.env"
  echo
  echo "  .env angelegt — bitte ausfuellen:  nano $DIR/.env"
else
  echo "  .env vorhanden, unveraendert."
fi

cat <<TXT

Fertig. Naechste Schritte:

  1) Verbindung und SQL pruefen
       cd $DIR && python3 influx_source.py

  2) Bericht ohne Versand erzeugen
       python3 run_report.py --no-mail && ls -la $DIR/berichte/

  3) Testlauf mit Versand
       python3 run_report.py

  4) Cron umstellen (crontab -e), alte Zeile ersetzen durch:
       0 6 * * 1 cd $DIR && /usr/bin/python3 run_report.py >> $DIR/report.log 2>&1

Layouttest ohne Datenbank:  python3 run_report.py --mock --no-mail
TXT
