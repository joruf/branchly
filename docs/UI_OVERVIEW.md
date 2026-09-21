# Branchly — Aufbau der Oberfläche

## Gesamtlayout

```
┌──────────────┬──────────────────────────────────────────────────────────────┐
│              │ pmtool  main        [Branch…] [Server prüfen] [Holen] [Senden]│
│  Projekte    ├──────────────────────────────────────────────────────────────┤
│              │ ⓘ Hinweisstreifen (nur wenn es etwas zu sagen gibt)          │
│  Hinzufügen  ├──────────────────────────┬───────────────────────────────────┤
│  Klonen      │ Änderungen│Graph│PRs      │ Gegenüberstellung                │
│  ┌─────────┐ │                          │                                   │
│  │ Suche   │ │  ☑ config.py             │ ┌─ Vergleichen: … ─ Nebeneinander ┐│
│  └─────────┘ │  ☑ notes.md              │ │  1 port = 8080 │ 1 port = 3000  ││
│  Sortierung  │                          │ │  2 debug=True  │ 2 debug=False  ││
│              │                          │ └────────────────────────────────┘│
│ ▾ Arbeit (1) │                          │                                   │
│   ★ pmtool   ├──────────────────────────┤                                   │
│ ▾ Privat (2) │ Kurzfassung…             │                                   │
│   ☆ consentry│ Beschreibung…            │                                   │
│   ☆ snappix  │        [2 Dateien in main │ speichern]                        │
│              │                          │                                   │
│ Zusammenfas. │                          │                                   │
│ Alle prüfen  │                          │                                   │
└──────────────┴──────────────────────────┴───────────────────────────────────┘
```

## Projektliste (`ui/sidebar.py`)

| Element | Zweck |
|---|---|
| Hinzufügen / Klonen | Projekt aufnehmen. Kurze Labels, volle Beschriftung im Tooltip |
| Suchfeld | Filtert Name und Pfad, auch in zugeklappten Kategorien |
| Sortierung | Sechs Ordnungen; Favoriten stehen immer oben |
| Kategorie-Kopf | Name und Anzahl, auf- und zuklappbar, Zustand wird gespeichert |
| Projektzeile | Sternchen, Name, Badges. Fehlender Ordner wird rot mit `!` markiert |
| Zusammenfassung | Ein Satz über alle Projekte |
| Alle Projekte prüfen | Während des Laufs Fortschrittsbalken statt Zusammenfassung |
| Alle Projekte aktualisieren | Öffnet den Massen-Pull. Während einer Prüfung gesperrt — beide würden um dieselben Index-Sperren streiten |

## Kopfzeile

Projektname und aktueller Branch links; rechts die Aktionen. Auf „Holen" und
„Senden" steht die Anzahl, sobald etwas anliegt. Ohne Server sind die drei
Server-Buttons ausgegraut.

## Hinweisstreifen (`ui/widgets.py`, `InlineMessage`)

Erscheint nur, wenn es etwas zu sagen gibt, und immer mit Erklärung:

| Anlass | Farbe |
|---|---|
| Projekt hat keinen Server | Info |
| Kein Branch ausgewählt (Detached HEAD) | Warnung |
| Server hat den Push abgelehnt | Warnung |
| Ordner nicht mehr gefunden | Gefahr |
| Konflikte offen — mit Button „Los geht's" | Warnung |

Dies ist bewusst kein Modal: eine Erklärung soll stehen bleiben, während man
weiterarbeitet.

Darüber liegt ein **zweiter** Streifen derselben Bauart, nur für Branchly selbst:
„Eine neuere Version ist verfügbar" mit *Installieren und neu starten* und *Jetzt
nicht*. Ein eigener Streifen, weil die nächste Projektauswahl den unteren neu
schreibt — eine Nachricht über das Programm darf dabei nicht verschwinden.

## Reiter Änderungen (`ui/changes_panel.py`)

Rechts an jeder Zeile steht das Statuszeichen (`+` neu, `•` ersetzt, `−` gelöscht,
`→` umbenannt, `!` Konflikt), gezeichnet von `StatusGlyphDelegate`. Ein Delegate
und kein Widget pro Zeile, weil die Zeilen eine Ankreuzbox tragen, die die
Ansicht selbst zeichnet: mit einem eigenen Widget müsste man die nachbauen.

Dateiliste mit Häkchen; die Farbe der Zeile sagt die Art (geändert, neu, gelöscht,
umbenannt, nicht erfasst, Konflikt). Konfliktdateien sind fett und **nicht**
anhakbar — sie können erst nach der Entscheidung gespeichert werden.

Unten die Commit-Box. Der Button ist deaktiviert, solange keine Datei angehakt oder
keine Kurzfassung geschrieben ist; der Tooltip sagt, was fehlt.

## Reiter Graph (`ui/graph_view.py`)

Lanes in Spalte 0, gezeichnet von einem Delegate. Punkt = Commit, Ring = „hier
stehst du", Farbe je Lane aus den Theme-Tokens. Rechts Betreff mit Branch- und
Tag-Namen, Autor, Datum. Kontextmenü mit allen Aktionen; Destruktives fragt nach.

## Reiter GitHub (`ui/github_panel.py`)

Kopfzeile mit dem Repository-Namen, „Repository-Einstellungen…" und
„Aktualisieren". Darunter vier Unterreiter: *Pull Requests*, *Issues*, *Releases*,
*Actions*.

Alle vier haben denselben Aufbau (`ui/github_lists.py`, Klasse `RemoteTab`):
Filterzeile, Liste, Detailansicht, Aktionsleiste. Eine Listenzeile trägt je nach
Reiter eine Ampel, zwei Zeilen Text und einen Avatar. Die Detailansicht ist ein
`QTextBrowser`, der Markdown rendert, denn Beschreibungen, Versionshinweise und
Kommentare sind auf GitHub Markdown.

Was nicht in die Aktionsleiste passt, liegt hinter „Mehr…". Aktionen, die gerade
nicht gehen, sind abgeschaltet und nicht versteckt, und der Tooltip nennt den
Grund: fehlendes Schreibrecht, ein archiviertes Repository, ein Entwurf, der noch
nicht zusammengeführt werden kann, eine Merge-Methode, die das Repository
abgeschaltet hat.

Die Aktionsleiste benutzt `FlowLayout` aus `ui/widgets.py` und bricht um, statt
überzulaufen. Acht Knöpfe passen bei voller Fensterbreite in eine Zeile, in einem
schmal gezogenen Panel werden daraus zwei oder drei. Ein `QHBoxLayout` hätte die
letzten Knöpfe rechts abgeschnitten, wo sie weder sichtbar noch erreichbar sind.

Jeder Zustand wird ausgesprochen: kein Token, kein GitHub-Projekt, Rate-Limit,
oder wirklich nichts offen. Eine leere Liste ohne Erklärung ist das, was Programme
kaputt aussehen lässt.

Kein Aufruf läuft im UI-Thread. Alles geht durch `ApiRunner`
(`ui/github_worker.py`), der die Aufrufe der Reihe nach abarbeitet.

## GitHub-Dialoge (`ui/github_dialogs.py`)

Issue, Pull Request, Merge, Prüfung, Kommentar, Release, Repository-Einstellungen
und Repository anlegen. Alle mit derselben Hülle: Formular, Hinweisstreifen für
die Prüfung der Eingabe, OK/Abbrechen. Keiner davon ruft die API auf, sie sammeln
nur Eingaben. Das Senden und das Melden des Ergebnisses liegt beim Panel.

Der Löschdialog für ein Repository ist bewusst anders: kein Ja/Nein, sondern der
vollständige Name zum Eintippen, und der Knopf bleibt bis dahin gesperrt.

## Gegenüberstellung (`ui/diff_view.py`)

Kopfzeile: Vergleichsziel, Darstellung, Abstände ignorieren, Wörter hervorheben,
`+n −n`, „Datei öffnen", „Im Ordner zeigen".

Fünf Zustände: Platzhalter („Wähle eine Datei"), eine Spalte, zwei Spalten,
Hinweis (Fehler), und `BinaryComparisonView` für alles, was keine vergleichbaren
Zeilen hat.

`BinaryComparisonView` hat zwei Spalten, Vorher und Nachher, jede mit Überschrift,
Herkunft („Auf deiner Festplatte", „Stand HEAD", „Bereit zum Speichern"), Vorschau
und darunter Größe und Datum. Die Vorschau ist bei einem Bild das Bild, sonst ein
gestricheltes Feld mit dem Dateityp. Unter beiden Spalten steht ein Satz zur
Größenänderung. Eine Seite, die es nicht gibt, schreibt das in ihr Vorschaufeld,
statt leer zu bleiben.

Die beiden Darstellungen sind zwei verschiedene Widgets, nicht zwei Varianten
derselben HTML-Tabelle:

- **Eine Spalte** ist ein `QTextBrowser` mit einem Dokument.
- **Nebeneinander** ist `ComparisonPanes`: ein `QSplitter` mit zwei
  `QTextBrowser`, links der alte Stand, rechts der neue. Jede Seite hat ihren
  eigenen waagerechten Scrollbalken, beide sind in beide Richtungen gekoppelt.
  Senkrecht ebenso, damit die Zeilen auf gleicher Höhe bleiben; dort wird nur der
  rechte Balken gezeichnet, weil zwei identische nebeneinander nur die Frage
  aufwerfen, welcher davon welcher ist.

Beide Seiten rendern eine Zeile pro Gegenüberstellungspaar, Leerzeilen
eingeschlossen, sonst laufen die Hälften auseinander.

## Konflikt-Assistent (`ui/conflict_dialog.py`)

```
┌─ Entscheidung 2 von 5 ─── config/app_settings.py ──────┐
│ Ihr habt beide dieselbe Zeile geändert.                │
├─────────────────┬─────────────────┬────────────────────┤
│ DEINE Änderung  │ VOM SERVER      │ ERGEBNIS           │
│ port = 8080     │ port = 3000     │ port = 8080        │
├─────────────────┴─────────────────┴────────────────────┤
│ [ Meine nehmen ] [ Andere nehmen ] [ Beide ] [ Selbst ]│
│ [ Das für alle übrigen so machen ]                     │
│ ← Zurück   [Abbrechen]        noch 3 offen   Weiter →  │
└────────────────────────────────────────────────────────┘
```

Drei Seiten: Einleitung (was ist passiert), Entscheidung (je Konflikt),
Abschluss. Die Ergebnis-Spalte bekommt einen farbigen Rahmen, sobald gewählt ist.

## Einstellungen (`ui/settings_dialog.py`)

Vier Reiter: Allgemein, Automatische Prüfung, Gegenüberstellung, GitHub.
Bearbeitet wird eine Kopie — Abbrechen lässt wirklich alles, wie es war. Nur der
GitHub-Reiter hat sofortige Wirkung: ein gespeichertes Token wird augenblicklich
gegen die API geprüft.

## Alle Projekte aktualisieren (`ui/pull_all_dialog.py`)

```
┌─ Alle Projekte aktualisieren ──────────────────────────┐
│ ┌────────────────────────────────────────────────────┐ │
│ │ Fertig                                             │ │
│ │ 2 aktualisiert · 6 neue Commits · 2 übersprungen   │ │
│ └────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │ snappix — eigene Änderungen … · 3 warten am Server │ │
│ │ byteback — 3 neue Commits                          │ │
│ │ consentry — 3 neue Commits                         │ │
│ │ nudge — eigene Commits sind noch nicht gesendet    │ │
│ └────────────────────────────────────────────────────┘ │
│                                            [Schließen] │
└────────────────────────────────────────────────────────┘
```

Drei Zustände in einem Fenster: Ankündigung, Fortschritt, Bericht. Zeilenfarbe nach
Ausgang — `success` vorgespult, `text_muted` schon aktuell, `warning` übersprungen,
`danger` fehlgeschlagen. Modal, und während des Laufs ist *Schließen* gesperrt: es
wird in Arbeitsbäume geschrieben.

## Updates (`ui/update_dialog.py`)

```
┌─ Updates ──────────────────────────────────────────────┐
│ Branchly                                               │
│ Version 0.1.0                                          │
│ ┌────────────────────────────────────────────────────┐ │
│ │ Eine neuere Version ist verfügbar                  │ │
│ │ Graph-Layout beschleunigt — Branchly kann sie holen│ │
│ └────────────────────────────────────────────────────┘ │
│ Installiert fa5cf9e424, verfügbar 9c1d7ab002.          │
│ [Erneut prüfen]   [Installieren und neu starten][Zu]   │
└────────────────────────────────────────────────────────┘
```

Über *Hilfe → Nach Updates suchen…* oder über den Streifen. Prüfen und Installieren
laufen auf einem Worker-Thread. Während des Installierens ist *Schließen* gesperrt:
ein Worker, der Dateien schreibt, darf nicht ins Leere laufen. Nichts wird ohne
Rückfrage installiert, und der Neustart passiert erst, wenn das Fenster weg ist.

## Farben

Kein Widget enthält einen Hex-Wert. Alles kommt aus `config/theme.py`:

| Gruppe | Beispiele |
|---|---|
| Chrome | `window_bg`, `surface`, `text`, `border`, `accent` |
| Semantik | `success`, `warning`, `danger`, `info` (+ `_bg` für Badges) |
| Diff | `diff_added_bg`, `diff_removed_bg`, `diff_word_added_bg`, `diff_gutter_bg` |
| Dateistatus | `status_modified`, `status_added`, `status_deleted`, `status_conflict` |
| Graph | `graph_lanes` (8 Farben), `graph_head_ring`, `graph_node_border` |
| Konflikt | `conflict_ours_bg`, `conflict_theirs_bg`, `conflict_result_bg` |
| Prüfungen | `check_pass`, `check_fail`, `check_pending`, `check_neutral` |

## Screenshots

- `screenshots/main-window-dark.png`, `main-window-light.png`
- `screenshots/graph-dark.png`, `graph-light.png`
- `screenshots/conflict-assistant-dark.png`, `conflict-assistant-light.png`
- `screenshots/github-panel-dark.png`, `github-panel-light.png`
- `screenshots/binary-comparison-dark.png`, `binary-comparison-light.png`

Neu erzeugen:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/generate_screenshots.py
```

Das Skript baut ein Demo-Repository mit Branch, Merge und offenen Änderungen und
wirft es danach weg.
