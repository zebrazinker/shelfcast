# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [SemVer](https://semver.org/lang/de/).

## [Unreleased]

### Hinzugefügt
- **Audiobookshelf-Anbindung**: Bücher direkt aus der eigenen Audiobookshelf-Bibliothek auswählen (Cover, Suche,
  mehrere Bibliotheken), herunterladen und wie gewohnt per USB oder WLAN an den Picco senden.
  API-Key im macOS-Schlüsselbund. „An Picco senden“ lädt, konvertiert und überträgt in einem Schritt.
- Übertragen-Status: Bücher aus Audiobookshelf zeigen dauerhaft „Auf dem Picco · Datum“; per Rechtsklick
  änderbar, Filter „Nicht übertragen | Übertragen“.
- Sortierung nach Titel, Autor, neu hinzugefügt, Übertragen-Datum und Lesefortschritt.
- Lesestatus aus Audiobookshelf („42 %“, „Gelesen“) neben dem Autor, Filter „Ungelesen | Liest gerade | Gelesen“.
- MIT-Lizenz.

### Geändert
- **Neuer Name: Shelfcast** (vorher „Boox Picco Studio“), Bundle-ID `de.forell.shelfcast`. Einstellungen,
  Übertragen-Status und API-Key werden beim ersten Start einmalig übernommen.
- Mindestversion macOS 26; Liquid Glass (schwebende Glas-Leiste, Glas-Buttons). Erscheinungsbild wählbar:
  System, Hell, Dunkel.
- Die gebaute App liegt nicht mehr im Repository, sondern nur noch im DMG der Releases.
- Neue Oberfläche: Seitenleiste mit Quellen (Dateien, Audiobookshelf) und Ziel (Picco per USB, Picco per WLAN,
  Mac), ruhige Bücherliste mit Abzeichen und Fortschritt pro Zeile, ein Hauptknopf, Einstellungen als Blatt.
- Neues App-Icon.

## 1.1.0 – 2026-10-31

Auf dem Boox Picco mit „Regime Change“ und „Kingsbridge“ getestet (USB und WLAN).

### Hinzugefügt
- **Senden per WLAN**: Die App findet den Picco per Bonjour, sobald auf dem Gerät „Lokale Netzwerkübertragung“
  geöffnet ist, und schickt die Bücher direkt – ohne Kabel, ohne Konto, ohne Cloud. Umschalter USB | WLAN,
  Sendefortschritt pro Buch. Die Verbindung läuft gezielt über WLAN, damit ein VPN sie nicht umleitet.
- Bereitstellung als **DMG**.
- Testkorpus mit Vergleichswerkzeug (`opub-converter/tests/`).

### Geändert
- Konverter an die offizielle Boox-Ausgabe angeglichen: Formatierungen (fett, kursiv, Größen, Einzüge, Abstände),
  Kästen/Rahmen, Listen mit Aufzählungszeichen, Tabellen, Fußnoten, Zeilenumbrüche, Bildgrößen.
- Fortschrittsanzeige in der App: Warteschlange, Gesamtbalken, bereits konvertierte Bücher werden übersprungen,
  Gerätestatus bleibt sichtbar.

### Behoben
- Lesefortschritt blieb auf dem Gerät bei 0 %: Seitentabelle hat jetzt höchstens 999 Einträge.
- „Datei beschädigt“ bei Büchern mit SVG-Cover (Calibre): SVG-Bilder werden übernommen, leere Kapitel entfallen.

## 1.0.0 – 2026-10-30

Erste Version. Auf dem Boox Picco (BOOX TILE) mit zwei Büchern getestet.

### Hinzugefügt
- **EPUB → OPUB-Konverter** (`opub.py`), rekonstruiert aus einem Boox-Referenzpaar: Kapitel, Absätze,
  Textformatierung, Links, Inhaltsverzeichnis, Metadaten, Bilder und Cover-Vorschaubilder.
- **Mac-App „Boox Picco Studio“** (SwiftUI): Drag & Drop, Buchliste mit Cover/Titel/Autor,
  Duplikat-Erkennung, Live-Erkennung des angeschlossenen Boox, Konvertieren direkt in `books`
  auf dem Gerät oder nach `~/Downloads/Boox`, automatisches Entfernen der macOS-`._`-Dateien.
- Eigenes App-Icon.
- Kommandozeilen-Aufruf, kleine Weboberfläche (`app.py`) und Prüfwerkzeug (`verify.py`).
- Formatbeschreibung `FORMAT.md`.

### Behoben (während der Entwicklung)
- Farbige Cover wurden zu dunkel in Graustufen umgerechnet; jetzt an die Helligkeit von Boox angeglichen.

### Bekannte Einschränkungen
- Keine Container/Rahmen, eingebetteten Schriften, Tabellen oder Floats.
- Seitenaufteilung geschätzt; 1-Bit-Raster der Vorschaubilder weicht vom Original ab.
- App nur ad-hoc signiert (beim ersten Start Rechtsklick → Öffnen), nur Apple Silicon, macOS 14+.

