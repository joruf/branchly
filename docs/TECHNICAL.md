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
├── services/              Registry, Scanner, Scheduler, Puller, Updater, Desktop
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

### Alle Projekte auf einmal holen

`services/puller.py` (Qt-frei, also ohne Fenster testbar) plus `PullCoordinator` in
`services/scheduler.py` für den Thread-Pool. Drei gleichzeitige Prozesse, nicht vier
wie beim Scan: jeder Pull ist ein voller Fetch **plus** ein Schreibvorgang im
Arbeitsbaum.

Die eine Entscheidung, auf der alles ruht: **`git pull --ff-only`**. Bei einem
Projekt schaut der Nutzer hin und entscheidet; bei zwanzig nicht. Ohne `--ff-only`
könnte ein Lauf zwanzig Merge-Commits bauen und in fünf Ordnern einen offenen
Konflikt hinterlassen — genau das, was Branchly nie ohne Rückfrage tun darf. Mit
`--ff-only` lehnt git ab und lässt HEAD stehen, wo es stand. `tests/test_pull_all.py`
prüft beides an echten Klon-Paaren, inklusive „der Einzel-Button merged weiter".

`blocking_reason()` nennt vorab *einen* Grund, sortiert danach, was der Nutzer am
ehesten hören muss: Konflikt → laufender Vorgang → Detached HEAD → unfertige Arbeit
→ kein Upstream → eigene Commits. Eigene Commits werden dabei aus `state.ahead`
erkannt, nicht aus einer Fehlermeldung von git: „dein Branch ist auseinandergelaufen"
ist eine Aussage, die Branchly selbst treffen kann.

Übersprungene Projekte bekommen trotzdem ein `fetch`. Fetch schreibt nur in die
Tracking-Refs und lässt den Arbeitsbaum unberührt — dadurch stimmt hinterher das
Badge, statt nach dem Lauf veraltet dazustehen.

Der Dialog ist modal, und während der Lauf schreibt, ist *Schließen* gesperrt. Das
ist die einzige Massenaktion in Branchly, die in Arbeitsbäume schreibt; solange ein
Worker in einem Projekt steckt, darf niemand daneben einen Push starten.

### Selbst-Update

`services/updater.py`. Branchly veröffentlicht keine Releases und keine Tags,
also ist „neuer" der Head-Commit von `main`: `GET /repos/joruf/branchly/commits/main`
liefert ihn, `git rev-parse HEAD` liefert den eigenen. Kein Versionsvergleich, keine
Semantik, nichts, was falsch sortieren kann.

Ungleiche Commits heißen aber **nicht** „hinterher". Wer an Branchly arbeitet, sitzt
auf einem eigenen, noch nicht gepushten Commit — die reine Ungleichheit hätte dem
ein Phantom-Update gemeldet, das kein `pull` je einlösen kann. Deshalb entscheidet
`_is_behind()` in drei Stufen:

1. `GET /compare/{local}...{remote}` → `ahead_by` ist die verbindliche Antwort.
   `0` heißt: der Server hat nichts, was hier fehlt.
2. Antwortet der Vergleich nicht (404, weil GitHub den lokalen Commit nie gesehen
   hat), fragt `git merge-base --is-ancestor` lokal nach — vorher prüft
   `git cat-file -e`, ob das Objekt überhaupt da ist.
3. Ist beides nicht zu haben, bleibt die Ungleichheit als letzte Auskunft.

Derselbe `/compare`-Aufruf liefert die Commit-Betreffzeilen für **Was ist neu**.
Ein Betreff wäre eine schlechte Antwort, wenn zwölf Commits dazwischenliegen: der
Nutzer sähe den letzten und erführe von den anderen nie. Angezeigt werden höchstens
`UPDATE_MAX_CHANGES` Zeilen, gezählt wird trotzdem alles.

Der Fund selbst liegt in `settings.json` (`update_remote_commit`,
`update_remote_summary`). Ohne das wäre „Jetzt nicht" faktisch „24 Stunden nicht":
die Drosselung verhindert die nächste Abfrage, und ein Neustart hätte den Streifen
nicht zurückgebracht. Beim Start wird der gemerkte Commit ohne Netz gegen `HEAD`
gehalten; passt er, erscheint der Streifen wieder, ist er installiert, wird er
vergessen. Ein Klick auf *Installieren* prüft neu — das holt die Änderungsliste und
bestätigt, dass das Update überhaupt noch aussteht.

Bewusst **nicht** über `github_api/client.py`: der Client dreht sich um das Token
des Nutzers, dessen Pull Requests und einen ETag-Cache. Die Update-Prüfung stellt
eine anonyme Frage zu einem öffentlichen Repo und darf dabei weder das Rate-Limit
des Nutzers verbrauchen noch sein Token sehen. `tests/test_updater.py` prüft, dass
kein `Authorization`-Header rausgeht.

Zwei Wege beim Einspielen, automatisch gewählt:

| Weg | Wann | Schutz |
|---|---|---|
| `git pull --ff-only` | Es gibt ein `.git` (der Normalfall) | `git status --porcelain` muss leer sein; `--ff-only` scheitert an eigenen Commits statt sie zu begraben |
| Branch-ZIP über den Ordner | Kein `.git` | `.git`, `.venv`, `settings.json`, `repos.json` werden nie überschrieben; Größenlimit beim Download; Zielpfade müssen innerhalb der Installation liegen |

Der Neustart ist das Letzte, was der Prozess tut: `ui/update_dialog.py` setzt nur
`restart_wanted`, das Fenster schließt normal, `run.py` ruft danach
`updater.restart()`. Dateien unter einem laufenden Python zu ersetzen ist harmlos —
die Module sind längst geladen —, ein Neustart mitten in der Event-Loop nicht.
Unter POSIX ersetzt `os.execve` den Prozess, unter Windows startet ein Kind mit
`pythonw.exe` und `CREATE_NO_WINDOW`. In beiden Fällen wird `BRANCHLY_REEXEC` aus
der Umgebung entfernt, sonst würde das Kind seine venv nicht mehr betreten.

Die Prüfung beim Start läuft verzögert (4 s) auf einem Worker-Thread und schweigt,
wenn sie scheitert: sie läuft auch ohne Netz, und ein Dialog darüber wäre Lärm.
Der Zeitstempel der letzten *erfolgreichen* Prüfung liegt in `settings.json`;
gescheiterte Prüfungen setzen ihn nicht, damit eine Woche offline nicht als
„geprüft" durchgeht.

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
