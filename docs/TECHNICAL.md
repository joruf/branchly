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
│   ├── http.py            Transport: Cache, Paginierung, Rate-Limit, Fehler
│   ├── repos/issues/…     je eine Gruppe von Endpunkten, als Mixin
│   └── client.py          setzt die Gruppen zu einer Klasse zusammen
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

### Die GitHub-Schicht

`github_api/` ist in Transport und Endpunkte getrennt. Der Transport (`http.py`)
beantwortet eine Frage an genau einer Stelle: was passiert, wenn der Server etwas
anderes sagt als „bitte sehr". Die Endpunktmodule beschreiben nur, wonach gefragt
wird, und werden als Mixins zu `GitHubClient` zusammengesetzt.

Fünf Dinge erledigt der Transport:

| Maßnahme | Warum |
|---|---|
| ETag-Cache | Die häufige Frage „gibt es Neues" kostet meist nur ein 304 |
| Jeder Schreibzugriff leert den Cache | Eine Liste, die vor einer Sekunde stimmte, stimmt nach einem Anlegen nicht mehr |
| Paginierung über den `Link`-Header | Wer „alle Issues" will, soll nicht wissen müssen, dass eine Seite bei 100 endet |
| Statuscode plus GitHubs eigener Satz | „name already exists on this account" ist brauchbar, „etwas ging schief" nicht |
| Timeout auf allem | Ein langsames Netz darf keinen Worker-Thread länger blockieren als nötig |

**Rate-Limit wird als Zustand geführt, nicht als Fehler.** Sagt GitHub „warte",
hört der Client auf zu fragen und meldet, wie lange. Ein 403 ist dabei doppeldeutig
(fehlende Berechtigung oder verbrauchtes Kontingent), unterschieden wird über den
Header `X-RateLimit-Remaining`, nicht über den Status.

**GraphQL nur, wo REST nichts anbietet.** Einen Entwurf als bereit zu markieren
gibt es ausschließlich als GraphQL-Mutation. Deswegen kennt der Transport
`graphql()`, und deswegen prüft der dortige Code den Antwortkörper statt des
Status: GraphQL antwortet auch auf Fehler mit 200.

**Ergebnisse sind einheitlich.** Jeder Aufruf gibt ein `ApiResult` zurück, mit
`ok`, Nutzlast, Übersetzungsschlüssel, GitHubs eigenem Satz und der Wartezeit bei
einem Limit. Die vier ursprünglichen Lesemethoden (`viewer`, `pull_requests`,
`issues`, `check_state`) behalten ihre alte Signatur, damit bestehende Aufrufer
unverändert bleiben.

### GitHub-Aufrufe und der UI-Thread

Qt zeichnet nichts, solange ein Slot läuft, also ruft kein Widget den Client
direkt auf. `ui/github_worker.py` nimmt den Aufruf entgegen, führt ihn auf einem
QThread aus und liefert das Ergebnis per Signal zurück.

Der Runner arbeitet **einen Aufruf nach dem anderen** ab. Das ist kein
Kompromiss bei der Geschwindigkeit, sondern der Zweck: ein Schreibzugriff leert den
Cache, und würde die nachfolgende Liste den Schreibzugriff überholen, sähe der
Nutzer den Stand von vor seiner eigenen Änderung.

Ein Aufruf, der beim Schließen noch läuft, wird nicht abgewartet. Sein Ergebnis
wird verworfen, denn ein Panel, das es nicht mehr gibt, kann nichts mehr anzeigen,
und ein Fensterschließen 15 Sekunden am Request hängen zu lassen wäre schlimmer als
ein verschwendeter Request.

### Was beim Löschen eines Repositories anders ist

Alles andere in Branchly ist wiederherstellbar: eine verworfene Änderung, ein
gelöschter Branch, ein zurückgesetzter Commit. Ein gelöschtes Repository nicht.
Deshalb ist die Bestätigung dort von anderer Art als überall sonst: der
vollständige Name muss eingetippt werden, genau wie in GitHubs eigener Oberfläche,
und der Knopf bleibt bis dahin gesperrt. Lokal wird nichts angefasst, Branchly
fragt danach nur, ob der Eintrag aus der Projektliste verschwinden soll.

### Die Gegenüberstellung als zwei Ansichten

Vorher war „Nebeneinander" **eine** HTML-Tabelle mit vier Spalten in einem
`QTextBrowser`. Das war weniger Code und hatte einen Fehler, der erst bei wenig
Platz auffällt: ein Widget hat einen waagerechten Scrollbalken. Wurde das Panel
schmal, war entweder eine Seite abgeschnitten oder man musste die ganze Tabelle
schieben, um eine lange Zeile rechts zu lesen.

Jetzt sind es zwei Dokumente in zwei `QTextBrowser` in einem `QSplitter`
(`ComparisonPanes`). Damit das funktioniert, müssen drei Dinge stimmen:

| Punkt | Lösung |
|---|---|
| Die Seiten müssen auf gleicher Höhe bleiben | Beide rendern eine Zeile pro Paar aus `pair_lines()`, Leerzeilen eingeschlossen. Eine leere Zelle bekommt `&nbsp;`, sonst fällt die Zeile in sich zusammen und die Hälften laufen auseinander |
| Waagerecht soll gemeinsam gescrollt werden | Jede Seite treibt die andere an, in beide Richtungen |
| Ein gesetzter Wert löst dasselbe Signal wieder aus | Ein Wächter-Flag statt Verbinden und Trennen |

**Die Falle dabei war eine Tabellenzeile mit `colspan`.** Qts Textengine gibt bei
einer Zeile, die beide Spalten überspannt, die Spaltenbreiten auf und schenkt der
Zeilennummernspalte ein Drittel der Fläche, wodurch der Code rechts aus dem Bild
läuft. Weder `width:1%` noch `width:52px` noch das `width`-Attribut helfen
dagegen. Die Kopfzeilen der Panes bestehen deshalb aus zwei echten Zellen, eine
leere Gutter-Zelle und der Text. Ein Test misst, wo die zweite Spalte beginnt,
damit das nicht unbemerkt zurückkommt.

### Die gemerkte Dateiauswahl

Gespeichert wird in `repos.json` pro Projekt, und zwar **was abgewählt wurde**,
nicht was angehakt ist. Der Unterschied ist nicht kosmetisch: alles ist per
Vorgabe angehakt, also braucht eine neu aufgetauchte Datei keinen Eintrag, um
dabei zu sein. Die umgekehrte Speicherung müsste jede neue Datei erst eintragen
und wäre bei jedem Scan zu pflegen.

Die Menge lebt im Panel und nicht in der Liste, damit eine Abwahl überlebt, dass
die Datei zwischendurch gar nicht in der Liste steht. Aufgeräumt wird nur an
einer Stelle: nach einem Commit vergisst `forget_selection()` genau die Pfade,
die drin waren.

### `.gitignore` schreiben

`gitops/ignore.py` schreibt die Datei selbst, denn git hat dafür kein Kommando.
Zwei Details, die man einmal falsch macht:

- **Ein Dateiname ist kein Muster.** `*`, `?`, `[`, ein führendes `#` und ein
  führendes `!` sind Syntax. Eine Datei namens `report[2].txt` braucht Maskierung,
  sonst trifft der Eintrag etwas anderes oder nichts. Ein Test schreibt den
  erzeugten Eintrag und lässt `git check-ignore` bestätigen, dass die echte Datei
  danach ignoriert wird.
- **Zeilenenden.** Die Datei wird mit `newline=""` gelesen, weil Python sonst CRLF
  beim Lesen zu LF macht. Ohne das hängt an eine CRLF-Datei ein LF an, und der
  nächste Diff zeigt die ganze Datei als geändert.

Ob etwas *schon* ignoriert ist, kann nur git beantworten, denn dafür zählen alle
`.gitignore` nach oben, `.git/info/exclude` und die globale Ausschlussdatei. Das
geht über `git check-ignore`.

### Dateien ohne vergleichbare Zeilen

`gitops/blobs.py` beantwortet für beide Seiten einer Gegenüberstellung zwei Fragen:
wie groß ist diese Fassung, und wann ist sie entstanden. Das Unangenehme daran ist,
dass die beiden Seiten selten dasselbe Ding sind:

| Seite | Größe | Datum |
|---|---|---|
| Arbeitsverzeichnis | `os.stat` | `st_mtime` |
| Commit | `git cat-file -s <rev>:<pfad>` | `git log -1 --format=%ct <rev> -- <pfad>` |
| Index | `git cat-file -s :<pfad>` | `mtime` aus `git ls-files --debug` |

Ein Blob hat **kein** eigenes Datum. Das einzig sinnvolle Datum für eine
committete Fassung ist das des Commits, der den Pfad zuletzt angefasst hat, und
genau das wird genommen. Der Index ist die eine Stelle, an der git einen
Dateisystem-Zeitstempel führt, deshalb gibt es dort überhaupt eine Antwort.

**Wo sich kein Datum ermitteln lässt, steht ein Strich.** Ein erfundener
Zeitstempel in einer Gegenüberstellung ist schlimmer als eine Lücke.

Welche zwei Fassungen ein Vergleichsziel meint, steht in `comparison_sides()` an
einer Stelle, statt an jeder Aufrufstelle neu abgeleitet zu werden.

Vorschaubytes werden nur für Bilder gelesen und nur bis `MAX_PREVIEW_BYTES`. Ein
dreißig Megabyte großes Bild würde ohnehin auf ein paar hundert Pixel skaliert;
es dafür ganz in den Speicher zu lesen wäre kein guter Handel. Dass die Vorschau
deswegen fehlt, wird gesagt, nicht als leeres Feld gezeigt.

### Die Statuszeichen in der Dateiliste

`CHANGE_GLYPHS` steht in `gitops/status.py`, direkt neben den Zustandskonstanten,
damit die Oberfläche nie selbst Statusbuchstaben auf Zeichen abbildet.

Gezeichnet werden sie von `StatusGlyphDelegate` (`ui/changes_panel.py`), nicht von
einem Widget pro Zeile. Der Grund ist die Ankreuzbox: die zeichnet die Ansicht
selbst, und ein `setItemWidget` pro Zeile hieße, sie nachzubauen. Das Delegate
verkleinert zuerst das Textrechteck um die Breite des Zeichens, sonst liefe ein
langer Pfad darunter hindurch statt vorher gekürzt zu werden.

### Tooltips

Die Texte stehen in `locales/` unter dem Präfix `tip.`, nicht im Code. Gesetzt
werden sie direkt beim Bau des Widgets, damit man beim Hinzufügen eines
Bedienelements darüber stolpert.

Drei Fehler kann man dabei machen, und keiner davon stürzt ab:

| Fehler | Was der Nutzer sieht | Was ihn findet |
|---|---|---|
| Schlüssel fehlt im Katalog | den rohen Schlüsselnamen, etwa `tip.pr_merge` | `test_tooltips.py`, Abgleich Code gegen Katalog |
| Neues Bedienelement ohne Tooltip | gar nichts | `test_tooltips.py`, Abdeckung der dauerhaft sichtbaren Bereiche |
| Schlüssel zweimal vergeben | den Text des *anderen* Elements | `test_i18n.py`, `_duplicate_keys()` |

Der dritte ist der unangenehmste und ist beim Bauen tatsächlich passiert:
`tip.repo_name` war zweimal definiert, einmal für die Projektbezeichnung in der
Kopfzeile und einmal für das Namensfeld beim Anlegen. JSON behält stillschweigend
den letzten, also erklärte die Kopfzeile plötzlich die erlaubten Zeichen eines
Repository-Namens. Beide Schlüsselmengen waren dabei vollständig und identisch,
der vorhandene Paritätstest konnte das nicht sehen. Deshalb liest der neue Test
die Datei roh statt geparst.

### Repositories auf der Festplatte finden

`services/discovery.py` durchläuft einen Ordner und meldet jedes Arbeitsverzeichnis
darunter. Drei Entscheidungen bestimmen das Modul:

- **Der Durchlauf startet nie git.** Git zu jedem Verzeichnis zu befragen hieße
  ein Prozess pro Kandidat, und ein Home-Verzeichnis hat Zehntausende. Erkannt
  wird ein Arbeitsverzeichnis am `.git`-Eintrag, Branch und Remote werden direkt
  aus `.git/HEAD` und `.git/config` gelesen. Gemessen auf dieser Maschine: 42
  Repositories aus 4740 Ordnern in 1,6 Sekunden.
- **In ein gefundenes Repository wird nicht hineingegangen.** Sonst wären
  Submodule und Vendor-Verzeichnisse eigene Projekte.
- **Was sich nicht lesen lässt, wird übersprungen, nicht gemeldet.** Ein
  Home-Verzeichnis hat immer ein paar Ordner ohne Leserecht, und keiner davon ist
  ein Problem des Nutzers.

Der Durchlauf benutzt `os.scandir` mit einem expliziten Stapel statt Rekursion,
folgt keinen Symlinks (`follow_symlinks=False`) und merkt sich aufgelöste Pfade,
damit ein Symlink zurück nach oben nicht denselben Baum zweimal durchläuft.

**`.git` ist nicht immer ein Verzeichnis.** Bei einem Submodul und bei einer
verknüpften Worktree ist es eine Datei mit `gitdir: <pfad>`, und Branch und Remote
liegen dort. `resolve_git_dir()` löst das auf, auch für relative Pfade.

**Der Branchname verliert nur `refs/heads/`.** Am letzten Schrägstrich zu trennen
wäre bequemer und macht aus `feature/login` den Branch `login`, was ein anderer
Branch ist. Das ist beim Bauen tatsächlich passiert und im Screenshot aufgefallen.

### Wann sich die Suche von selbst meldet

Beim ersten Start mit leerer Projektliste, genau einmal, gesteuert über
`discovery_offered` in den Einstellungen.

Ausgelöst wird das aus `showEvent`, nicht aus dem Konstruktor. Ein Fenster, das
gebaut, aber nie gezeigt wurde, hat niemanden davor, den man fragen könnte, und
genau das ist die Lage in den Tests und im Screenshot-Skript. Im Konstruktor
öffnete der Timer dort einen modalen Dialog, auf dessen Klick niemand wartete,
und die Testsuite blieb stehen.

### Wann von selbst aktualisiert wird

Neben dem Intervall und dem Start lösen zwei weitere Dinge eine Aktualisierung
aus, und beide tut der Nutzer nebenbei statt absichtlich. Genau deshalb brauchen
sie eine Bremse.

**Fenster bekommt den Fokus** (`changeEvent`, `QEvent.Type.ActivationChange` plus
`isActiveWindow()`). Liest das ausgewählte Projekt neu, erzeugt die
Gegenüberstellung neu und stößt einen Scan an. Gedrosselt über `RefreshThrottle`
auf höchstens alle zwei Sekunden: zwischen Editor und Branchly hin und her zu
springen darf nicht bei jedem Sprung `git status` bedeuten.

Die Drossel merkt sich **nur den Zeitpunkt, an dem sie ausgelöst hat**, nicht die
abgelehnten Versuche. Andernfalls könnte ständiges Alt-Tabben die Aktualisierung
beliebig lange hinausschieben. Sie rechnet außerdem mit `time.monotonic`, denn
eine Wanduhr, die zurückspringt (Laptop aus dem Ruhezustand, Zeitabgleich),
würde jede Aktualisierung blockieren, bis sie wieder aufgeholt hat.

**Projektwechsel** (`_activate`). Die Panels wurden schon vorher neu gelesen; neu
ist der Scan, der Badges und Serverstand nachzieht. Der ist um 300 ms verzögert,
denn mit den Pfeiltasten durch die Liste zu wandern würde sonst für jedes Projekt
auf dem Weg einen Scan starten. Ein Timer, der bei jedem Wechsel neu anläuft,
lässt am Ende nur das Projekt übrig, auf dem man stehen bleibt.

**Ein abgelehnter Scan wird nachgeholt.** `ScanCoordinator.start()` weist einen
Stapel ab, solange ein anderer läuft, und gab das vorher an niemanden zurück.
`_start_scan()` meldet das jetzt, `_scan_current()` merkt sich den Schlüssel, und
`_on_scan_batch_finished()` holt ihn nach. Ohne das verlöre genau der Wechsel
während eines laufenden Durchlaufs seine Aktualisierung, also der Fall, in dem
man am ehesten wartet.

**GitHub bleibt außen vor.** Netzanfragen gegen ein Limit, für Daten, die sich in
Minuten statt Sekunden ändern, und das Panel hat einen eigenen Knopf.

**Beim Schließen wird nichts Neues mehr angefangen.** `closeEvent` setzt
`_closing`, hält den Verzögerungs-Timer an und verwirft einen vorgemerkten Scan.
Ohne das konnte nach dem `_scans.wait()` noch ein Stapel starten, etwa weil der
Timer gerade ablief oder weil `_on_scan_batch_finished` einen vorgemerkten Scan
nachholte. Qt bricht den Prozess ab, wenn ein Thread seinen Pool überlebt
(`QThread: Destroyed while thread is still running`), und genau das passierte
sporadisch beim Beenden, seit auf Fokus und auf jeden Projektwechsel gescannt
wird.

### Wenn ein Knopf nichts tun kann

Ein Qt-Stylesheet gewichtet wie CSS, und ein ID-Selektor wiegt schwerer als eine
Pseudoklasse. `QPushButton#Primary` schlug also `QPushButton:disabled`, und der
Commit-Knopf blieb in voller Akzentfarbe stehen, während `isEnabled()` längst
`False` war. Das sieht nicht nach „wartet auf eine Eingabe" aus, sondern nach
kaputt.

Jede Steuerung hat deshalb eine eigene `:disabled`-Regel, die ID-Varianten
ausdrücklich mit: `QPushButton#Primary`, `#Danger`, `#Link`, dazu `QToolButton`,
die Eingabefelder, `QCheckBox`, `QRadioButton`, `QLabel`, `QTabBar::tab` und die
Menüeinträge. Die Farben kommen aus drei eigenen Token, `disabled_bg`,
`disabled_text` und `disabled_border`. `disabled_text` ist dunkler als
`text_muted`, denn gedämpfter Text soll noch gelesen werden, ausgegrauter soll
übersprungen werden.

Geprüft wird das gerendert, nicht gelesen: `tests/test_theme.py` zeichnet jede
Steuerung ein- und ausgeschaltet und zählt die abweichenden Pixel. Eine Regel,
die von einer anderen überstimmt wird, steht sonst im Stylesheet und wirkt
trotzdem nicht.

### Erscheinungsbild im Menü statt im Dialog

Hell und Dunkel liegen als abhakbare `QAction` in einer exklusiven
`QActionGroup` unter *Ansicht → Erscheinungsbild*. `_choose_theme()` schreibt die
Einstellung sofort weg und ruft `_apply_theme()`, das Stylesheet,
`refresh_theme_aware()`, Sidebar, Graph und Gegenüberstellung nachzieht.

Der Einstellungsdialog kennt das Feld nicht mehr und gibt `theme` unverändert aus
`self._original` zurück. Ohne das würde ein Besuch im Dialog die Wahl aus dem
Menü mit dem Stand überschreiben, den der Dialog beim Öffnen gesehen hat.

## Themes und Sprachen erweitern

**Theme:** In `config/theme.py` eine `ThemeColors`-Instanz anlegen und in `_THEMES`
eintragen. Kein Widget kennt Hex-Werte; `tests/test_theme.py` prüft, dass jedes
Theme jeden Token definiert, dass jeder Wert eine Farbe ist und dass jede
Steuerung im ausgeschalteten Zustand anders aussieht als im eingeschalteten.

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

`tests/support_github.py` startet einen echten HTTP-Server auf localhost als
Ersatz für api.github.com, statt `requests.Session` zu stubben. Genau die Stellen,
die eine Attrappe verdecken würde, sind die interessanten: dem `Link`-Header
folgen, ein 304 richtig deuten, Rate-Limit-Header lesen, aus einem 422 einen Satz
machen. Das GitHub-Panel wird gegen denselben Server gefahren, damit der ganze
Weg geprüft ist: ein Klick wird zum Request, die Antwort wird zu Zeilen, und auf
einen Schreibzugriff folgt ein Nachladen, das nicht aus dem Cache kommen kann.

Ein Detail, das man einmal falsch macht: das Panel meldet Fehler in einem modalen
Dialog. Im Test würde dessen `exec()` auf einen Klick warten, der nie kommt, also
ersetzt `tests/test_github_ui.py` die `QMessageBox` durch eine, die nur
mitschreibt.

Die Tests der Sicherheitsschicht laufen in der CI **zuerst**, in einem eigenen
Schritt. Fällt dort etwas um, ist der Rest nicht mehr interessant.

## Linting

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
```

Die Regeln stehen in `ruff.toml` und sind danach ausgewählt, was der Code ohnehin
tut, nicht um einen neuen Stil durchzusetzen. Maßstab waren die vorhandenen
`# noqa:`-Kommentare: sie nennen `SLF001`, `ANN`, `BLE001`, `N802` und die
Bandit-Codes, und ein solcher Kommentar bedeutet nur etwas, wenn die Regel auch
eingeschaltet ist.

Zwei Entscheidungen, die man sonst nachschlagen müsste:

- **`S105`, `S106` und `S107` sind global aus.** Sie greifen auf den *Namen* einer
  Variablen, also auch auf `ERROR_NO_TOKEN` und auf eine Themefarbe namens
  `check_pass`. Branchly legt per Entwurf kein Geheimnis in eine Datei, das Token
  liegt im Schlüsselspeicher. Jeder Treffer wäre ein Name, kein Geheimnis.
- **`SLF001` ist in `tests/` aus.** Ein Panel-Test, der nicht in die Liste des
  Panels schauen darf, ist kein Test. Vorher standen 134 Marker „das ist
  Absicht" im Testcode, was den Marker bedeutungslos machte.

ruff ist ein Entwicklungswerkzeug und steht deshalb in `requirements-dev.txt`,
nicht in `requirements.txt`: wer Branchly installiert, soll keinen Linter
mitinstallieren. Die CI lintet vor den Tests.

## Plattformen

Getestet unter Linux. Alle OS-Spezifika liegen hinter `paths.py` (Konfig- und
Cache-Verzeichnisse, venv-Interpreter) und `services/open_with.py` (`xdg-open`,
`os.startfile`, `open`). Die CI-Matrix deckt Ubuntu 22.04, 24.04 und
`windows-latest` mit Python 3.11 und 3.12 ab.
