# Brief 0001 — Le collecteur et son simulateur

> **Décidé le** : 2026-08-12
> **Exécutant** : Claude Code
> **Autorité** : [`COLLECTE.md`](../../COLLECTE.md) — en cas de divergence avec ce
> brief, c'est le contrat qui l'emporte, et ce brief qui est à corriger.
>
> Brief autoportant. **Les exclusions sont aussi contraignantes que le périmètre.**

---

## Contexte

Aucun appareil ne sert encore le contrat. Le firmware de l'horloge sera modifié
lors d'une dépose dont la date dépend d'une livraison de composants.

**Le collecteur ne l'attend pas.** Il est écrit contre le contrat et éprouvé
contre un simulateur. Le jour de la repose, il devient le banc de test du
firmware — au lieu de découvrir les deux côtés en même temps.

Ce brief couvre les deux : le collecteur et le simulateur. **Le simulateur est
écrit en premier**, faute de quoi rien ne permet de vérifier le collecteur.

---

## Périmètre

### Structure

```
collector/     __init__.py  config.py  state.py  fetch.py  archive.py
               report.py  notify.py  main.py
simulator/     __init__.py  server.py  scenarios.py
tests/         test_*.py
config.ini.template
```

Bibliothèque standard uniquement — `urllib`, `csv`, `configparser`, `json`,
`smtplib`, `email`, `unittest`. **Aucun `pip install`, aucun environnement
virtuel.**

### 1. Configuration — `config.py`

Fichier INI **hors dépôt**, chemin passé en argument ou `~/.config/data-collector/config.ini`.
Seul `config.ini.template` est versionné, avec des valeurs factices.

```ini
[device:clock]
url = http://192.168.x.x
timeout_s = 10

[archive]
dir = /var/lib/data-collector
; Avertir si l'archive compressée dépasse cette taille en pièce jointe
max_attachment_mb = 20

[ntfy]
server = https://ntfy.sh
topic = YOUR_NTFY_TOPIC

[smtp]
host = YOUR_SMTP_HOST
port = 587
user = YOUR_SMTP_USER
to = YOUR_EMAIL

[alerts]
unreachable_polls = 3
mute_sensor_hours = 6
```

⚠️ **Aucune valeur réelle, aucune adresse locale, aucun sujet ntfy dans le
`.template`.** Le mot de passe SMTP ne figure pas dans le fichier : il est lu
d'une variable d'environnement, que la minuterie `systemd` fournit.

### 2. État — `state.py`

Un JSON par appareil, à côté de l'archive. **Non versionné.** Contient au minimum :
dernier horodatage collecté, dernière valeur d'`overwritten` observée, nombre
d'échecs consécutifs, horodatage de la dernière alerte de chaque type.

L'état sert à ne pas ré-alerter à chaque passage. **Il ne fait jamais autorité sur
le contenu de l'archive** : si l'état est perdu, il se reconstruit en relisant la
dernière ligne de l'archive.

### 3. Collecte — `fetch.py`

- `since` = dernier horodatage présent dans l'archive de l'appareil. Archive
  absente ⇒ pas de `since`, on prend tout ce que l'appareil détient.
- Boucler `GET /api/history?since=…` **jusqu'à obtenir zéro ligne de données**,
  en repartant du dernier horodatage reçu. Plafonner le nombre d'itérations et
  journaliser si le plafond est atteint.
- Temporisation sur chaque requête. Un échec **n'interrompt pas** le traitement
  des autres appareils.
- Interroger aussi `GET /api/status`.

**Une ligne malformée fait rejeter le lot entier**, avec journalisation et alerte.
Elle n'est ni corrigée, ni devinée, ni ignorée silencieusement. Un collecteur qui
répare ce qu'il ne comprend pas fabrique de la donnée.

### 4. Archive — `archive.py`

L'archive d'un appareil est un **répertoire**, pas un fichier :

```
/var/lib/data-collector/clock/2026-08-12.csv
                             /2027-03-04.csv     ← après extension du schéma
```

Chaque fichier a un **schéma constant** et porte la date de sa première ligne.

**Règle d'écriture : ajout seul, sans aucune exception.** Une ligne écrite n'est
jamais modifiée, et l'en-tête non plus.

**Quand l'appareil sert des colonnes supplémentaires** — extension prévue par
`COLLECTE.md` §3 — le collecteur **ouvre un nouveau fichier** avec le nouvel
en-tête. Il ne réécrit pas l'ancien et ne complète pas les lignes passées.

⚠️ C'est la raison d'être du répertoire. Réécrire l'en-tête d'un fichier existant
serait une modification du passé ; compléter les anciennes lignes serait pire.
Chaque fichier reste un CSV valide, ouvrable seul.

**Si l'appareil sert des colonnes en moins, ou renommées, ou réordonnées** : c'est
une violation du contrat. Rejeter, journaliser, alerter.

**Idempotence.** Une ligne dont l'horodatage existe déjà dans l'archive est
ignorée et journalisée. Deux exécutions consécutives ne produisent jamais de
doublon.

### 5. Détection des manques — dans `archive.py`

- **Trous** : discontinuité des horodatages, en heures. Relevés à l'écriture et
  conservés pour le rapport.
- **Écrasement** : `overwritten` de `/api/status` comparé à la valeur mémorisée.
  Toute augmentation est une **perte définitive** et se rapporte comme telle.
- **Capteur muet** : compteur d'échantillons à zéro alors que d'autres sont non
  nuls, sur plus de `mute_sensor_hours` heures consécutives.
- **Heure non fiable** : `time_trusted` à zéro.

### 6. Rapports — `report.py`

**Tous les chiffres sont calculés en code, de façon déterministe.** Aucun modèle
de langage n'intervient dans ce brief, ni pour calculer, ni pour rédiger. Une
couche de mise en récit pourra être ajoutée plus tard, et n'aura jamais le droit
de produire un chiffre.

**Hebdomadaire** — bref, par ntfy. Signe de vie, heures collectées, trous,
capteurs muets, minimum et maximum extérieurs.

**Mensuel** — par courriel. Moyennes, minima, maxima par grandeur ; comparaison au
mois précédent et au même mois de l'année précédente **lorsque les données
existent** ; bilan des trous et des écrasements.

**Deux pièces jointes :**

| Pièce | Contenu |
|---|---|
| Export du mois | CSV du mois écoulé, au schéma courant — pour la lecture |
| **Archive complète** | **Tous les fichiers de l'archive, compressés** — pour la restauration |

⚠️ **L'archive entière part à chaque envoi mensuel, pas seulement le mois
écoulé.** L'archive vit sur la carte SD du Pi, qui est un point de défaillance
unique ; une sauvegarde reconstituée mois par mois échoue dès qu'un message
manque. **Chaque courriel doit être un point de restauration complet et
autonome.**

Les volumes le permettent largement : de l'ordre d'un méga-octet de CSV par an,
et ce format se comprime au dixième. Le jour où l'archive deviendrait trop grosse
pour un courriel, ce sera une décision à prendre, pas une dégradation à subir —
journaliser un avertissement au-delà d'un seuil configurable.

Cette disposition satisfait le critère de réussite n° 5 d'ADR-0004 du dépôt
horloge — *« une sauvegarde exportée est restaurable sur une installation
vierge »* — **par construction plutôt que par vérification**. Il n'y a rien à
tester périodiquement : chaque exemplaire est complet.

⚠️ **Une comparaison impossible se dit, elle ne s'omet pas.** « Pas de donnée pour
août 2025 » est une information ; une ligne absente du tableau n'en est pas une.

⚠️ **Une période dont la couverture est partielle est annoncée avec sa
couverture.** Une moyenne mensuelle calculée sur 40 % des heures se présente
accompagnée de ces 40 %.

L'export joint est un **artefact dérivé**, régénéré à chaque envoi au schéma
courant. Il n'est pas l'archive et ne la remplace pas.

### 7. Notifications — `notify.py`

- **ntfy** : `POST` sur `{server}/{topic}`, titre et priorité en en-têtes. Aucune
  bibliothèque.
- **Courriel** : `smtplib` en STARTTLS, `email.message.EmailMessage`, pièce jointe
  CSV.

Un échec d'envoi **se journalise et fait sortir en code non nul**. Une
notification perdue en silence annulerait tout l'intérêt du dispositif.

### 8. Points d'entrée — `main.py`

```
python3 -m collector poll             # interroge, archive, alerte si besoin
python3 -m collector report --weekly
python3 -m collector report --monthly
python3 -m collector export --from YYYY-MM --to YYYY-MM
```

Sortie non nulle en cas d'échec, pour que la minuterie `systemd` le consigne.

Fournir les unités `systemd` en exemple, dans `docs/` : minuterie horaire pour
`poll`, hebdomadaire et mensuelle pour les rapports. **Pas de `cron`** : ses
échecs partent dans un courrier local que personne ne lit.

### 9. Simulateur — `simulator/`

Serveur HTTP `http.server` servant `/api/history` et `/api/status` **conformément
au contrat**, avec un scénario sélectionnable.

Scénarios exigés :

| Scénario | Ce qu'il éprouve |
|---|---|
| `nominal` | Cas courant, plusieurs jours d'heures pleines |
| `gap` | Heures manquantes au milieu — lignes absentes, pas vides |
| `dead_sensor` | Champs vides et compteur à zéro pour une grandeur, les autres valides |
| `overwritten` | `overwritten` non nul et croissant |
| `time_untrusted` | `time_trusted` à zéro |
| `unreachable` | Refus de connexion, puis temporisation |
| `schema_extension` | Colonnes supplémentaires en fin de ligne |
| `schema_violation` | Colonne renommée ou réordonnée — doit être rejeté |
| `pagination` | Plus de lignes que le plafond, force la boucle de reprise |
| `malformed` | Ligne tronquée ou champ non numérique — doit être rejeté |
| `empty` | Aucune ligne nouvelle — cas normal, ne doit rien déclencher |

---

## Exclusions

**Pas de base de données.** L'archive est un CSV, l'état un JSON.

**Pas d'ordonnanceur applicatif.** Le déclenchement appartient à `systemd`.

**Pas de dépendance tierce**, y compris pour les graphes. Un rapport mensuel est
un tableau ; s'il faut un jour un graphe, ce sera une décision, pas un ajout.

**Pas de mise en récit par un modèle.** Hors périmètre de ce brief.

**Pas d'interface web, pas de tableau de bord.** C'est le dispositif qui a échoué.

**Ne pas modifier `COLLECTE.md`.** Toute imprécision du contrat se signale, elle ne
se comble pas dans le code.

---

## Critères d'acceptation

| # | Critère | Vérification |
|---|---|---|
| 1 | Deux `poll` consécutifs ne produisent aucun doublon | Scénario `nominal`, deux exécutions, comparer les fichiers |
| 2 | Une heure sans échantillon arrive dans l'archive en champ vide, compteur à zéro | Scénario `dead_sensor` |
| 3 | Un trou reste un trou — aucune ligne fabriquée | Scénario `gap` |
| 4 | Une extension de schéma ouvre un nouveau fichier, l'ancien est inchangé | Scénario `schema_extension`, comparer l'empreinte de l'ancien fichier avant et après |
| 5 | Un schéma violé ou une ligne malformée fait rejeter le lot, sans écriture partielle | Scénarios `schema_violation` et `malformed` |
| 6 | Un appareil injoignable produit une alerte après `unreachable_polls` passages, et une seule | Scénario `unreachable`, cinq passages |
| 7 | Une augmentation d'`overwritten` est rapportée comme une perte | Scénario `overwritten` |
| 8 | La pagination collecte l'intégralité des heures disponibles | Scénario `pagination`, compter les lignes |
| 9 | Un mois à couverture partielle est présenté avec sa couverture | Rapport mensuel sur `gap` |
| 10 | Une comparaison impossible est écrite, pas omise | Rapport mensuel sans année précédente |
| 11 | L'archive jointe au rapport mensuel se restaure seule | Extraire la pièce jointe dans un répertoire vide, relancer `poll` : aucune ligne n'est recollectée |
| 12 | Aucun identifiant ni adresse locale dans les fichiers versionnés | `grep` sur le dépôt avant commit |
| 13 | Aucun import hors bibliothèque standard | Inspection des `import` |

Les critères 3, 4, 5 et 10 sont ceux qui distinguent ce collecteur d'un script
qui marche. **Un dispositif qui comble, répare ou omet en silence est
précisément ce que ce projet a payé quatre mois.**

---

## Ordre suggéré

1. Simulateur et scénarios — rien n'est vérifiable avant
2. `config`, `state`, `fetch`
3. `archive`, avec les critères 1 à 5
4. Alertes et `notify`
5. Rapports, avec les critères 9 et 10
6. Unités `systemd` d'exemple

**Point d'arrêt après l'étape 3.** L'archive est la pièce dont dépend tout le
reste, et ses règles d'écriture sont irréversibles une fois des données
accumulées.
