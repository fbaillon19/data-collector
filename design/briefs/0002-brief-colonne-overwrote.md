# Brief 0002 — La colonne `overwrote`

> **Autorité** : [`COLLECTE.md`](../../COLLECTE.md) — en cas de divergence avec ce
> brief, c'est le contrat qui l'emporte.
> **Décidé le** : 2026-08-13 · **Exécutant** : Claude Code
>
> ⚠️ **À exécuter avant que le firmware serve cette colonne** — brief 0007 partie F
> du dépôt `smart-led-clock`.

---

## Contexte

Le contrat définissait un bit `flags` signifiant « cet enregistrement a écrasé un
enregistrement non collecté », **sans jamais l'exposer en CSV**. L'information
existait dans l'anneau de l'appareil et n'atteignait pas le collecteur.

Elle est désormais exposée sous la colonne `overwrote`, en fin de ligne. Ça change
la nature de la détection de perte : **elle passe d'un compteur à une propriété de
la donnée elle-même.**

Le compteur `overwritten` de `/collect/status` vit en mémoire vive côté appareil et
**repart de zéro à chaque redémarrage**. Le collecteur, comparant à la valeur
mémorisée et en voyant une plus basse, en déduisait qu'il ne s'était rien passé —
un redémarrage masquait donc toutes les pertes suivantes jusqu'à repasser l'ancien
maximum. La colonne referme ce trou : elle survit au redémarrage de l'appareil, à
la perte de l'état du collecteur, et voyage dans la sauvegarde mensuelle.

⚠️ **Sans cette mise à jour, rien ne casse — et c'est le problème.** Le collecteur
verrait une colonne de plus, la traiterait comme une extension de schéma légitime,
ouvrirait un nouveau fichier et ignorerait le contenu. Une perte silencieuse de
plus, dans un dispositif construit pour n'en tolérer aucune.

---

## Périmètre

### 1. Le schéma

`overwrote` rejoint `BASE_COLUMNS` en dernière position, et `REQUIRED_COLUMNS` :
elle est **toujours présente et jamais vide**, comme `partial`. Valeurs `0` ou `1`.

⚠️ Ce n'est **pas** une extension de schéma. C'est une correction du schéma de
base, faite avant qu'aucune archive réelle n'existe. Après la première collecte en
service, la même modification aurait imposé une génération de fichier.

### 2. Le simulateur

Tous les scénarios émettent la colonne. Ajouter un scénario dédié où une ligne au
moins porte `overwrote=1`, et l'articuler avec le scénario `overwritten` existant :
le compteur augmente **et** les lignes concernées sont marquées.

Ajouter un scénario **`device_restart`** : le compteur `overwritten` de
`/collect/status` **décroît** — l'appareil a redémarré — alors que des lignes
portent `overwrote=1`. C'est le cas que l'ancienne logique traitait par le silence.

### 3. Les événements

Une ligne collectée portant `overwrote=1` produit un `overwrite_detected` dans
`events.csv`, au même titre qu'une augmentation du compteur. **Ne pas journaliser
deux fois le même épisode** quand les deux sources concordent — la logique
d'épisode existe déjà.

### 4. Le rapport mensuel

Le bilan des pertes se calcule **depuis la colonne**, en comptant les lignes
marquées sur la fenêtre du mois. Le compteur `overwritten` reste affiché en
complément, étiqueté comme tel.

### 5. La décroissance du compteur n'est plus un silence

`check_overwritten` renvoie `None` quand la valeur courante est inférieure à la
précédente. Ce cas signifie **« l'appareil a redémarré »** et doit produire un
événement, pas un silence. La ligne de base est alors réinitialisée sur la valeur
courante.

Ce n'est plus une perte de données — la colonne s'en charge — mais c'en est une
information, et le principe du dépôt est qu'une information ne se perd pas parce
qu'elle est gênante à représenter.

---

## Exclusions

**Ne pas modifier les règles d'écriture de l'archive** : ajout seul, idempotence,
extension par nouveau fichier. Seule la liste des colonnes change.

**Ne pas modifier `COLLECTE.md`.** Toute imprécision se signale.

**Ne pas toucher au déplacement de `crc8`** mentionné dans le contrat : il concerne
la disposition binaire côté appareil, que le collecteur ne voit jamais.

---

## Critères d'acceptation

| # | Critère | Vérification |
|---|---|---|
| 1 | Une ligne `overwrote=1` produit un `overwrite_detected` durable | Scénario dédié, lecture d'`events.csv` |
| 2 | Un en-tête sans `overwrote` est rejeté comme violation de schéma | Scénario `schema_violation` étendu |
| 3 | Un compteur qui décroît produit un événement, pas un silence | Scénario `device_restart` |
| 4 | Le bilan mensuel des pertes est calculé depuis la colonne | Simulateur éteint, rapport généré |
| 5 | Une même perte vue par les deux sources n'est journalisée qu'une fois | Compteur et colonne concordants |
| 6 | La suite complète reste verte | `python3 -m unittest discover -s tests` |

Le critère 3 est celui qui n'existait pas et qui manquait. Le critère 5 est celui
qu'on casse facilement en corrigeant le 1.
