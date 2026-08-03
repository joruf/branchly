# Branchly — Technische Dokumentation

## Grundentscheidung: Git macht die Arbeit

Branchly implementiert Git nicht nachträglich, sondern ruft das installierte
`git`-Binary auf — dasselbe, was GitHub Desktop über *dugite* tut. Der Grund ist
nicht Bequemlichkeit: Gits Verhalten bei Credential-Helpern, Hooks, `.gitattributes`,
sparse-checkout, Zeilenenden und Submodulen ist die Referenz, und jede
Reimplementierung weicht irgendwo davon ab. `pygit2`/libgit2 wurde deshalb
verworfen, `GitPython` ist selbst nur ein Subprocess-Wrapper und hätte nichts
beigetragen.

Die Folge: die einzige heikle Stelle ist der Übergang von Benutzereingabe zur
Kommandozeile. Genau dort liegt die gesamte Härtung, in einem Modul.

## Schichten

```
run.py                     Bootstrap: Argumente, Sprache, Theme, QApplication
├── config/                AppSettings (JSON) + Theme-Token
├── i18n.py + locales/     Übersetzungen
├── models/                Reine Daten: RepoEntry, Category, Sortierung
├── gitops/                Die EINZIGE Schicht, die git aufruft
├── github_api/            REST-Client, Token-Ablage, Antwortmodelle
├── services/              Registry, Scanner, Scheduler, Desktop, Avatare
└── ui/                    Qt-Widgets; kennt gitops, aber nie subprocess
```

Regeln, die die Struktur tragen:

- **Nur `gitops/runner.py` startet einen Prozess.** Kein Widget, kein Service.
- **`ui/` parst keine Git-Ausgabe.** Das Parsen liegt in `gitops/`, damit es ohne
  laufendes Qt testbar ist — die Diff-Tests brauchen kein Fenster.
- **`gitops/` kennt kein Qt.** Der komplette Git-Kern ist mit `unittest` gegen
  Wegwerf-Repos prüfbar.
- **`models/` kennt nichts.** Reine Dataclasses mit defensivem `from_dict`.

## Die Sicherheitsschicht

### `gitops/runner.py`

Jeder Git-Aufruf geht durch `run()`. Dort und nur dort:

| Maßnahme | Warum |
|---|---|
| `subprocess.run([...], shell=False)` | Argumentliste statt Kommandostring — Quoting kann nicht schiefgehen |
| Kein NUL-Byte in Argumenten | Trennt in C-Strings vorzeitig ab |
| `--upload-pack`, `--receive-pack`, `--exec` werden abgewiesen | Kein Branchly-Kommando braucht sie; tauchen sie auf, kam sie aus ungeprüfter Eingabe |
| `-c protocol.ext.allow=never` vor jedem Subkommando | Der `ext`-Transport führt Kommandos aus |
| `GIT_TERMINAL_PROMPT=0` | Sonst hängt der Prozess an einem unsichtbaren Prompt |
| `LC_ALL=C`, `LANG=C` | Parsen darf nicht von der Locale abhängen |
| `GIT_PAGER=cat`, `GIT_EDITOR=true` | Kein interaktives Etwas |
| Timeout auf allem | Ein hängendes git darf das Fenster nicht einfrieren |
| `--no-optional-locks` bei Lesekommandos | Ein Hintergrund-Scan streitet nicht mit dem git des Nutzers um die Index-Sperre |

**Bewusst nicht gesetzt: `GIT_PROTOCOL_FROM_USER=0`.** Das hätte auch lokale Pfade
und Netzlaufwerke blockiert — ein Test hat das aufgedeckt. Der `ext`-Transport ist
doppelt abgesichert (URL-Prüfung + `protocol.ext.allow=never`), die Variable hätte
nur Funktionalität gekostet.

### `gitops/remote_url.py`

Klassifiziert jede Adresse, bevor Git sie sieht. Abgewiesen wird:

- **Steuerzeichen, insbesondere `\r` und `\n`.** Gits Credential-Protokoll ist
  zeilenbasiert; ein eingeschmuggeltes `\r` kann eine Zeile früher beenden, als das
  lesende Programm denkt, und Zugangsdaten für Host A an Host B geben. Das ist
  CVE-2025-23040 in GitHub Desktop, Teil der „Clone2Leak"-Serie.
- **`ext::`** und jedes andere `wort::`-Präfix (Transport-Helper führen Kommandos aus).
- **`--upload-pack=`, `--receive-pack=`, führender Bindestrich.**
- **Hostnamen, die mit `-` beginnen** — SSH liest die als Option. Auch das kam aus
  einem Test, nicht aus dem Entwurf.

Die Unterscheidung „Tippfehler" gegen „gecraftet" wird an die UI weitergegeben, damit
ein Vertipper keine Sicherheitswarnung auslöst.

### `gitops/refname.py`

Prüft Branch-, Tag- und Revisionsnamen nach den Regeln von `git check-ref-format`
plus einem Zusatz: **ein führender Bindestrich ist immer verboten**. Ein Branch
namens `--upload-pack=x` wäre sonst auf der Kommandozeile eine Option.

`suggest_branch_name()` macht aus Prosa einen gültigen Namen und ist so gebaut,
dass es nie einen ungültigen zurückgibt — das ist eigens getestet.

### Zugangsdaten

Branchly speichert **keine** Git-Zugangsdaten. Git-Operationen nutzen den
konfigurierten Credential-Helper des Nutzers. Nur das GitHub-API-Token wird
gespeichert, und zwar über `keyring` im Schlüsselspeicher des Systems. Ist keiner
verfügbar, bleiben die GitHub-Funktionen aus — es gibt bewusst **keinen** Rückfall
auf eine Datei.

## Nennenswerte Implementierungen

### Klonen in ein nicht-leeres Verzeichnis

`git clone` verweigert ein nicht-leeres Ziel grundsätzlich. Der Ersatz ist der
lange Weg: `git init` → `git remote add` → `git fetch` → `git checkout`. Gits
Checkout weigert sich, eine vorhandene ungetrackte Datei zu überschreiben — genau
die Zusicherung, die der Warndialog vorher ausspricht. Scheitert ein Schritt, wird
das gerade erzeugte `.git` wieder entfernt, damit kein halbfertiges Repository
zurückbleibt.

### Konflikte

Grundlage sind die Index-Stufen, die Git schon angelegt hat: `:1:` gemeinsame
Basis, `:2:` unsere Fassung, `:3:` ihre. Die drei gehen durch
`git merge-file --diff3`, also durch **Gits eigenen Merge-Algorithmus**; das
Ergebnis mit Markern wird geparst und in Regionen zerlegt.

Ein eigener 3-Wege-Merge wäre schwieriger und weniger verlässlich gewesen. Die
Marker selbst erreichen die UI nie — sie werden im Parser verbraucht.

### Graph-Layout

`git log --topo-order` liefert Commits mit Eltern-IDs; die Lane-Zuordnung passiert
in `gitops/history.py`: jede Lane merkt sich, auf welchen Commit sie wartet. Ein
Commit übernimmt die Lane, die auf ihn wartete, gibt sie an seinen ersten Eltern
weiter und weist weiteren Eltern eine eigene zu — freie Slots vor neuen.

Gezeichnet wird von einem `QStyledItemDelegate` in Spalte 0 eines `QTreeWidget`.
Das bringt Auswahl, Tastaturnavigation, Scrollen und Kontextmenüs kostenlos mit;
zu tun bleibt nur das Malen.

### Diff-Rendering

Als HTML in einem `QTextBrowser`, nicht als eigengemalte Fläche: ein Diff ist genau
das, wofür Markup gut ist. Die Renderer sind **reine Funktionen**
(`render_side_by_side`, `render_unified`, `render_many`) und werden ohne Fenster
getestet — inklusive Escaping, das dort ausdrücklich geprüft wird.

Wort-Ebene über `difflib.SequenceMatcher` auf Wort-Token, nicht auf Zeichen: ein
geänderter Identifier leuchtet als ein Block statt als Buchstabensalat.

### Hintergrund-Scans

`services/scheduler.py` hält einen `QThreadPool` mit **vier** gleichzeitigen
Prozessen. Zwanzig Repos auf einmal würden kurz die Maschine sättigen und das
Fenster ruckeln lassen — das Gegenteil von hilfreich. Ein zweiter Batch wird
abgewiesen, solange einer läuft, statt sich einzureihen.

Der Online-Teil nutzt `git ls-remote`. Das ist rein lesend: ein automatischer Scan
kann die Refs des Nutzers nicht bewegen. `fetch` und `pull` laufen nur auf
ausdrücklichen Wunsch.

## Themes und Sprachen erweitern

**Theme:** In `config/theme.py` eine `ThemeColors`-Instanz anlegen und in `_THEMES`
eintragen. Kein Widget kennt Hex-Werte; `tests/test_theme.py` prüft, dass jedes
Theme jeden Token definiert und dass jeder Wert eine Farbe ist.

**Sprache:** Eine JSON-Datei in `locales/` ablegen. Englisch ist der Fallback für
fehlende Schlüssel, `_label` liefert den Namen in der Auswahl.
`tests/test_i18n.py` erzwingt, dass DE und EN identische Schlüssel **und**
identische Platzhalter haben — das ist die Bremse gegen Auseinanderdriften.

## Tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

`tests/support.py` baut Wegwerf-Repos mit eigenem `HOME` und eigener
`GIT_CONFIG_GLOBAL`, damit die Suite auf einer Entwicklermaschine mit reicher
`~/.gitconfig` genauso läuft wie auf einem nackten CI-Runner. `temp_repo_pair()`
liefert zwei Clones eines Bare-Repos — die Form, die jeder Sync- und Konflikttest
braucht.

Die Tests der Sicherheitsschicht laufen in der CI **zuerst**, in einem eigenen
Schritt. Fällt dort etwas um, ist der Rest nicht mehr interessant.

## Plattformen

Getestet unter Linux. Alle OS-Spezifika liegen hinter `paths.py` (Konfig- und
Cache-Verzeichnisse, venv-Interpreter) und `services/open_with.py` (`xdg-open`,
`os.startfile`, `open`). Die CI-Matrix deckt Ubuntu 22.04, 24.04 und
`windows-latest` mit Python 3.11 und 3.12 ab.
