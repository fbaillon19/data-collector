# CLAUDE.md — Contexte du projet data-collector

> **À lire en premier, avant toute action.**
> En cas de contradiction avec une autre source, ce fichier fait foi, à
> l'exception de [`COLLECTE.md`](COLLECTE.md) : le contrat prime sur tout.

---

## 1. Le projet en bref

Tâche planifiée qui interroge des appareils domestiques, complète une archive CSV
et envoie des rapports. Voir [`README.md`](README.md).

**Le contrat [`COLLECTE.md`](COLLECTE.md) est l'autorité de référence.** Il est
implémenté des deux côtés : par le firmware de chaque appareil, par ce collecteur.
Une modification du contrat engage tous les appareils — elle se décide, elle ne se
constate pas.

### Ce qui existe ailleurs

| Dépôt | Rôle |
|---|---|
| `smart-led-clock` | Premier appareil collecté. Y vivent l'intention du système (`design/INTENTION.md`), la décision de bascule en pull (`design/adr/0005-*`) et le brochage |
| Station météo | Second appareil envisagé. N'existe pas encore |

---

## 2. Contraintes structurantes

**Zéro dépendance tierce.** Bibliothèque standard Python uniquement. Pas de
`requests`, pas de `pandas`, pas d'ordonnanceur applicatif. Le déclenchement est
confié au système — minuterie `systemd` de préférence à `cron`, dont les échecs
partent dans un courrier local que personne ne lit.

Cette règle n'est pas de l'ascétisme : elle vient de `INTENTION.md` du dépôt
horloge, qui compte parmi les échecs « une dépendance qu'on ne sait pas remonter
soi-même ».

**L'absence n'est jamais une valeur.** Un champ vide reste vide. Un compteur
d'échantillons nul signifie « non mesuré » et se distingue d'un zéro mesuré. Ne
jamais combler, lisser ou interpoler — y compris dans un graphe.

**L'archive est en ajout seul.** Une ligne écrite n'est jamais réécrite. Une
correction se fait par une ligne nouvelle, jamais par modification du passé.

**Les colonnes ne sont qu'ajoutées, en fin de ligne.** Jamais insérées,
réordonnées ni renommées — voir `COLLECTE.md` §3. Un fichier couvrant des années
contient plusieurs générations de schéma.

**L'absence de réponse est un événement.** Un appareil injoignable se journalise
et se rapporte. Il ne se traite jamais comme « rien de neuf ».

---

## 3. Sécurité

**Aucun identifiant, aucune adresse locale, aucun sujet ntfy dans un fichier
versionné** — y compris en commentaire et à titre d'exemple. Utiliser
`192.168.x.x`, `YOUR_SMTP_HOST`, `YOUR_NTFY_TOPIC`.

La configuration réelle vit dans un fichier hors dépôt, ignoré par Git, dont seul
le `.template` est versionné.

**Un sujet ntfy public est une adresse devinable** : qui le connaît reçoit les
notifications. À traiter comme un secret.

**La même règle vaut pour les rapports, messages de commit et échanges.** Une
adresse IP locale est un élément de topologie réseau : citer « l'adresse de
l'appareil » plutôt que sa valeur.

Le dépôt horloge a connu une exposition d'identifiants dans son historique public.
Ne pas reproduire.

---

## 4. Conventions

**Commits** — format `type: sujet`, types `feat`, `fix`, `refactor`, `docs`,
`test`, `build`, `ci`, `security`. Un commit = un changement cohérent.

**Code** — commentaires et identifiants en anglais. Constantes en
`MAJUSCULES_AVEC_UNDERSCORES`, fonctions en `snake_case`, classes en
`PascalCase`. Indentation de 4 espaces.

**Documentation** — un commentaire périmé est un piège actif. En modifiant un
bloc, vérifier son commentaire.

---

## 5. Répartition Cowork / Claude Code

Même règle que le dépôt horloge, **la frontière est le type de fichier** :

| | Cowork | Claude Code |
|---|---|---|
| Rôle | Analyse, décisions, spécifications | Implémentation, tests, CI, Git |
| Écrit dans | `*.md` à la racine, `docs/`, `design/` | `collector/`, `simulator/`, `tests/`, CI |
| Commite | produit les commandes, l'auteur les exécute | son propre périmètre |

⚠️ **`COLLECTE.md` est un cas particulier.** C'est un contrat partagé avec les
dépôts d'appareils : le modifier engage des implémentations qui vivent ailleurs.
Toute évolution se décide, se date, et respecte la règle d'extension du §3 du
contrat.

---

## 6. Réflexes attendus

- **Le simulateur d'abord.** Toute règle de traitement de l'absence se vérifie
  contre un cas produit par le simulateur, pas contre un appareil réel.
- **Ne pas élargir le périmètre.** Une anomalie hors périmètre se signale, elle ne
  se corrige pas dans la foulée.
- **Un commentaire n'est pas une preuve.** Vérifier le code.
- **Une mesure absente ne doit jamais ressembler à une mesure.** C'est le défaut
  qui a fait naître ce dépôt : quatre mois de valeurs fabriquées, indiscernables
  de mesures réelles, découvertes par hasard.
