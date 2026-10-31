# Shelfcast

Schickt EPUB-Bücher – aus Dateien oder aus deiner Audiobookshelf-Bibliothek – auf den **BOOX Picco**.
Dafür werden sie in das proprietäre **OPUB**-Format konvertiert, das der Picco als einziges öffnet.

Das Format ist nicht öffentlich dokumentiert; es wurde aus einem Referenzpaar (EPUB + von Boox
erzeugte OPUB) rekonstruiert. Details: [opub-converter/FORMAT.md](opub-converter/FORMAT.md).

## Mac-App

Erscheinungsbild (System, Hell, Dunkel) und Audiobookshelf-Verbindung findest du unter **Einstellungen** unten in der Seitenleiste.

<img src="docs/icon.png" width="128" alt="App-Icon">

1. DMG aus den Releases öffnen und `Shelfcast.app` auf „Programme“ ziehen (oder [selbst bauen](#selbst-bauen)).
2. EPUBs ins Fenster oder aufs Dock-Symbol ziehen – oder „Dateien auswählen…“.
3. Links unter **Gerät** das Ziel wählen (wird automatisch vorgeschlagen) und **An Picco senden**
   bzw. **Konvertieren** klicken:
   - Boox angeschlossen (Laufwerk mit Ordner `books`) → Bücher landen direkt auf dem Gerät,
     macOS-`._`-Dateien werden entfernt.
   - Picco im WLAN (auf dem Gerät „Lokale Netzwerkübertragung“ öffnen) → Bücher werden kabellos gesendet;
     beim ersten Start den Zugriff auf das lokale Netzwerk erlauben. Ein VPN, das das Heimnetz umleitet, stört.
   - Kein Boox → Bücher landen in `~/Downloads/Boox`.
   - Vorhandene Dateien werden nie überschrieben (` (2)`, ` (3)` …).

### Bücher aus Audiobookshelf

In der Seitenleiste **Audiobookshelf** wählen, Server-Adresse (z. B. `http://nas:13378`) und einen
API-Key eintragen (in Audiobookshelf: Einstellungen → API-Keys). Der Key liegt im macOS-Schlüsselbund.
Die App zeigt alle Einträge mit E-Book, mit Suche, Filter und Sortierung. EPUBs anhaken →
**An Picco senden**: herunterladen, konvertieren und übertragen in einem Schritt.
PDFs und andere Formate werden angezeigt, lassen sich aber nicht wählen.

Übertragene Bücher tragen das Abzeichen „Auf dem Picco“ (gespeichert in
`~/Library/Application Support/de.forell.shelfcast/transfers.json`). Hast du ein Buch auf dem Picco
gelöscht: Rechtsklick → „Als nicht übertragen markieren“. Der Picco selbst kann per WLAN keine Bücherliste
liefern, daher merkt sich die App das selbst.

Beim ersten Start einer heruntergeladenen Kopie: Rechtsklick → **Öffnen** (die App ist nur ad-hoc signiert).

Voraussetzungen: macOS 26+, Apple Silicon, `/usr/bin/python3` (kommt mit den Command Line Tools).

## Kommandozeile

```bash
python3 opub-converter/opub.py buch.epub [ausgabe.opub]
python3 opub-converter/app.py            # kleine Weboberfläche auf 127.0.0.1:8765
python3 opub-converter/verify.py --check ausgabe.opub
```

## Selbst bauen

```bash
opub-converter/build_app.sh
```

Braucht nur die Xcode Command Line Tools (`swiftc`), kein Xcode. Die App enthält Kopien von
`opub.py`, `droplet_helper.py` und `bookinfo.py` – nach Änderungen daran neu bauen.

## Aufbau

| Datei | Zweck |
|---|---|
| `opub-converter/opub.py` | Konverter EPUB → OPUB (nur Python-Standardbibliothek) |
| `opub-converter/droplet_helper.py` | Zielordner wählen (Boox / Downloads), konvertieren, `._` aufräumen |
| `opub-converter/bookinfo.py` | Titel, Autor und Cover für die App-Liste |
| `opub-converter/mac/` | SwiftUI-App, Info.plist, Icon-Generator |
| `opub-converter/verify.py` | Struktur-Prüfung und Vergleich mit einer Boox-Referenzdatei |
| `opub-converter/app.py` | Minimale Weboberfläche |

## Bekannte Einschränkungen

- Die Seitenaufteilung weicht in einzelnen Kapiteln um eine Seite von Boox ab.
- Graustufen farbiger Bilder und das Raster der 1-Bit-Vorschaubilder weichen leicht vom Original ab.
- Eingebettete Schriften werden (wie bei Boox) nicht übernommen, nur der Schriftname.
- Getestet mit wenigen Büchern und einem Gerät (Boox Picco / BOOX TILE).

## Lizenz

[MIT](LICENSE) © 2026 Martin Forell

Inoffizielles Projekt, nicht mit Onyx/BOOX verbunden. „BOOX“ ist eine Marke ihres Inhabers.
Das OPUB-Format wurde zur Interoperabilität aus eigenen Dateien rekonstruiert; das Repository enthält
keinen Code und keine Dateien von BOOX. Bücher mit Kopierschutz (DRM) werden nicht unterstützt.
