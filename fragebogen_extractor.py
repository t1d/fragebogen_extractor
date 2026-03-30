#!/usr/bin/env python3
"""
Fragebogen-Extraktor – Psychiatrie St.Gallen (v2)
Liest gescannte Patientenzufriedenheits-Fragebögen (PDF) aus
und extrahiert alle Antworten via Claude Vision API.
"""

import anthropic
import base64
import json
import csv
import sys
import os
from pathlib import Path
from datetime import datetime

try:
    import fitz  # PyMuPDF
except ImportError:
    print("❌  PyMuPDF nicht installiert. Bitte: pip install pymupdf")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────
# PASS 1: Visuelles Lesen – was steht/ist markiert auf dem Bogen?
# ──────────────────────────────────────────────────────────────

SYSTEM_VISUAL_READER = """
Du bist ein präziser Dokumentenanalyst. Du analysierst ausgefüllte Papierfragebögen
der Psychiatrie St.Gallen. Deine Aufgabe ist es, exakt und vollständig zu beschreiben,
was du siehst – ohne zu interpretieren oder zu schlussfolgern.

Regeln:
- Beschreibe Kreuzchen/Häkchen in Checkboxen EXAKT nach Position (von links gezählt)
- Bei Likert-Skalen: Zähle Kästchen strikt von links = 1 bis rechts = max
- Handschrift transkribieren so genau wie möglich, auch bei schlechter Lesbarkeit
- Unsicheres mit [?] markieren
- Leere Felder explizit als "leer" nennen
"""

PROMPT_PAGE1_DESCRIBE = """
Analysiere diese Seite des Fragebogens (Titelseite / Gesamturteil-Seite).

Beschreibe genau:

1. GESCHLECHT: Welches der drei Kästchen (männlich / weiblich / divers) ist angekreuzt?

2. ALTER: Was steht handgeschrieben im Altersfeld? Transkribiere die Zahl.

3. ANZAHL AUFENTHALTE: Was steht handgeschrieben im Feld "Wie oft waren Sie schon..."?

4. AUFENTHALTSDAUER: Welches der vier Kästchen ist angekreuzt?
   (1-5 Tage / 6-14 Tage / 15-30 Tage / mehr als 30 Tage)

5. GESAMTURTEIL POSITIV: Was steht handgeschrieben im oberen grossen Textfeld
   ("Was fanden Sie besonders gut / positiv?")?

6. GESAMTURTEIL NEGATIV: Was steht handgeschrieben im unteren grossen Textfeld
   ("Was hat Sie an der Klinik gestört?")?

Antworte strukturiert mit Abschnitt für jeden Punkt.
"""

PROMPT_PAGE2_DESCRIBE = """
Analysiere diese Seite des Fragebogens (Fragen 1–25).

Der Fragebogen hat zwei verschiedene Skalentypen:

TYP A – 7 Kästchen in einer Reihe:
□ □ □ □ □ □ □
1 2 3 4 5 6 7
Links = "Trifft überhaupt nicht zu" / "Sehr unzufrieden"
Rechts = "Trifft voll und ganz zu" / "Sehr zufrieden"
→ Zähle das angekreuzte Kästchen von LINKS (1=erstes, 7=letztes)

TYP B – 6 Kästchen + separates "Kein Kontakt/Therapeutenwechsel"-Feld (Fragen 9, 12–16):
□ □ □ □ □ □   □ Kein Kontakt
1 2 3 4 5 6
→ Zähle ebenso von links, oder "kein_kontakt" wenn das separate Feld markiert ist

Beschreibe für JEDE Frage einzeln:
- Fragenummer
- Welches Kästchen ist markiert (Position von links, oder leer, oder Sonderfeld)
- Falls unklar: Notiere deine Unsicherheit mit [?]

Gehe durch folgende Fragen:

AUFNAHME/EINTRITT (7er-Skala):
F1 – Situation im Aufnahmegespräch darlegen
F2 – Weiteres Vorgehen wurde erklärt

AUFENTHALT (7er-Skala):
F3 – Wesen der Krankheit erklärt
F4 – Verständliche Antworten auf Fragen
F5 – Einfluss auf Therapieplanung
F6 – Bewegungsfreiheit unnötig eingeschränkt [ACHTUNG: negativ formuliert]
F7 – Wirkungen/Nebenwirkungen Medikamente erklärt
F8 – Zusammenleben mit Mitpatienten hilfreich
F9 – Therapeutenwechsel gut vorbereitet (7er-Skala ODER "Kein Therapeutenwechsel")
F10 – Respektvolle Behandlung durch Personal
F11 – Therapieziele vereinbart

ZUSAMMENARBEIT (6er-Skala + Kein Kontakt):
F12 – Ärztin
F13 – Psychologin
F14 – Pflegebezugsperson
F15 – Sozialarbeiterin
F16 – Weitere Therapeutinnen

BEHANDLUNG (7er-Skala):
F17 – Körperliche Beschwerden medizinisch gut betreut
F18 – Einfluss auf medikamentöse Therapie
F19 – Keine Hemmungen, Fragen zu stellen [ACHTUNG: negativ formuliert]
F20 – Behandlung half beim Umgang mit Problemen
F21 – Zusammenarbeit mit Angehörigen (7er-Skala ODER "Keine Angehörigen")

AUSTRITT (7er-Skala):
F22 – Nachbetreuung gut organisiert
F23 – Vorbereitung auf Entlassung

F24 – ZUSTAND VERGLICHEN MIT EINTRITT:
Welches der 7 Kästchen ist angekreuzt?
(sehr viel besser / viel besser / besser / unverändert / schlechter / viel schlechter / sehr viel schlechter)
ODER: "Kann ich nicht beurteilen"

F25 – ZUFRIEDENHEIT MIT EINRICHTUNGEN (je 7er-Skala, 1=sehr unzufrieden, 7=sehr zufrieden):
- Zimmer
- Essen
- Gemeinschaftseinrichtungen
- Restaurant/Cafeteria
- Freizeitmöglichkeiten

GESAMTZUFRIEDENHEIT (7er-Skala, unter F25 links)
WEITEREMPFEHLUNG (7er-Skala, unter F25 rechts)
"""

# ──────────────────────────────────────────────────────────────
# PASS 2: Strukturierung der Beschreibung in JSON
# ──────────────────────────────────────────────────────────────

SYSTEM_EXTRACTOR = """
Du bist ein präziser Datenstrukturierer. Du erhältst eine detaillierte Beschreibung
eines ausgefüllten Fragebogens und konvertierst diese in ein valides JSON-Objekt.

Regeln:
- Gib NUR das JSON zurück, kein Text davor/danach, keine Markdown-Backticks
- Bei Unsicherheit: wähle den wahrscheinlichsten Wert und setze confidence auf "low"
- Leere/nicht ausgefüllte Felder: null
- Zahlen als Integer, nicht als String
"""

PROMPT_EXTRACT_JSON = """
Konvertiere diese Beschreibung eines ausgefüllten Fragebogens in folgendes JSON-Schema.
Gib NUR das JSON zurück.

Beschreibung:
{description}

JSON-Schema:
{{
  "meta": {{
    "geschlecht": "männlich" | "weiblich" | "divers" | null,
    "alter": <Integer> | null,
    "anzahl_aufenthalte": <Integer> | null,
    "aufenthaltsdauer": "1-5_tage" | "6-14_tage" | "15-30_tage" | "mehr_als_30_tage" | null
  }},
  "gesamturteil": {{
    "positiv": "<Freitext>" | null,
    "negativ": "<Freitext>" | null
  }},
  "aufnahme_eintritt": {{
    "f1": <1-7> | null,
    "f2": <1-7> | null
  }},
  "aufenthalt": {{
    "f3": <1-7> | null,
    "f4": <1-7> | null,
    "f5": <1-7> | null,
    "f6": <1-7> | null,
    "f7": <1-7> | null,
    "f8": <1-7> | null,
    "f9": <1-7> | "kein_therapeutenwechsel" | null,
    "f10": <1-7> | null,
    "f11": <1-7> | null
  }},
  "zusammenarbeit": {{
    "f12": <1-6> | "kein_kontakt" | null,
    "f13": <1-6> | "kein_kontakt" | null,
    "f14": <1-6> | "kein_kontakt" | null,
    "f15": <1-6> | "kein_kontakt" | null,
    "f16": <1-6> | "kein_kontakt" | null
  }},
  "behandlung": {{
    "f17": <1-7> | null,
    "f18": <1-7> | null,
    "f19": <1-7> | null,
    "f20": <1-7> | null,
    "f21": <1-7> | "keine_angehoerigen" | null
  }},
  "austritt": {{
    "f22": <1-7> | null,
    "f23": <1-7> | null,
    "f24": "sehr_viel_besser" | "viel_besser" | "besser" | "unveraendert" | "schlechter" | "viel_schlechter" | "sehr_viel_schlechter" | "kann_nicht_beurteilen" | null
  }},
  "zufriedenheit_einrichtung": {{
    "zimmer": <1-7> | null,
    "essen": <1-7> | null,
    "gemeinschaftseinrichtungen": <1-7> | null,
    "restaurant_cafeteria": <1-7> | null,
    "freizeitmoeglichkeiten": <1-7> | null
  }},
  "abschluss": {{
    "gesamtzufriedenheit": <1-7> | null,
    "weiterempfehlung": <1-7> | null
  }},
  "extraction_meta": {{
    "confidence": "high" | "medium" | "low",
    "unsichere_felder": ["f6", "f19", ...],
    "hinweise": "<optionale Notizen zu Besonderheiten>"
  }}
}}
"""


def pdf_to_base64_images(pdf_path: str, dpi: int = 300) -> list[dict]:
    """Konvertiert PDF-Seiten in base64-kodierte JPEG-Bilder bei hoher Auflösung."""
    doc = fitz.open(pdf_path)
    images = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("jpeg", jpg_quality=95)
        img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        images.append({
            "b64": img_b64,
            "content_block": {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": img_b64
                }
            }
        })
        size_kb = len(img_bytes) // 1024
        print(f"   Seite {page_num + 1}/{len(doc)} gerendert bei {dpi} DPI ({size_kb} KB)")
    
    doc.close()
    return images


def call_claude(client: anthropic.Anthropic, system: str, messages: list) -> str:
    """Ruft Claude API auf und gibt den Text-Response zurück."""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=system,
        messages=messages
    )
    return response.content[0].text.strip()


def pass1_describe(client: anthropic.Anthropic, images: list) -> str:
    """
    Erster Pass: Lässt Claude den Fragebogen visuell beschreiben.
    Seite 1 und Seite 2 werden separat analysiert.
    """
    descriptions = []
    
    # Seite 1 (Titelseite / Gesamturteil)
    print("   → Pass 1a: Beschreibe Seite 1 (Titelseite)...")
    page1_img = images[0]["content_block"] if len(images) >= 1 else None
    
    if page1_img:
        resp1 = call_claude(
            client,
            SYSTEM_VISUAL_READER,
            [{"role": "user", "content": [
                page1_img,
                {"type": "text", "text": PROMPT_PAGE1_DESCRIBE}
            ]}]
        )
        descriptions.append(f"=== SEITE 1 (Titelseite) ===\n{resp1}")
    
    # Seite 2 (Fragen 1–25) – bei 4-seitigem PDF sind das Seiten 2 und 3 zusammen
    # Der Fragebogen ist ein Faltblatt: 4 Druckseiten = 2 PDF-Seiten
    page2_img = images[1]["content_block"] if len(images) >= 2 else None
    
    if page2_img:
        print("   → Pass 1b: Beschreibe Seite 2 (Fragen 1–25)...")
        resp2 = call_claude(
            client,
            SYSTEM_VISUAL_READER,
            [{"role": "user", "content": [
                page2_img,
                {"type": "text", "text": PROMPT_PAGE2_DESCRIBE}
            ]}]
        )
        descriptions.append(f"=== SEITE 2 (Fragen 1–25) ===\n{resp2}")
    
    return "\n\n".join(descriptions)


def pass2_structure(client: anthropic.Anthropic, description: str) -> dict:
    """
    Zweiter Pass: Konvertiert die Beschreibung in strukturiertes JSON.
    """
    print("   → Pass 2: Strukturiere in JSON...")
    
    prompt = PROMPT_EXTRACT_JSON.format(description=description)
    
    raw = call_claude(
        client,
        SYSTEM_EXTRACTOR,
        [{"role": "user", "content": prompt}]
    )
    
    # Bereinige mögliche Markdown-Wrapper
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    
    return json.loads(raw)


def validate_data(data: dict) -> list[str]:
    """Prüft ob extrahierte Werte im erlaubten Bereich liegen."""
    warnings = []
    
    seven_scale_fields = [
        ("aufnahme_eintritt", ["f1", "f2"]),
        ("aufenthalt", ["f3", "f4", "f5", "f6", "f7", "f8", "f10", "f11"]),
        ("behandlung", ["f17", "f18", "f19", "f20"]),
        ("austritt", ["f22", "f23"]),
        ("zufriedenheit_einrichtung", ["zimmer", "essen", "gemeinschaftseinrichtungen",
                                        "restaurant_cafeteria", "freizeitmoeglichkeiten"]),
        ("abschluss", ["gesamtzufriedenheit", "weiterempfehlung"]),
    ]
    
    for section, fields in seven_scale_fields:
        for field in fields:
            val = data.get(section, {}).get(field)
            if val is not None and isinstance(val, int) and not (1 <= val <= 7):
                warnings.append(f"{section}.{field} = {val} (ausserhalb 1–7)")
    
    six_scale = ["f12", "f13", "f14", "f15", "f16"]
    for field in six_scale:
        val = data.get("zusammenarbeit", {}).get(field)
        if val is not None and isinstance(val, int) and not (1 <= val <= 6):
            warnings.append(f"zusammenarbeit.{field} = {val} (ausserhalb 1–6)")
    
    return warnings


def extract_fragebogen(pdf_path: str) -> dict:
    """Hauptfunktion: PDF → JSON via zweistufiger Extraktion."""
    
    print(f"\n📄  Lese PDF: {pdf_path}")
    images = pdf_to_base64_images(pdf_path, dpi=300)
    
    client = anthropic.Anthropic()
    
    print("\n🔍  Pass 1: Visuelles Lesen...")
    description = pass1_describe(client, images)

    print("\n🧩  Pass 2: Strukturierung...")
    data = pass2_structure(client, description)
    
    # Validierung
    warnings = validate_data(data)
    if warnings:
        print(f"\n⚠️   Validierungshinweise:")
        for w in warnings:
            print(f"   • {w}")
        data["_validation_warnings"] = warnings
    
    # Rohe Beschreibung anhängen (für Audit/Debugging)
    data["_raw_description"] = description
    
    confidence = data.get("extraction_meta", {}).get("confidence", "unknown")
    unsicher = data.get("extraction_meta", {}).get("unsichere_felder", [])
    print(f"\n✅  Extraktion abgeschlossen (Konfidenz: {confidence})")
    if unsicher:
        print(f"   Unsichere Felder: {', '.join(unsicher)}")
    
    return data


def flatten_for_csv(data: dict, source_file: str) -> dict:
    """Flacht das verschachtelte JSON für CSV-Export ab."""
    row = {
        "quelldatei": Path(source_file).name,
        "extrahiert_am": datetime.now().isoformat(timespec="seconds"),
        "konfidenz": data.get("extraction_meta", {}).get("confidence"),
        "hinweise": data.get("extraction_meta", {}).get("hinweise"),
        
        # Meta
        "geschlecht": data.get("meta", {}).get("geschlecht"),
        "alter": data.get("meta", {}).get("alter"),
        "anzahl_aufenthalte": data.get("meta", {}).get("anzahl_aufenthalte"),
        "aufenthaltsdauer": data.get("meta", {}).get("aufenthaltsdauer"),
        
        # Gesamturteil
        "gesamturteil_positiv": data.get("gesamturteil", {}).get("positiv"),
        "gesamturteil_negativ": data.get("gesamturteil", {}).get("negativ"),
    }
    
    # Alle Fragen-Sektionen
    for section_key, frage_keys in [
        ("aufnahme_eintritt", ["f1", "f2"]),
        ("aufenthalt",        ["f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11"]),
        ("zusammenarbeit",    ["f12", "f13", "f14", "f15", "f16"]),
        ("behandlung",        ["f17", "f18", "f19", "f20", "f21"]),
        ("austritt",          ["f22", "f23", "f24"]),
    ]:
        section = data.get(section_key, {})
        for k in frage_keys:
            row[k] = section.get(k)
    
    # Zufriedenheit Einrichtung
    zuf = data.get("zufriedenheit_einrichtung", {})
    for key in ["zimmer", "essen", "gemeinschaftseinrichtungen",
                "restaurant_cafeteria", "freizeitmoeglichkeiten"]:
        row[f"zuf_{key}"] = zuf.get(key)
    
    # Abschluss
    row["gesamtzufriedenheit"] = data.get("abschluss", {}).get("gesamtzufriedenheit")
    row["weiterempfehlung"] = data.get("abschluss", {}).get("weiterempfehlung")
    
    return row


def save_json(data: dict, output_path: str):
    # Keine interne _raw_description im JSON (zu gross für Übersicht)
    export = {k: v for k, v in data.items() if not k.startswith("_")}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"💾  JSON gespeichert: {output_path}")


def save_csv(row: dict, csv_path: str):
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()), delimiter=";")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"💾  CSV ergänzt: {csv_path}")


def save_description(description: str, output_path: str):
    """Speichert die Rohbeschreibung für manuelles Audit."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(description)
    print(f"💾  Beschreibung gespeichert: {output_path}")


def print_summary(data: dict):
    print("\n" + "═" * 55)
    print("  EXTRAKTIONS-ZUSAMMENFASSUNG")
    print("═" * 55)
    meta = data.get("meta", {})
    print(f"  Geschlecht:        {meta.get('geschlecht', '–')}")
    print(f"  Alter:             {meta.get('alter', '–')} Jahre")
    print(f"  Aufenthalte:       {meta.get('anzahl_aufenthalte', '–')} Mal")
    print(f"  Aufenthaltsdauer:  {meta.get('aufenthaltsdauer', '–')}")
    
    abschluss = data.get("abschluss", {})
    print(f"\n  Gesamtzufriedenheit: {abschluss.get('gesamtzufriedenheit', '–')} / 7")
    print(f"  Weiterempfehlung:    {abschluss.get('weiterempfehlung', '–')} / 7")
    
    f24 = data.get("austritt", {}).get("f24")
    if f24:
        print(f"  Zustand (F24):       {f24}")
    
    gt = data.get("gesamturteil", {})
    if gt.get("positiv"):
        text = str(gt["positiv"])
        print(f"\n  ✓ Positiv:  {text[:70]}{'…' if len(text) > 70 else ''}")
    if gt.get("negativ"):
        text = str(gt["negativ"])
        print(f"  ✗ Negativ:  {text[:70]}{'…' if len(text) > 70 else ''}")
    
    em = data.get("extraction_meta", {})
    print(f"\n  Konfidenz:  {em.get('confidence', '–')}")
    if em.get("unsichere_felder"):
        print(f"  Unsicher:   {', '.join(em['unsichere_felder'])}")
    if em.get("hinweise"):
        print(f"  Hinweise:   {em['hinweise']}")
    print("═" * 55)


# ──────────────────────────────────────────────
# Batch-Verarbeitung
# ──────────────────────────────────────────────

def collect_pdfs(inputs: list[str]) -> list[Path]:
    """
    Sammelt alle PDF-Dateien aus den angegebenen Pfaden.
    Akzeptiert: einzelne Dateien, mehrere Dateien, Ordner (rekursiv).
    """
    pdfs = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            found = sorted(p.rglob("*.pdf"))
            if not found:
                print(f"⚠️   Keine PDFs gefunden in: {p}")
            else:
                print(f"📂  Ordner: {p} → {len(found)} PDF(s) gefunden")
                pdfs.extend(found)
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdfs.append(p)
        elif not p.exists():
            print(f"⚠️   Nicht gefunden, wird übersprungen: {p}")
        else:
            print(f"⚠️   Kein PDF, wird übersprungen: {p}")
    return pdfs


def process_one(pdf_path: Path, output_dir: str, csv_path: str) -> bool:
    """Verarbeitet einen einzelnen Fragebogen. Gibt True bei Erfolg zurück."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = pdf_path.stem
    json_path = os.path.join(output_dir, f"{stem}_{ts}.json")
    desc_path = os.path.join(output_dir, f"{stem}_{ts}_beschreibung.txt")

    try:
        data = extract_fragebogen(str(pdf_path))
        print_summary(data)
        save_json(data, json_path)
        save_csv(flatten_for_csv(data, str(pdf_path)), csv_path)
        if "_raw_description" in data:
            save_description(data["_raw_description"], desc_path)
        return True

    except json.JSONDecodeError as e:
        print(f"  ❌  JSON-Parsing fehlgeschlagen: {e}")
        return False
    except Exception as e:
        print(f"  ❌  Fehler: {e}")
        return False


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

USAGE = """
Fragebogen-Extraktor v2 – Psychiatrie St.Gallen
================================================

Verwendung:
  python fragebogen_extractor.py <eingabe> [<eingabe2> ...] [--output <ordner>]

Eingabe kann sein:
  • Eine einzelne PDF-Datei
  • Mehrere PDF-Dateien (durch Leerzeichen getrennt)
  • Ein Ordner (alle *.pdf darin werden verarbeitet)
  • Kombination davon

Optionen:
  --output <ordner>   Ausgabe-Ordner (Standard: ./output)

Beispiele:
  python fragebogen_extractor.py scan_001.pdf
  python fragebogen_extractor.py scan_001.pdf scan_002.pdf scan_003.pdf
  python fragebogen_extractor.py ./scans/
  python fragebogen_extractor.py ./scans/ --output ./ergebnisse
  python fragebogen_extractor.py scan_001.pdf ./weitere_scans/ --output ./ergebnisse
"""


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(USAGE)
        sys.exit(0)

    # --output parsen
    output_dir = "./output"
    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 >= len(args):
            print("❌  --output benötigt einen Pfad")
            sys.exit(1)
        output_dir = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if not args:
        print("❌  Keine Eingabedateien angegeben.")
        print(USAGE)
        sys.exit(1)

    # PDFs sammeln
    pdfs = collect_pdfs(args)

    if not pdfs:
        print("❌  Keine PDFs gefunden.")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "fragebogen_sammlung.csv")

    total = len(pdfs)
    ok = 0
    failed = []

    print(f"\n{'═' * 55}")
    print(f"  BATCH-VERARBEITUNG: {total} Fragebogen")
    print(f"  Ausgabe: {output_dir}")
    print(f"{'═' * 55}")

    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{total}] {pdf.name}")
        print("─" * 55)
        success = process_one(pdf, output_dir, csv_path)
        if success:
            ok += 1
        else:
            failed.append(pdf.name)

    # Abschlussbericht
    print(f"\n{'═' * 55}")
    print(f"  ABGESCHLOSSEN")
    print(f"  ✅  Erfolgreich: {ok}/{total}")
    if failed:
        print(f"  ❌  Fehlgeschlagen ({len(failed)}):")
        for f in failed:
            print(f"      • {f}")
    print(f"  📊  CSV: {csv_path}")
    print(f"{'═' * 55}\n")


if __name__ == "__main__":
    main()
