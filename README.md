# data-collector

> Collecte, archive et rapporte les mesures d'appareils domestiques qui exposent
> le contrat décrit dans [`COLLECTE.md`](COLLECTE.md).

**État : implémentation complète, jamais confrontée à un appareil réel.**
Créé le 2026-08-12, collecteur livré le 2026-08-13 — 73 tests, aucune dépendance
tierce.

Aucun appareil ne sert encore le contrat : l'horloge sera modifiée lors d'une
dépose à venir. Le collecteur a été écrit et éprouvé contre un simulateur, et
deviendra le banc de test du firmware le jour de la repose.

**Reste avant mise en service** : configuration réelle hors dépôt, installation
des unités `systemd` sur le Pi, première collecte contre l'horloge.

---

## Ce que c'est

Une tâche planifiée qui interroge des appareils, complète une archive CSV et
envoie des rapports. Rien de plus.

Elle ne détient pas la donnée : **les appareils la détiennent**, et le collecteur
vient la chercher. Une collecte manquée n'est pas une perte, c'est un retard. Ce
choix est la conséquence directe d'une panne où quatre mois de mesures ont été
perdues, puis fabriquées, sans que personne le remarque.

| | |
|---|---|
| **Interroge** | `GET /api/history?since=…` jusqu'à obtenir zéro ligne |
| **Ajoute** | à un CSV d'archive, sans jamais réécrire une ligne passée |
| **Rapporte** | résumé hebdomadaire bref, synthèse mensuelle avec l'archive jointe |
| **Alerte** | appareil injoignable, capteur muet, écrasement d'anneau |

**Le courriel mensuel est la sauvegarde.** Il emporte l'archive complète, pas
seulement le mois écoulé : chaque message est un point de restauration autonome.
Rien à vérifier périodiquement, puisqu'il n'existe pas d'exemplaire partiel.

---

## Pourquoi un dépôt séparé

Le premier appareil collecté est une horloge murale — dépôt `smart-led-clock`.
Une station météo autonome suivra, avec son matériel et son firmware propres,
donc son dépôt.

Le collecteur n'appartient donc **pas plus à l'un qu'à l'autre**. L'héberger chez
le premier appareil serait un accident d'antériorité : il faudrait l'en sortir dès
que le second existe.

Le contrat vit ici pour la même raison. **C'est le collecteur qui le fait
respecter** : un appareil est collectable s'il sert ce que le collecteur sait
lire.

---

## Principes

**Zéro dépendance tierce.** Bibliothèque standard Python uniquement — `urllib`,
`csv`, `smtplib`, `email`. Aucun `pip install`, aucun environnement virtuel à
reconstituer. Un script sans installation se réinstalle dans dix ans, y compris
quand on aura oublié comment il marche.

**L'absence n'est jamais une valeur.** Un champ vide reste vide, un compteur
d'échantillons nul signifie « non mesuré ». Le collecteur ne comble pas, ne
lisse pas, n'interpole pas.

**L'archive survit au logiciel qui la produit.** CSV, ASCII, UTC, séparateur
virgule, point décimal. Lisible par n'importe quoi, dans trente ans, sans ce
dépôt.

**Le rapport est sa propre surveillance.** S'il n'arrive pas, l'information est
déjà passée. Pas de tableau de bord à consulter — c'est précisément le dispositif
qui a échoué.

**Aucun identifiant dans le dépôt.** Ni SMTP, ni sujet ntfy, ni adresse locale.
Configuration dans un fichier hors dépôt, y compris en commentaire et à titre
d'exemple.

---

## Structure prévue

```
data-collector/
├── README.md          ce fichier
├── CLAUDE.md          contexte et conventions
├── COLLECTE.md        ← le contrat, autorité de référence
├── design/briefs/     spécifications de passation vers Claude Code
├── collector/         implémentation
├── simulator/         serveur servant le contrat, pour éprouver hors matériel
└── tests/
```

**Briefs**

| N° | Titre | Statut |
|---|---|---|
| [0001](design/briefs/0001-brief-collecteur.md) | Le collecteur et son simulateur | ✅ Exécuté — 2026-08-13, bilan en fin de document |
| [0002](design/briefs/0002-brief-colonne-overwrote.md) | La colonne `overwrote` | 📋 Rédigé — 2026-08-13 |

### Un enseignement du premier lot

Les trois défauts trouvés à la relecture n'étaient pas des erreurs de calcul :
c'étaient des **détections incapables de se déclencher**, ou qui repartaient à
chaque passage pour un même épisode. Toutes passaient leurs tests.

La cause était commune : les tests alimentaient les fonctions avec des lots que la
production ne produira jamais — vingt-quatre lignes d'un coup, là où un `poll`
horaire en rapporte une. **Écrire les tests d'intégration au rythme réel du `poll`
est ce qui les a fait tomber.**

Le **simulateur** n'est pas un accessoire. Il sait produire les cas désagréables —
trous d'horodatage, champs vides, anneau écrasé, heure non fiable, appareil
injoignable — et permet de vérifier que le collecteur traite correctement
l'absence **avant** qu'un firmware existe, sans jamais provoquer ces cas sur un
appareil en service.

---

## Appareils collectés

| Appareil | Dépôt | État |
|---|---|---|
| Horloge murale à LED | `smart-led-clock` | Contrat à implémenter — lot de dépose |
| Station météo | à créer | Envisagée |
