# COLLECTE.md — Contrat de collecte des mesures

> **Ce document est un contrat, pas une description.** Il est implémenté des deux
> côtés — par le firmware qui expose, par le collecteur qui interroge — et toute
> source future, à commencer par la station météo, doit le respecter pour être
> collectée par le même outil.
>
> Il succède à `docs/BROKER.md` du dépôt `smart-led-clock`, où le transport MQTT
> est retiré par ADR-0005.
>
> **Aucun identifiant, aucune adresse locale ne figure ici.**

---

## 1. Principes

Cinq règles dont tout le reste découle. Elles ne sont pas négociables au cas par
cas : c'est leur constance qui fait la valeur de l'archive.

**L'appareil détient, le collecteur vient chercher.** Une mesure non collectée
n'est pas perdue, elle attend. L'anneau couvre 170 jours.

**Lire est libre, modifier est authentifié.** L'asymétrie n'est pas entre les
personnes mais entre les opérations. Toute opération qui change un état —
configuration, effacement de l'anneau, remise à zéro d'un compteur — exige une
authentification. La lecture n'en exige aucune.

**L'absence n'est jamais une valeur.** Une grandeur non mesurée sort du contrat
comme un champ vide, jamais comme un zéro, jamais comme la dernière valeur connue.
Chaque grandeur est accompagnée de son nombre d'échantillons.

**L'archive est en UTC.** Sans exception et sans suffixe ambigu. L'affichage local
est une affaire d'interface, pas d'archive. Cette règle met l'archive hors
d'atteinte du défaut de changement d'heure de l'horloge (`BUG-08` dans son dépôt)
sans attendre qu'il soit corrigé : sinon chaque automne produirait une heure en double et chaque
printemps une heure manquante, indiscernables d'un trou réel.

**Un seul format de bout en bout.** Le CSV produit par l'appareil est celui
qu'ajoute le collecteur, celui que transporte le rapport mensuel, celui qu'un
tableur ouvrira dans trente ans. Aucune conversion, aucun schéma intermédiaire.

---

## 2. L'enregistrement, côté appareil

Anneau de **4 096 enregistrements de 32 octets** en EEPROM I²C — 170 jours
d'agrégats horaires.

| Champ | Type | Octets | Unité |
|---|---|---:|---|
| `ts_utc` | `uint32` | 4 | Début de l'heure, epoch Unix UTC |
| `t_in_mean` | `int16` | 2 | °C ×10 |
| `rh_in_mean` | `uint16` | 2 | % ×10 |
| `t_out_mean` | `int16` | 2 | °C ×10 |
| `t_out_min` | `int16` | 2 | °C ×10 |
| `t_out_max` | `int16` | 2 | °C ×10 |
| `rh_out_mean` | `uint16` | 2 | % ×10 |
| `co2_mean` | `uint16` | 2 | ppm |
| `pm1_mean` | `uint16` | 2 | µg/m³ ×10 |
| `pm25_mean` | `uint16` | 2 | µg/m³ ×10 |
| `pm10_mean` | `uint16` | 2 | µg/m³ ×10 |
| `n_in` | `uint8` | 1 | échantillons DHT22 intérieur |
| `n_out` | `uint8` | 1 | échantillons DHT22 extérieur |
| `n_co2` | `uint8` | 1 | échantillons SCD41 |
| `n_pm` | `uint8` | 1 | échantillons SPS30 |
| `flags` | `uint8` | 1 | voir ci-dessous |
| **Réservé** | — | **2** | pression atmosphérique de la station météo |
| `crc8` | `uint8` | 1 | **dernier octet** — couvre les octets 0 à 30 |

⚠️ **`crc8` occupe le dernier octet, et couvre tout ce qui précède** — y compris
les deux octets réservés. Une somme de contrôle qui ne couvre pas la totalité de
l'enregistrement laisse une zone où la corruption ne se voit pas.

⚠️ **La graine de calcul doit être non nulle et vérifiée contre le motif d'une
EEPROM vierge.** Une puce effacée présente un motif constant : soit tous ses
emplacements valident la somme de contrôle, soit aucun. Ce n'est pas une
probabilité, c'est un déterminisme — à éprouver par un test, pas à supposer.

**Valeurs sentinelles.** Un champ dont le compteur vaut zéro porte `INT16_MIN`
pour les signés, `UINT16_MAX` pour les non signés. Le compteur fait foi : la
sentinelle n'est qu'une protection contre une lecture qui l'ignorerait.

**`flags`**, bits significatifs :

| Bit | Signification |
|---:|---|
| 0 | Heure partielle — l'agrégation a été interrompue par un redémarrage |
| 1 | Cet enregistrement a écrasé un enregistrement non collecté. **Exposé en CSV sous `overwrote`** |

⚠️ **32 divise exactement la page d'écriture de 128 octets et le bloc de
65 536.** Aucun enregistrement ne chevauche jamais une frontière. Cette propriété
est la raison de la taille : ne pas la rompre.

---

## 3. Le format CSV

**Conventions, strictes et sans exception :**

| | |
|---|---|
| Séparateur de champ | virgule `,` |
| Séparateur décimal | point `.` |
| Fin de ligne | `LF` (`\n`) |
| Encodage | ASCII |
| Horodatage | ISO 8601 UTC — `2026-08-12T14:00:00Z` |
| Ordre | horodatage croissant, une ligne par heure |
| Première ligne | en-tête, toujours présente |

Le point décimal et la virgule séparatrice sont imposés contre l'habitude
française du point-virgule : un fichier destiné à durer ne se lit pas selon les
réglages régionaux de la machine qui l'ouvre.

**Colonnes, dans l'ordre :**

```
ts_utc,t_in,rh_in,t_out,t_out_min,t_out_max,rh_out,co2,pm1,pm25,pm10,n_in,n_out,n_co2,n_pm,partial,overwrote
```

Les valeurs sont exprimées dans leur unité naturelle, non multipliées : `21.5`
pour 21,5 °C, `-3.2`, `847` pour 847 ppm, `12.4` µg/m³.

**Une grandeur non mesurée sort en champ vide**, son compteur à `0` :

```
ts_utc,t_in,rh_in,t_out,t_out_min,t_out_max,rh_out,co2,pm1,pm25,pm10,n_in,n_out,n_co2,n_pm,partial,overwrote
2026-08-12T14:00:00Z,21.5,48.2,18.1,17.4,19.0,62.1,847,3.1,5.2,7.8,30,30,12,6,0,0
2026-08-12T15:00:00Z,21.7,47.9,,,,,912,3.4,5.6,8.1,30,0,12,6,0,0
```

Sur la seconde ligne, le capteur extérieur n'a rien produit : quatre champs vides,
`n_out` à zéro, et **les mesures intérieures sont là**. C'est le découplage des capteurs
vu depuis l'archive.

**Une heure absente est une ligne absente.** On ne fabrique pas de ligne vide pour
combler un trou : c'est au lecteur de constater la discontinuité des horodatages.

### Règle d'extension

**Les colonnes ne sont qu'ajoutées, en fin de ligne, jamais insérées ni
réordonnées ni renommées.** Un fichier d'archive couvre des années et contient
donc plusieurs générations de schéma ; seule cette règle garantit qu'une ligne de
2026 reste lisible avec le lecteur de 2032. L'en-tête d'un fichier reflète la
génération la plus récente qu'il contient.

---

## 4. Le contrat HTTP

**Tout ce que ce contrat définit vit sous le préfixe `/collect/`.** Le préfixe
n'est pas décoratif : il sépare ce qui s'adresse à une machine de ce qu'un
appareil peut exposer par ailleurs pour un usage humain. Une horloge servant déjà
`/api/status` à sa propre page web ne risque alors aucune collision, et personne
n'a à deviner laquelle des deux sémantiques s'applique.

### `GET /collect/history`

| Paramètre | Sémantique |
|---|---|
| `since` | Horodatage **au format ISO 8601 UTC**, identique à la colonne `ts_utc` — `2026-08-12T14:00:00Z`, encodé pour l'URL. Renvoie les heures **strictement postérieures**. Absent ⇒ tout ce qui est détenu |
| `limit` | Nombre maximal de lignes. Défaut et plafond fixés par le firmware |

Réponse `200`, `Content-Type: text/csv`, **émise par tranches** — jamais composée
en RAM. La ligne d'en-tête est toujours présente, y compris quand
aucune ligne de données ne suit.

⚠️ **Le format de `since` est celui de la colonne, pas un epoch Unix.** Le
collecteur renvoie littéralement la dernière valeur de `ts_utc` qu'il détient : il
n'a rien à convertir, et l'appareil non plus au-delà de l'analyse. Un appareil qui
lirait ce paramètre avec un `atoi` obtiendrait l'année — une valeur plausible,
silencieusement fausse. **Ce piège s'est produit le 2026-08-13**, et il n'a été
trouvé qu'en faisant dialoguer les deux implémentations réelles.

⚠️ **Le paramètre arrive encodé pour l'URL** : les deux-points de l'heure y
figurent en `%3A`. Le décodage est à la charge de l'appareil.

**La pagination ne demande aucun protocole.** Le collecteur rappelle avec
`since` = dernier horodatage reçu, jusqu'à obtenir zéro ligne. L'état vit chez le
collecteur, l'appareil reste sans mémoire de session.

### `GET /collect/status`

Réponse en CSV à deux colonnes `cle,valeur` — un seul format, un seul analyseur.

| Clé | Contenu |
|---|---|
| `fw_version` | Version du firmware |
| `time_trusted` | `1` si l'heure est jugée fiable, `0` sinon |
| `hours_held` | Nombre d'heures détenues dans l'anneau |
| `oldest_ts`, `newest_ts` | Bornes de l'anneau, UTC |
| `overwritten` | Compteur cumulé d'enregistrements écrasés avant collecte |
| `sensors` | Inventaire et état, une clé par capteur |
| `uptime_s` | Secondes depuis le démarrage |

`overwritten` est **cumulatif et peut repartir de zéro** si l'appareil redémarre
sans le persister. Ce n'est donc pas la source de vérité d'une perte : la colonne
`overwrote` de chaque ligne l'est, parce qu'elle est portée par la donnée
elle-même et survit à tout. Le compteur reste utile en complément, et une
décroissance signale un redémarrage de l'appareil, jamais l'absence de perte.

### Opérations modifiant un état

La configuration de l'appareil, l'effacement de l'anneau, la remise à zéro des
compteurs : **authentifiées**. Ces routes restent sous le préfixe propre à
l'appareil et ne relèvent pas de ce contrat, qui ne couvre que la lecture.

⚠️ **Le firmware de l'horloge ne gère pas TLS.** Un identifiant circulerait donc en clair sur
le réseau local. Ce n'est acceptable que sur un réseau domestique de confiance,
et impose un secret **qui ne soit réutilisé nulle part ailleurs**. C'est une
limite assumée côté appareil, pas un oubli.

### Protection contre le martèlement

Pas d'authentification en lecture : la disponibilité se protège autrement.

- Temporisation et bornage sur la lecture des en-têtes HTTP côté appareil
- Longueur maximale de ligne de requête et d'en-tête
- Plafond de lignes par réponse
- Intervalle minimal entre deux vidages complets de l'anneau

---

## 5. Règles d'agrégation

**L'heure se clôt sur la frontière horaire UTC** lue au RTC.

**Un échantillon n'est compté que si la lecture du capteur est valide.** Un
capteur muet fait décroître son compteur, jamais la valeur d'un autre.

**Si l'heure n'est pas fiable, rien n'est écrit.** Après une coupure sans
resynchronisation, l'horodatage serait faux. Un trou vaut mieux qu'une heure
datée n'importe comment. **C'est le seul cas où l'appareil n'écrit pas.**

**Une heure sans aucun échantillon est écrite quand même**, tous compteurs à zéro
et toutes valeurs à leur sentinelle. L'appareil tournait, son heure était fiable,
et aucun capteur n'a répondu : c'est une information, et elle se distingue d'un
appareil éteint — qui, lui, ne produit aucune ligne.

⚠️ Un trou dans la série signifie donc **« l'appareil n'a pas écrit »** — éteint,
ou heure non fiable. Une ligne à compteurs nuls signifie **« l'appareil a écrit
qu'il n'avait rien mesuré »**. Ne pas confondre les deux, et ne jamais produire la
première là où la seconde est possible.

**Une heure partielle est écrite avec son compteur réel** et le bit `partial`.
Sept relevés au lieu de trente est une information honnête dès lors que le sept
est visible.

**L'écrasement se marque dans la donnée.** Quand l'écriture d'une heure recouvre
un enregistrement non encore collecté, le bit 1 de `flags` est posé **sur
l'enregistrement écrivain**, et sort en CSV dans `overwrote`. Le compteur
`overwritten` est incrémenté en parallèle.

⚠️ **L'appareil ne sait ce qui a été collecté que par le paramètre `since` des
requêtes reçues.** Il retient le plus grand `since` vu depuis son démarrage : le
collecteur ne demande `since=X` que s'il détient déjà tout jusqu'à X. Ce repère
vit en mémoire vive et repart de zéro à chaque démarrage — donc l'appareil
**sur-compte** tant qu'aucune collecte n'a eu lieu depuis son redémarrage. C'est
délibéré : sous-compter une perte est plus grave que la sur-compter.

---

## 6. Ce qu'on attend du collecteur

- Interroger à cadence fixe, et **considérer l'absence de réponse comme un
  événement**, pas comme une absence de nouvelles
- **Ajouter** à l'archive, ne jamais réécrire une ligne passée
- Détecter les discontinuités d'horodatage et les rapporter dans le rapport
- Alerter au-delà d'un seuil d'injoignabilité
- Reporter les lignes portant `overwrote` à `1` : c'est la trace durable d'une
  perte. Le compteur `overwritten` vient en complément, jamais seul

---

## 7. Ce que le contrat n'impose pas

Ni le support de stockage de l'appareil, ni le langage du collecteur, ni la nature
de l'appareil lui-même. **Une station météo autonome qui expose ce contrat se
collecte avec le même outil, sans qu'une ligne du collecteur change.**

C'est le contrat qui est conçu, pas la liaison.

---

## Voir aussi

- [`README.md`](README.md) — ce que fait le collecteur, et pourquoi il vit ici
- Dépôt `smart-led-clock` — première source implémentant ce contrat :
  `design/INTENTION.md` pour la finalité, `design/adr/0005-*` pour la décision,
  `docs/HARDWARE.md` pour le brochage, `docs/BROKER.md` pour l'architecture
  précédente conservée pour mémoire
