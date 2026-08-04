# Branchly — Handbuch

Dieses Handbuch beschreibt jede Funktion in der Reihenfolge, in der man ihr im
Programm begegnet. Git-Vorwissen wird nicht vorausgesetzt.

## Die drei Bereiche

Links die **Projektliste**, in der Mitte **Änderungen / Graph / Pull Requests**,
rechts die **Gegenüberstellung**. Die Trennlinien lassen sich verschieben; die
Fenstergröße und -position werden beim Beenden gespeichert.

## Projekte

### Hinzufügen und Klonen

- **Hinzufügen** wählt einen Ordner, in dem schon ein Git-Projekt liegt. Du darfst
  auch einen Unterordner wählen — Branchly findet den obersten Ordner selbst.
- **Klonen** holt ein Projekt von einem Server.

Beim Klonen prüft Branchly die Adresse, während du tippst:

| Meldung | Bedeutung |
|---|---|
| „Diese Adresse lässt sich nicht verwenden" | Tippfehler. Erwartet werden `https://…` oder `git@server:nutzer/projekt.git` |
| „Diese Adresse ist nicht sicher" | Die Adresse enthält Zeichen oder Optionen, mit denen Git etwas anderes täte als verlangt. Branchly verweigert sie |
| „Dieser Ordner ist nicht leer" | Es liegen schon Dateien darin. Klonen ist möglich, siehe unten |
| „Dieser Ordner ist schon ein Git-Projekt" | Hier würde ein Projekt im Projekt entstehen. Nimm „Hinzufügen" |

**Klonen in einen Ordner, in dem schon etwas liegt:** Alles Vorhandene bleibt
erhalten, die Projektdateien kommen daneben. Hätte eine Datei aus dem Projekt
denselben Namen wie eine vorhandene, bricht Git ab und ändert nichts — du
verlierst also nichts, auch wenn du dich verklickst.

### Kategorien

Rechtsklick in die Liste oder auf eine Kategorie:

- **Neue Kategorie** anlegen
- **In Kategorie verschieben** — auch direkt in eine neu angelegte
- **Umbenennen** — die Projekte wandern mit
- **Entfernen** — die Projekte bleiben, sie landen unter „Ohne Kategorie"
- **Alle auf-/zuklappen**

Der Zustand jeder Kategorie wird gespeichert. Eine **Suche zeigt auch Treffer in
zugeklappten Kategorien** — sonst wäre nicht nachvollziehbar, warum ein Projekt
fehlt.

### Sternchen und Sortierung

Das Sternchen links neben dem Namen heftet ein Projekt **innerhalb seiner
Kategorie** nach oben. Das gilt in jeder Sortierung — dafür ist es da.

Sortierungen: Name A–Z, Name Z–A, neueste Änderung zuerst, zuletzt geöffnet
zuerst, Projekte mit Änderungen zuerst, eigene Reihenfolge.

### Die Badges

| Badge | Bedeutung |
|---|---|
| `!` rot | Konflikt — eine Entscheidung fehlt |
| `●` gelb | Geänderte Dateien im Projekt |
| `↓` blau | Auf dem Server liegt Neues |
| `↑` gelb | Commits, die noch nicht gesendet sind |
| `!` allein | Der Ordner ist verschwunden |

Unter der Liste steht die Zusammenfassung über alle Projekte, z. B.
„2 Projekte mit Änderungen · 1 Projekt mit Neuigkeiten vom Server".

## Prüfen, was neu ist

- **Alle Projekte prüfen** unten in der Liste
- **Dieses Projekt jetzt prüfen** im Rechtsklickmenü
- **Automatisch** in Einstellungen → Automatische Prüfung: aus, 5, 15, 30, 60
  oder 120 Minuten

Die Online-Prüfung fragt den Server nur, welche Stände er hat. Sie lädt nichts
herunter und verändert dein Projekt nicht. Wer ganz ohne Netz arbeiten will,
schaltet „Auch die Server fragen" aus; dann werden nur die lokalen Ordner
angesehen.

## Änderungen speichern

1. Dateien anhaken, die zusammengehören
2. Kurzfassung schreiben (Pflicht), Beschreibung optional
3. **„n Datei(en) in <branch> speichern"**

„Stattdessen zum letzten Commit hinzufügen" hängt die Auswahl an den vorherigen
Commit, statt einen neuen anzulegen.

Rechtsklick auf eine Datei: **Datei öffnen** (Standardprogramm), **Im Ordner
zeigen**, **Pfad kopieren**, **Änderungen verwerfen**. Verwerfen fragt vorher
nach und lässt sich nicht rückgängig machen.

## Gegenüberstellung

Zwei Einstellungen, unabhängig voneinander:

**Darstellung** — Nebeneinander oder Eine Spalte, „Abstände ignorieren",
„Geänderte Wörter hervorheben".

**Vergleichen** — was gegen was gehalten wird:

| Auswahl | Vergleicht |
|---|---|
| Deine Bearbeitung ↔ letzter Stand | Der übliche Fall |
| Deine Bearbeitung ↔ bereit zum Speichern | Was noch nicht angehakt wirkt |
| Bereit zum Speichern ↔ letzter Stand | Was der nächste Commit enthält |
| Zwei ausgewählte Stände | Im Graph zwei Commits markieren |
| Zwei Branches | Zwei Zweige vollständig |

Bilder werden als **Vorher/Nachher** gezeigt. Bei anderen Binärdateien sagt
Branchly, dass es keine Zeilen vergleichen kann, statt Datenmüll anzuzeigen.
Sehr große Diffs werden gekürzt, mit Hinweis.

## Graph

Jede Linie ist ein Branch, jeder Punkt ein gespeicherter Stand, der neueste oben.
Der Ring markiert, wo du stehst. In Klammern stehen Branch- und Tag-Namen.

Ein Klick zeigt rechts **alle Änderungen dieses Commits**. Rechtsklick:

| Aktion | Wirkung |
|---|---|
| Diesen Stand ansehen | Springt zu diesem Stand. Branchly erklärt vorher, dass dabei kein Branch aktiv ist, und bietet an, stattdessen einen anzulegen |
| Hier einen neuen Branch beginnen | Neuer Branch ab diesem Stand |
| In `<branch>` zusammenführen | Merge in den aktuellen Branch |
| Diese Änderung auf `<branch>` übernehmen | Cherry-pick |
| Diese Änderung rückgängig machen | Neuer Commit, der zurücknimmt. Der alte bleibt in der Historie |
| Branch hierher setzen… | Drei Varianten: alles behalten, Dateien behalten, oder **meine Arbeit wegwerfen** |
| Diesem Stand einen Namen geben | Tag |
| Stand-Kennung kopieren | SHA in die Zwischenablage |
| Zum Vergleich markieren | Danach an einem zweiten Commit „Mit dem markierten Stand vergleichen" |

„Branch hierher setzen und meine Arbeit wegwerfen" ist die einzige Aktion, die
Arbeit endgültig vernichtet. Sie fragt in klaren Worten nach.

## Konflikte auflösen

Wenn beim Holen oder Zusammenführen dieselben Stellen von beiden Seiten geändert
wurden, erscheint oben ein Hinweis mit **„Los geht's"**.

Der Assistent geht die Stellen einzeln durch:

- Kopf: „Entscheidung 2 von 5", Dateiname, Grund in Klartext
- Drei Spalten: **Deine Änderung**, **Vom Server**, **Ergebnis**
- Vier Buttons: **Meine nehmen**, **Andere nehmen**, **Beide nehmen**,
  **Selbst schreiben**
- **Das für alle übrigen so machen** als Abkürzung
- **Zurück** und **Weiter**, rechts die Zahl der offenen Entscheidungen

Bilder und andere Binärdateien lassen sich nicht Zeile für Zeile zusammenführen —
dort wählst du, welche Datei komplett bleibt. Hat eine Seite die Datei gelöscht
und die andere sie geändert, lautet die Frage „behalten oder löschen".

Geschrieben wird erst, wenn **alle** Entscheidungen getroffen sind.
**Abbrechen und zurücksetzen** stellt den Stand von vorher wieder her; deine
eigene gespeicherte Arbeit bleibt dabei unberührt.

## Branches und Server

Oben rechts: **Neuer Branch**, **Branch wechseln**, **Server prüfen**,
**Änderungen vom Server holen**, **Änderungen zum Server senden**. Auf den
letzten beiden steht die Anzahl, sobald etwas anliegt.

Ein Branchname wird geprüft, bevor Git ihn sieht. Aus „Fix login bug" macht
Branchly `fix-login-bug`.

Zum Wechseln muss der Arbeitsstand sauber sein — sonst wärest du dir selbst im
Weg. Speichere oder verwirf vorher.

Lehnt der Server einen Push ab, heißt das fast immer: jemand anderes war
schneller. Erst holen, dann senden.

## Pull Requests und Issues

Nur für Projekte auf GitHub und nur mit hinterlegtem Zugriffstoken
(Einstellungen → GitHub). Ohne Token oder bei GitLab und selbst gehosteten
Servern sagt das Panel das ausdrücklich; alles andere funktioniert normal weiter.

Ein Token legst du auf github.com unter *Settings → Developer settings →
Personal access tokens* an. Es landet im Schlüsselspeicher des Systems, nie in
einer Datei. Branchly prüft es sofort und zeigt, als wer du angemeldet bist.

## Branchly aktuell halten

Beim Start fragt Branchly einmal am Tag bei GitHub nach, ob es eine neuere Version
von sich selbst gibt. Findet es eine, erscheint über den Panels ein Streifen:
**Installieren und neu starten** oder **Jetzt nicht**. Ohne Neuigkeit sagt es
nichts — eine Meldung „alles beim Alten" braucht niemand.

Sofort nachfragen: **Hilfe → Nach Updates suchen…**. Dort steht, welcher Stand
installiert und welcher verfügbar ist, mit der Kurzfassung der neuesten Änderung.

Beim Installieren holt Branchly die neue Version, schließt sich und startet
wieder. Was dabei **nicht** passiert:

- Deine Projekte, Einstellungen und dein Token werden nicht angefasst. Sie liegen
  außerhalb des Programmordners.
- Eigene, nicht eingecheckte Änderungen im Branchly-Ordner werden nicht
  überschrieben. Stattdessen bricht das Update ab und sagt genau das. Speichere
  sie erst als Änderung ein oder verwirf sie.
- Nichts wird ohne Klick installiert. Die Prüfung schaut nur.

Die Prüfung beim Start lässt sich in *Einstellungen → Automatische Prüfung*
abschalten. Wie oft sie höchstens läuft, steht als `update_check_hours` in
`settings.json` — `0` heißt „bei jedem Start".

## Einstellungen

| Reiter | Inhalt |
|---|---|
| Allgemein | Sprache, Erscheinungsbild, Standard-Sortierung, Nachfragen vor Verlust |
| Automatische Prüfung | Intervall, ob dabei die Server gefragt werden, Update-Suche beim Start |
| Gegenüberstellung | Standardansicht, Abstände, Wort-Hervorhebung |
| GitHub | Token, GitHub-Funktionen ein/aus, Autorenbilder |

Ein Sprachwechsel greift beim nächsten Start. Das Erscheinungsbild wechselt
sofort.

## Wo liegen die Daten

| Was | Linux | Windows |
|---|---|---|
| Einstellungen | `~/.config/branchly/settings.json` | `%APPDATA%\branchly\settings.json` |
| Projektliste | `~/.config/branchly/repos.json` | `%APPDATA%\branchly\repos.json` |
| Autorenbilder | `~/.cache/branchly/avatars/` | `%LOCALAPPDATA%\branchly\cache\avatars\` |
| GitHub-Token | Schlüsselspeicher (libsecret) | Credential Manager |

Branchly speichert **keine** Git-Zugangsdaten. Das macht Gits eigener
Credential-Helper.

## Aufrufoptionen

```
branchly.sh [--language de|en] [--theme dark|light] [--repo PFAD] [--version]
```

`--language` und `--theme` gelten nur für diesen Start und überschreiben die
gespeicherte Einstellung nicht.
