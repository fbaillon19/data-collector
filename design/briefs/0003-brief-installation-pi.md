# Brief 0003 — Mise en service du collecteur sur le Raspberry Pi

> **Exécutant** : **l'auteur**, sur le Pi. Claude Code n'y a pas accès, et rien de
> ce lot ne se fait dans le dépôt.
> **Décidé par** : [ADR-0005](../../../smart-led-clock/design/adr/0005-bascule-du-transport-en-pull.md)
> `D4`, et [ADR-0004](../../../smart-led-clock/design/adr/0004-fiabilite-de-la-chaine-de-donnees.md)
> critère 5
> **Procédure** : [`docs/INSTALLATION-PI.md`](../../docs/INSTALLATION-PI.md) —
> elle décrit **comment**, ce brief dit **ce qui compte comme réussi**
> **Date** : 2026-08-15
>
> Brief autoportant. **Les exclusions sont aussi contraignantes que le périmètre.**

---

## Pourquoi ce lot

Le collecteur est écrit, testé et **n'a jamais tourné ailleurs que sur une machine
de développement**. `D4` d'ADR-0005 attend son installation ; le critère 5
d'ADR-0004 — vérifier une sauvegarde en la restaurant — attend qu'il existe une
sauvegarde à restaurer.

Ce lot ne dépend d'aucun matériel nouveau et **n'attend ni le SPS30 ni la dépose**.

⚠️ **L'horloge ne sert pas encore le contrat.** Son firmware `/collect/` est écrit
et testé, jamais flashé. Ce lot valide donc **tout sauf elle** : minuteries,
archive, rapports, sauvegarde et restauration. Le jour de la repose, l'horloge
sera la seule inconnue au lieu d'être une inconnue parmi six.

C'est la logique du lot 0007 côté firmware — faire d'abord tout ce qui ne demande
pas le matériel — appliquée à l'autre bout de la chaîne.

---

## Ce qui a été vérifié avant d'écrire ce brief

Exécuté le 2026-08-14 sur la machine de développement, Python 3.10.12, sans réseau :

| Vérification | Résultat |
|---|---|
| Suite complète | **79 tests, tous verts** |
| Surface de la ligne de commande | `python3 -m collector [--config CHEMIN] {poll,report,export}` ; `report` exige `--weekly` ou `--monthly` |
| Chaîne simulateur → collecteur → archive | CSV de 17 colonnes conforme au contrat, plus `clock.state.json` |
| Second passage sans nouveauté | Idempotent |
| Scénario `dead_sensor` | **Champ vide, compteur à zéro, jamais de valeur reportée.** Les grandeurs intérieures continuent d'être archivées, un événement `sensor_mute_start` est journalisé |
| Scénario `overwritten` | Événement `overwrite_detected` journalisé |

---

## Périmètre

Quatre parties, dans cet ordre.

### A — Le système

Raspberry Pi OS Lite sur la carte de 58 Go. **Ni Home Assistant, ni Mosquitto.**

⚠️ **Le nom d'utilisateur est un piège.** Les cinq unités `systemd` livrées
déclarent `User=pi` ; Raspberry Pi OS ne crée plus cet utilisateur par défaut
depuis 2022. Trancher avant d'activer quoi que ce soit.

⚠️ **L'heure du Pi doit être synchronisée** avant la première collecte : le
collecteur raisonne sur des fenêtres mensuelles et sur un paramètre `since`.

### B — Le collecteur et sa configuration

Copie dans `/opt/data-collector`, configuration hors dépôt, secret SMTP dans un
`EnvironmentFile`. Aucune dépendance à installer.

⚠️ **Une divergence à trancher, relevée à la vérification.** Les unités ne passent
pas `--config` ; le collecteur retombe alors sur `~/.config/...`, c'est-à-dire le
*home* du service, alors que le secret SMTP est dans `/etc`. **Deux emplacements
pour une même configuration est la classe de défaut que le dépôt horloge vient de
supprimer côté firmware.** Choisir, et un seul.

### C — Les minuteries

Passage à la main de chaque unité **avant** d'activer la moindre minuterie.

### D — La validation, contre le simulateur

Six scénarios, un par un : `dead_sensor`, `gap`, `overwritten`, `unreachable`,
`time_untrusted`, `device_restart`.

---

## Exclusions

- **Ne pas pointer le collecteur sur l'horloge.** Elle sert encore l'ancien
  firmware ; l'interroger ne produirait rien d'exploitable et brouillerait
  l'archive naissante avec des erreurs qui ne prouvent rien.
- **Ne pas reconstruire Home Assistant ni un broker.** `docs/BROKER.md` du dépôt
  horloge est une archive, pas une procédure.
- **Ne pas modifier le collecteur.** Si un défaut apparaît, le consigner et
  s'arrêter : un correctif écrit sur le Pi ne sera ni testé, ni versionné, ni
  retrouvé.
- **Ne rien mettre d'identifiant dans le dépôt.** Configuration et secret vivent
  hors de lui, y compris en commentaire.

---

## Critères d'acceptation

| # | Critère | Vérification |
|---|---|---|
| 1 | Les 79 tests passent **sur le Pi** | `python3 -m unittest discover -s tests -t .` |
| 2 | Les trois minuteries sont actives et leur prochaine échéance est cohérente | `systemctl list-timers 'data-collector-*'` |
| 3 | Un `poll` contre le simulateur produit une archive, et un second n'ajoute rien | Deux exécutions, `diff` |
| 4 | **Un capteur muet produit un champ vide et un compteur à zéro** | Scénario `dead_sensor`, relecture du CSV |
| 5 | Un appareil injoignable produit un **événement**, pas un silence | Scénario `unreachable`, relecture de `events.csv` |
| 6 | Le rapport hebdomadaire arrive effectivement | Réception constatée, pas code de retour nul |
| 7 | Le rapport mensuel arrive **avec l'archive complète en pièce jointe** | Réception constatée |
| 8 | **La sauvegarde est restaurée sur une autre machine, et un collecteur neuf reprend dessus là où l'archive s'arrête** | Voir ci-dessous |
| 9 | Une coupure d'alimentation franche ne laisse ni archive tronquée ni état incohérent | Coupure réelle, puis `poll` |

⚠️ **Le critère 8 est le seul qui ferme `ADR-0004`.** Les critères 6 et 7
vérifient qu'un message part ; le 8 vérifie qu'il **sert à quelque chose**. La
dernière fois, `supervisor/backup/` était vide et personne ne le savait — c'est
précisément ce qui a rendu la récupération manuelle nécessaire.

**Tant que le critère 8 n'est pas passé, `ADR-0004` reste ouvert**, quel que soit
le nombre de courriels reçus.

⚠️ **Le critère 9 est celui qui a manqué la première fois.** Une coupure pendant
un orage est le scénario de menace documenté du projet, et c'est ce qui a tué le
Pi le 2026-06-28.

---

## Point d'arrêt

**Après la partie C, avant la partie D.** Rendre l'état des trois minuteries et la
sortie du premier `poll` manuel.

Si une unité ne démarre pas — le piège `User=pi` est le candidat le plus probable
— il vaut mieux le savoir avant d'avoir déroulé six scénarios.

---

## Ce que ce lot ne fait pas

- Il ne collecte aucune mesure réelle. La première viendra de l'horloge reflashée.
- Il ne valide pas le contrat de bout en bout : le simulateur sert ce que le
  collecteur sait lire, ce qui **ne prouve rien sur le firmware**. Cette
  vérification-là n'existera qu'à la repose, et c'est elle qui compte.
- Il ne referme pas `US-46` — le signalement du silence — dont l'énoncé attend le
  comportement réel du collecteur en service.
