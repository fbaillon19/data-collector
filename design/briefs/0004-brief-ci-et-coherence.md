# Brief 0004 — Intégration continue et barrière de cohérence

> **Exécutant** : Claude Code
> **Décidé par** : `ADR-0009` du dépôt `smart-led-clock` — la décision y vit, ce
> brief l'applique ici
> **Date** : 2026-08-15
>
> Brief autoportant. **Les exclusions sont aussi contraignantes que le périmètre.**
>
> Ce brief existe parce que le brief 0010 du dépôt horloge portait la mention
> ⚠️ *non vérifié — la CI de `data-collector` n'a pas été inspectée*, et que
> Claude Code a refusé de deviner plutôt que de la satisfaire. **C'était la bonne
> décision** : ce dépôt n'a aucune CI, donc il ne s'agissait pas d'ajouter une
> étape à un enchaînement existant.

---

## Ce qui a été relevé, le 2026-08-15

| Constat | Valeur |
|---|---|
| CI existante | **aucune** — pas de `.github/` |
| Tests | **79**, `python3 -m unittest discover -s tests -t .`, verts sur **Python 3.10.12 (Linux)** et **3.9 (macOS)** |
| Dépendances tierces | **aucune** — bibliothèque standard uniquement |
| Documents Markdown | 7 |
| Blocs d'arborescence | **1**, dans `README.md` |
| Valeurs exposées | aucune. Seules occurrences : `127.0.0.1` dans les tests, boucle locale légitime |
| Index d'anomalies | **aucun** — ce dépôt n'a ni `BUG-NN` ni `US-NN` |
| Point de reprise | **aucun** — pas de `REPRISE.md` |
| Liens hors dépôt | 3, corrigés le 2026-08-15 avant ce brief (voir plus bas) |

---

## Périmètre

### Partie A — La CI, qui n'existe pas

Créer `.github/workflows/`. Deux étapes, dans cet ordre.

**1. Cohérence documentaire**, en premier. Elle ne dépend de rien : un lien mort
doit échouer en quelques secondes, pas après la suite de tests.

**2. Les 79 tests**, sur **une matrice de versions de Python**.

⚠️ **La matrice n'est pas du zèle.** Le collecteur tournera sur le Pi, pas sur la
machine de développement. Raspberry Pi OS *bookworm* fournit Python 3.11,
*bullseye* 3.9.

**Correction du 2026-08-15 — deux machines, pas une.** Ce brief annonçait « verts
sur 3.10.12 » et en déduisait que 3.9 était inconnu. Les deux mesures existaient :
**3.10.12 dans la VM Linux** où tourne l'outillage de Cowork, **3.9 sur le macOS**
où tourne Claude Code. Aucune n'était fausse ; ce qui manquait à la première,
c'est le nom de la machine.

C'est la règle du gabarit — *avec quel instrument la valeur a-t-elle été obtenue ?*
— dont il manquait la moitié : **une version d'interpréteur sans sa machine est
une valeur sans instrument.**

La question centrale du brief est donc déjà tranchée, et dans le bon sens : la
suite passe sur **3.9 et 3.10, sur deux systèmes**. La matrice reste utile pour
3.11 et 3.12, qu'aucune machine locale ne fournit.

Si une version échoue, c'est un défaut à consigner, pas une version à retirer de
la matrice — le critère 1 du brief 0003 exige que les tests passent *sur le Pi*.

Aucune installation de dépendances : il n'y en a pas.

### Partie B — La barrière, réduite à ce qui s'applique

Copier `tools/coherence.py` **verbatim** depuis le dépôt `smart-led-clock`, et le
configurer pour ce dépôt.

| Contrôle | Ici | Raison |
|---|---|---|
| Arborescences étiquetées `tree` | ✅ actif | Un bloc dans `README.md`, **à étiqueter** — il ne l'est pas |
| Liens et ancres | ✅ actif | 13 liens relatifs |
| Bloc d'état périmé | ✅ actif | Voir partie C |
| Statuts divergents | ❌ inactif | Ni `BUG-NN` ni `US-NN` dans ce dépôt |
| Valeurs exposées | ✅ actif | Avec `127.0.0.1` en exception déclarée |
| Point de reprise non relu | ❌ inactif | Pas de `REPRISE.md` |

⚠️ **Copier, ne pas réimplémenter.** Deux implémentations des mêmes primitives
divergeraient en silence, ce qui est la classe de défaut que tout ce dispositif
combat.

⚠️ **Et la copie doit se voir.** Le fichier porte en tête : *« Copie de
`smart-led-clock/tools/coherence.py`. La version de référence vit là-bas ; toute
modification s'y fait d'abord, puis se reporte ici. »*

**Il n'existe aucun mécanisme automatique de synchronisation** entre deux dépôts
privés sans registre commun. La synchronisation est donc **un geste humain, porté
par la liste de clôture** du gabarit de brief du dépôt horloge — au même titre que
la mise à jour d'un statut d'ADR. C'est explicite, visible, et faillible ; les
trois autres options le sont davantage.

### Partie C — Le bloc d'état

Copier et adapter `tools/etat.py`. Ce dépôt a peu à relever, mais **c'est
exactement ce peu qui a été faux** :

| Ligne | Source de lecture |
|---|---|
| Tests | `def test_` dans `tests/*.py` |
| Briefs | l'index de `README.md` |
| Modules du collecteur | `collector/*.py` |
| Scénarios du simulateur | la liste de `--scenario` |

⚠️ **Pas de `Relevé le`.** La leçon est acquise dans le dépôt horloge : une date
inscrite dans un fichier généré le rend périmé à chaque commit, quelle que soit sa
granularité. La fraîcheur est garantie par le contrôle lui-même, et
`git log -1 design/ETAT.md` la donne exactement.

**Pourquoi ce dépôt en a besoin autant que l'autre** : son `README.md` a annoncé
« 73 tests », le dépôt horloge « 78 », et il y en avait **79**. Trois valeurs, zéro
juste. C'est la famille de défauts la plus nombreuse de l'audit du 2026-08-14, et
elle a franchi la frontière entre les deux dépôts.

---

## Exclusions

- **Ne pas ajouter de dépendance**, ni de test, ni d'outil de couverture ou de
  style. Ce dépôt tient sans, et c'est une propriété défendue par `CLAUDE.md` §2.
- **Ne pas corriger ce que la barrière trouve.** Ce lot pose l'instrument ; ce
  qu'il révèle se traite ensuite, et relève de Cowork pour la documentation.
- **Ne pas toucher au contrat `COLLECTE.md`.** Il engage des implémentations qui
  vivent ailleurs — `CLAUDE.md` §5.
- **Ne pas réécrire l'historique** pour les trois liens hors dépôt : ils ont été
  corrigés en tête, l'historique n'a pas à l'être.

---

## Critères d'acceptation

| # | Critère | Vérification |
|---|---|---|
| 1 | La CI est verte, et **rouge si un lien est cassé** | Casser un lien dans une branche jetable |
| 2 | Les 79 tests passent sur **chacune** des versions de la matrice | Sortie de la CI, version par version |
| 3 | Une version de la matrice qui échoue est **consignée, pas retirée** | Le cas échéant |
| 4 | Le contrôle d'arborescence signale une entrée fausse dans le bloc `tree` du `README`, et ignore un bloc non étiqueté portant la même | Cas construit, comme pour le dépôt horloge |
| 5 | `ETAT.md` régénéré deux fois de suite est identique à l'octet près | Double exécution |
| 6 | `ETAT.md` ne change pas entre deux commits sans changement de valeur mesurée | Deux commits |
| 7 | Le fichier copié porte l'en-tête nommant sa version de référence | Relecture |
| 8 | Sur `HEAD`, **aucun contrôle bloquant** | Exécution |

⚠️ **Le critère 8 est celui qui a échoué dans le dépôt horloge**, et il est plus
difficile que le critère 1. Ne rien signaler quand il n'y a rien vaut mieux que
signaler beaucoup.

⚠️ **Le critère 3 protège contre la tentation évidente.** Si 3.9 échoue, retirer
3.9 de la matrice rend la CI verte et laisse le collecteur incapable de tourner
sur un Pi *bullseye*. C'est un critère qui existe pour interdire une correction,
pas pour en exiger une.

---

## Point d'arrêt

**Après la partie A**, rendre le résultat de la matrice.

Si 3.9 ou 3.12 échoue, c'est un fait nouveau qui touche le brief 0003 — la mise en
service sur le Pi — et qui doit être connu avant qu'on installe quoi que ce soit.

---

## Ce que ce lot ne fait pas

- Il ne teste pas le collecteur contre un appareil réel. Aucun ne sert encore le
  contrat.
- Il ne vérifie pas le contrat lui-même. `COLLECTE.md` est un document ; que les
  deux implémentations le respectent **ne se prouve qu'en les faisant dialoguer**,
  et cela n'arrivera qu'à la repose.
