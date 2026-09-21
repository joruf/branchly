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

## Alle Projekte auf einmal aktualisieren

**Alle Projekte aktualisieren** unten in der Liste (oder *Projekt → Alle Projekte
aktualisieren*) holt für jedes Projekt die Änderungen vom Server. Ein Fenster sagt
vorher, was passiert, zeigt den Fortschritt und danach für **jedes** Projekt
einzeln, was daraus geworden ist.

Der Vorgang **spult nur vor**. Er führt nichts zusammen, überschreibt nichts und
kann keinen Konflikt hinterlassen. Was nicht eindeutig vorzuspulen ist, bleibt
unangetastet und steht namentlich in der Liste:

| Steht da | Heißt |
|---|---|
| *3 neue Commits* | Vorgespult, das Projekt ist auf dem Stand des Servers |
| *schon aktuell* | Der Server hatte nichts Neues |
| *eigene Änderungen sind noch nicht gespeichert* | Im Ordner liegt unfertige Arbeit |
| *eigene Commits sind noch nicht gesendet* | Der Branch ist auseinandergelaufen — Vorspulen ist unmöglich |
| *Konflikte warten auf eine Entscheidung* | Erst den Konflikt-Assistenten durchlaufen |
| *kein Branch ausgewählt* | Detached HEAD |
| *der Branch folgt keinem Server-Branch* | Kein Upstream gesetzt |
| *kein Server* | Rein lokales Projekt |

Übersprungene Projekte werden trotzdem **geholt** (`fetch`). Das rührt den Ordner
nicht an, sorgt aber dafür, dass hinterher „3 warten auf dem Server" dasteht statt
eines veralteten Badges. Ein solches Projekt aktualisierst du danach einzeln über
**Änderungen vom Server holen** — dort führt Branchly bei Bedarf zusammen und
öffnet den Konflikt-Assistenten.

Während der Lauf arbeitet, lässt sich das Fenster nicht schließen: es schreibt in
die Ordner, und ein halb fertiges Projekt ohne jemanden, der zusieht, wäre die
schlechtere Variante.

## Änderungen speichern

1. Dateien anhaken, die zusammengehören
2. Kurzfassung schreiben (Pflicht), Beschreibung optional
3. **„n Datei(en) in <branch> speichern"**

„Stattdessen zum letzten Commit hinzufügen" hängt die Auswahl an den vorherigen
Commit, statt einen neuen anzulegen.

### Die Auswahl bleibt erhalten

Neue Änderungen sind immer angehakt. Nimmst du einen Haken weg, merkt Branchly
sich das: nach dem Schließen und Öffnen ist die Datei wieder abgewählt. Gemerkt
wird nur, was du abgewählt hast, nicht was angehakt ist, denn alles andere ist
ohnehin dabei und eine frisch geänderte Datei soll nicht erst gesucht werden
müssen.

Die Merkung überlebt auch, dass die Datei zwischendurch aus der Liste
verschwindet, etwa weil du die Änderung verworfen hast und später erneut
dieselbe Datei anfasst.

Sobald eine Datei in einem Commit gelandet ist, ist die Sache erledigt und
Branchly vergisst sie. Eine spätere, ganz andere Änderung an derselben Datei ist
also wieder angehakt.

### Was in der Liste steht

Rechts an jeder Zeile steht ein Zeichen, das sagt, was mit der Datei passiert ist,
genau wie in GitHub Desktop:

| Zeichen | Bedeutung |
| --- | --- |
| `+` | neu angelegt, git kannte sie vorher nicht |
| `•` | ersetzt, es gab sie schon |
| `−` | gelöscht |
| `→` | umbenannt oder verschoben |
| `!` | Konflikt, muss erst entschieden werden |

Die Farbe der Zeile sagt dasselbe noch einmal, damit es auch erkennbar bleibt,
wenn die Zeichen schwer zu unterscheiden sind.

### Rechtsklick auf eine Datei

**Datei öffnen** (Standardprogramm), **Im Ordner zeigen**, **Pfad kopieren**,
**Ignorieren…**, **Änderungen verwerfen**. Verwerfen fragt vorher nach und lässt
sich nicht rückgängig machen.

### Ignorieren

Unter **Ignorieren…** stehen bis zu drei Einträge, je nachdem, worauf du geklickt
hast:

| Eintrag | Schreibt nach `.gitignore` |
| --- | --- |
| Nur diese Datei | `/pfad/zur/datei.txt` |
| Alle `*.log`-Dateien | `*.log` |
| Den ganzen Ordner `build/` | `/build/` |

Der Eintrag für eine einzelne Datei bekommt einen führenden Schrägstrich. Ohne
ihn würde `notizen.txt` auch `doku/notizen.txt` treffen, und gemeint war die eine
Zeile, auf die du geklickt hast. Sonderzeichen im Dateinamen (`*`, `?`, `[`, ein
führendes `#`) werden maskiert, damit der Eintrag genau diese Datei trifft und
keine andere.

`.gitignore` wird angelegt, falls es sie noch nicht gibt, sonst wird unten
angehängt. Steht der Eintrag schon drin, sagt Branchly das und schreibt nichts
doppelt.

Eine Datei, die bereits unter Versionskontrolle steht, verschwindet dadurch
**nicht**. `.gitignore` gilt nur für Dateien, die git noch nicht kennt. Der
Eintrag wird trotzdem geschrieben, damit er greift, sobald die Datei aus der
Versionskontrolle genommen wird.

## Gegenüberstellung

Zwei Einstellungen, unabhängig voneinander:

**Darstellung** — Nebeneinander oder Eine Spalte, „Abstände ignorieren",
„Geänderte Wörter hervorheben".

Bei **Nebeneinander** stehen zwei getrennte Ansichten: links der alte Stand,
rechts der neue. Beide bleiben sichtbar, auch wenn der Platz knapp wird; keine
der beiden Seiten verschwindet oder wird zusammengeschoben. Jede Seite hat einen
eigenen waagerechten Scrollbalken für lange Zeilen, und beide sind gekoppelt:
egal welchen du benutzt, es scrollen immer beide Seiten mit, sonst würdest du
zwei Stellen vergleichen, die nichts miteinander zu tun haben. Senkrecht
scrollen beide ebenfalls gemeinsam, damit die Zeilen auf gleicher Höhe bleiben.

Die Trennlinie zwischen den beiden Seiten lässt sich ziehen, wenn eine Seite mehr
Platz braucht als die andere.

**Vergleichen** — was gegen was gehalten wird:

| Auswahl | Vergleicht |
|---|---|
| Deine Bearbeitung ↔ letzter Stand | Der übliche Fall |
| Deine Bearbeitung ↔ bereit zum Speichern | Was noch nicht angehakt wirkt |
| Bereit zum Speichern ↔ letzter Stand | Was der nächste Commit enthält |
| Zwei ausgewählte Stände | Im Graph zwei Commits markieren |
| Zwei Branches | Zwei Zweige vollständig |

### Dateien ohne Zeilen

Nicht jede Datei besteht aus Zeilen, die sich vergleichen lassen. Gegenübergestellt
werden sie trotzdem, denn eine neue Fassung ist eine Änderung, und „das ist keine
Textdatei" sagt darüber nichts aus.

**Bilder** stehen als Vorher und Nachher nebeneinander, beide auf Fenstergröße
verkleinert, wenn sie zu groß sind.

**Alles andere** bekommt statt des Bildes ein Feld mit dem Dateityp, und darunter
stehen auf beiden Seiten die zwei Angaben, die es zu jeder Datei gibt: **Größe**
und **wann sie geschrieben wurde**. Darunter steht in einem Satz, was sich
geändert hat, etwa „11 KB größer als vorher".

Woher das Datum kommt, hängt von der Seite ab:

| Seite | Größe | Datum |
| --- | --- | --- |
| Auf deiner Festplatte | die echte Dateigröße | wann die Datei zuletzt geschrieben wurde |
| Ein Commit | die Größe im Repository | das Datum des Commits, der die Datei zuletzt geändert hat |
| Bereit zum Speichern | die Größe im Index | wann die Datei beim Bereitstellen geschrieben wurde |

Lässt sich ein Datum nicht ermitteln, steht dort ein Strich. Ein erfundenes Datum
in einer Gegenüberstellung wäre schlimmer als gar keins.

Eine Seite, die es nicht gibt, sagt das ausdrücklich: eine gerade erst angelegte
Datei hat kein „Vorher", und das ist eine Information, keine Lücke.

Sehr große Diffs werden gekürzt, mit Hinweis. Bei einem sehr großen Bild wird die
Vorschau ausgelassen, die Größe und das Datum stehen trotzdem da.

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

## GitHub

Der Reiter *GitHub* zeigt alles, was das ausgewählte Projekt auf dem Server hat,
und lässt es auch bearbeiten. Nur für Projekte auf GitHub und nur mit hinterlegtem
Zugriffstoken (Einstellungen → GitHub). Ohne Token oder bei GitLab und selbst
gehosteten Servern sagt das Panel das ausdrücklich, alles andere funktioniert
normal weiter.

### Das Token

Ein Token legst du auf github.com unter *Settings → Developer settings →
Personal access tokens* an. Es landet im Schlüsselspeicher des Systems, nie in
einer Datei. Branchly prüft es sofort und zeigt, als wer du angemeldet bist.

Welche Berechtigungen es braucht, hängt davon ab, was du tun willst:

| Berechtigung | Wofür |
| --- | --- |
| `repo` | alles Lesen, Issues, Pull Requests, Releases, Einstellungen |
| `workflow` | Actions-Läufe neu starten, abbrechen, löschen |
| `delete_repo` | ein Repository löschen |
| `read:org` | Repositories in einer Organisation anlegen |

Fehlt eine Berechtigung, lehnt GitHub die Aktion ab und Branchly sagt, dass
vermutlich dem Token etwas fehlt. Ein fein granulares Token (*fine-grained*)
meldet seine Rechte nicht, dort lässt sich das vorher nicht prüfen.

### Pull Requests

Der Filter oben schaltet zwischen offenen, geschlossenen und allen. Die Auswahl
lädt darunter Beschreibung, Labels, Zuweisungen, Prüfungen und Kommentare.

Möglich sind: anlegen, Titel, Text und Zielbranch ändern, kommentieren, prüfen
(zustimmen, Änderungen anfordern, nur kommentieren), eine Prüfung bei jemandem
anfordern, zusammenführen, schließen, wieder öffnen, einen Entwurf freigeben oder
wieder zum Entwurf machen, und den Branch auschecken.

Die Detailansicht listet außerdem die geänderten Dateien mit ihren Zeilenzahlen
und die Commits des Pull Requests.

Beim Zusammenführen wählst du die Methode und kannst den Quellbranch gleich
danach löschen lassen. Angeboten werden nur die Methoden, die das Repository
erlaubt; eine abgeschaltete steht grau da und der Tooltip sagt, warum. Löscht das
Repository gemergte Branches ohnehin selbst, ist das Häkchen schon gesetzt. Branchly schickt dabei den Commit mit, den es zu
verschmelzen glaubt. Hat jemand in der Zwischenzeit gepusht, lehnt GitHub den
Merge ab, statt etwas anderes zusammenzuführen als das, was du gesehen hast.

Ein Entwurf lässt sich nicht zusammenführen. Der Knopf ist dann abgeschaltet und
der Tooltip sagt, warum.

### Issues

Wie bei den Pull Requests filtert der Knopf oben nach Zustand. Anlegen, Titel und
Text ändern, Labels, Zuweisungen und Meilenstein setzen, kommentieren, schließen
und wieder öffnen.

Beim Schließen unterscheidet Branchly die beiden Gründe, die GitHub kennt:
*erledigt* und *nicht geplant*. Der zweite steht im Menü „Mehr".

Unter „Mehr → Labels und Meilensteine…" lassen sich beide anlegen, umbenennen
und löschen. Ein gelöschtes Label verschwindet aus jedem Issue, das es trägt; ein
gelöschter Meilenstein lässt die Issues stehen und nimmt ihnen nur die Zuordnung.
Für einen abgeschlossenen Meilenstein ist *Schließen* meist das Richtige, denn
dann bleibt er in der Historie stehen.

### Releases

Anlegen mit Tag, Titel und Versionshinweisen, wahlweise als Entwurf oder als
Vorabversion, und auf Wunsch schreibt GitHub die Änderungsliste selbst. Bestehende
Releases lassen sich ändern und löschen, Dateien anhängen und wieder entfernen.

Ein gelöschtes Release nimmt sein Tag nicht mit. Das ist Absicht: ein Tag, das
jemand schon geholt hat, kommt durch Neuanlegen nicht zurück. Das Tag selbst
entfernst du über „Mehr → Tag löschen…".

„Mehr → Änderungsliste von GitHub holen…" lässt GitHub die Liste der Änderungen
seit dem vorherigen Tag schreiben und öffnet damit den Bearbeiten-Dialog, sodass
du sie noch anpassen kannst, bevor sie gespeichert wird.

### Actions

Die Läufe der Workflows, neuester zuerst, wahlweise nur für den aktuellen Branch.
Die Auswahl zeigt die einzelnen Jobs mit ihrem Ergebnis. Ein fertiger Lauf lässt
sich neu starten, wahlweise nur mit den fehlgeschlagenen Jobs, ein laufender
abbrechen, ein alter aus der Liste löschen.

Über „Mehr → Workflow starten…" lässt sich ein Workflow von Hand anstoßen. Das
geht nur bei Workflows, die `workflow_dispatch` deklarieren; bei allen anderen
lehnt GitHub es mit einer Begründung ab, die Branchly weiterreicht.

Die vollständigen Logs bleiben im Browser. Branchly zeigt pro Job das Ergebnis,
ein Log-Betrachter hätte ein ZIP-Archiv auspacken müssen und wäre ein eigenes
Fenster geworden.

### Repository-Einstellungen

Der Knopf *Repository-Einstellungen…* oben rechts öffnet Beschreibung, Website,
Themen, Standard-Branch, Sichtbarkeit, die Schalter für Issues, Wiki,
Projekt-Boards und Diskussionen, die erlaubten Merge-Methoden sowie das
Archivieren. Gesendet wird nur, was du wirklich geändert hast.

Im selben Dialog stehen die Personen mit Zugriff. Jemanden einladen geht mit
Kontoname und Zugriffsstufe (Lesen, Triage, Schreiben, Verwalten,
Administrieren), Zugriff entziehen mit Auswahl und Knopf. Eine Einladung wirkt
erst, wenn die eingeladene Person sie annimmt, deshalb taucht sie nicht sofort in
der Liste auf.

Branch-Schutzregeln sind bewusst nicht dabei: GitHub hat dafür inzwischen zwei
parallele Systeme (klassische Protection und Rulesets), und ein Dialog, der nur
eines davon kennt, richtet mehr Schaden an als er nützt.

Im selben Dialog liegt *Repository löschen…*. Das ist die einzige Aktion in
Branchly, die sich nicht rückgängig machen lässt, deshalb genügt dort kein Ja/Nein:
der vollständige Name muss eingetippt werden, genau wie auf github.com. Danach
fragt Branchly, ob auch die lokale Kopie aus der Projektliste verschwinden soll.
Der Ordner auf der Festplatte bleibt in jedem Fall liegen.

Darf das Konto die Einstellungen nicht ändern, öffnet sich der Dialog trotzdem,
zeigt aber alles gesperrt und sagt den Grund. Ein leerer Dialog oder ein Fehler
nach dem Speichern wäre die schlechtere Antwort.

### Neues Repository auf GitHub

*Projekt → Neues Repository auf GitHub…* legt eines an: Name, Beschreibung,
persönliches Konto oder Organisation, privat oder öffentlich, `.gitignore`-Vorlage
und Lizenz. Voreingestellt ist **privat**, denn ein versehentlich öffentliches
Repository lässt sich nicht ungesehen machen, der umgekehrte Fehler kostet einen
Klick.

Ist „Direkt nach dem Anlegen klonen" angehakt, öffnet sich danach der gewohnte
Klon-Dialog mit bereits eingetragener Adresse.

### Ein vorhandenes Repository von GitHub klonen

Im Klon-Dialog steht neben dem Adressfeld *Von GitHub…*. Der Knopf lädt alle
Repositories, die dein Token sehen kann, mit Suche über Name und Beschreibung,
und trägt die Klon-Adresse des gewählten ein. Das ist die einzige Stelle, an der
das ganze Konto aufgelistet wird, und sie ist dort, weil „welches meiner
Repositories" genau beim Klonen die eigentliche Frage ist.

## Branchly aktuell halten

Beim Start fragt Branchly einmal am Tag bei GitHub nach, ob es eine neuere Version
von sich selbst gibt. Findet es eine, erscheint über den Panels ein Streifen:
**Installieren und neu starten** oder **Jetzt nicht**. Ohne Neuigkeit sagt es
nichts — eine Meldung „alles beim Alten" braucht niemand.

„Jetzt nicht" heißt wirklich nur *jetzt* nicht: der Fund bleibt gemerkt, und beim
nächsten Start steht der Streifen wieder da — ohne dass dafür erneut jemand gefragt
werden muss. Weg ist er erst, wenn du das Update installiert hast.

Sofort nachfragen: **Hilfe → Nach Updates suchen…**. Dort steht, welcher Stand
installiert und welcher verfügbar ist — und unter **Was ist neu** alle Änderungen
seit deinem Stand, nicht nur die letzte. Sind es mehr als zehn, nennt die Liste am
Ende die Zahl der übrigen.

Ein eigener, noch nicht gepushter Commit im Branchly-Ordner ist **kein** Update.
Branchly vergleicht nicht bloß „gleicher Commit oder nicht", sondern fragt, ob der
Server wirklich etwas hat, was dir fehlt.

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
