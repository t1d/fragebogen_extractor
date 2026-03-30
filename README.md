# Fragebogen-Extraktor v2 – Psychiatrie St.Gallen

Extrahiert automatisch alle Antworten aus gescannten Patientenzufriedenheits-Fragebögen (PDF)
via Claude Vision API. Ausgabe als **JSON** (pro Fragebogen) und **CSV** (kumulativ, alle Bögen).

---

## Voraussetzungen

- Python 3.11+
- Anthropic API Key ([console.anthropic.com](https://console.anthropic.com))

---

## Installation

```bash
# 1. In den Ordner wechseln
cd fragebogen_extractor

# 2. Abhängigkeiten installieren
pip install -r requirements.txt

# 3. API Key setzen (einmalig pro Terminal-Session)
# Windows (PowerShell):
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# macOS / Linux:
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Verwendung

```bash
# Einzelner Fragebogen → JSON + CSV im aktuellen Ordner
python fragebogen_extractor.py fragebogen_test.pdf

# Mit eigenem Ausgabe-Ordner
python fragebogen_extractor.py fragebogen_test.pdf ./output

# Mehrere Fragebögen in einer Schleife (PowerShell)
Get-ChildItem *.pdf | ForEach-Object {
    python fragebogen_extractor.py $_.FullName ./output
}

# Mehrere Fragebögen in einer Schleife (bash)
for f in *.pdf; do python fragebogen_extractor.py "$f" ./output; done
```

---

## Ausgabe

### JSON (pro Fragebogen)
```json
{
  "meta": {
    "geschlecht": "männlich",
    "alter": 69,
    "anzahl_aufenthalte": 1,
    "aufenthaltsdauer": "15-30_tage"
  },
  "gesamturteil": {
    "positiv": "...",
    "negativ": null
  },
  "aufnahme_eintritt": { "f1": 5, "f2": 5 },
  "aufenthalt": { "f3": 7, "f4": 4, ... },
  "zusammenarbeit": { "f12": 6, "f13": 6, ... },
  "behandlung": { "f17": 7, "f18": 7, ... },
  "austritt": { "f22": 6, "f23": 6, "f24": "viel_besser", ... },
  "zufriedenheit_einrichtung": { "zimmer": 7, "essen": 6, ... },
  "abschluss": { "gesamtzufriedenheit": 6, "weiterempfehlung": 6 }
}
```

### CSV (kumulativ – alle Fragebögen in einer Datei)
`fragebogen_sammlung.csv` wird automatisch erweitert, sodass alle Bögen
in einer Datei gesammelt werden. Ideal für Excel / Power BI.

Spalten: `quelldatei`, `extrahiert_am`, `geschlecht`, `alter`, `anzahl_aufenthalte`,
`aufenthaltsdauer`, `gesamturteil_positiv`, `gesamturteil_negativ`,
`f1` bis `f24`, `zuf_zimmer`, `zuf_essen`, `zuf_gemeinschaftseinrichtungen`,
`zuf_restaurant_cafeteria`, `zuf_freizeitmoeglichkeiten`,
`gesamtzufriedenheit`, `weiterempfehlung`

---

## Hinweise zur Scanqualität

- **Empfohlene Auflösung:** min. 200 DPI (300 DPI optimal)
- **Format:** PDF (auch mehrseitig)
- Das Skript rendert intern mit 200 DPI – bei sehr schlechten Scans auf 300 DPI erhöhen
  (Variable `dpi` in `pdf_to_base64_images()`)

---

## Datenschutz

- Es werden **keine Daten gespeichert** – die API-Anfragen gehen an Anthropic
- Für Produktion: Anthropic Business Associate Agreement (BAA) prüfen
- Alternativ: Azure AI Document Intelligence mit EU-Datenresidenz

---

## Nächste Schritte (optional)

- SurveyMonkey API-Integration (POST /surveys/{id}/responses)
- Automatischer SharePoint-Watch-Ordner (neue PDFs werden automatisch verarbeitet)
- Power BI Dashboard auf Basis der CSV
