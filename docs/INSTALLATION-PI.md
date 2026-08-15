# Installation du collecteur sur le Raspberry Pi

> Procédure de mise en service. Rédigée le 2026-08-14.
>
> **Ce qui a été exécuté avant d'être écrit ici est marqué ✅ vérifié.**
> **Ce qui ne pouvait pas l'être depuis la machine de développement est marqué
> ⚠️ non vérifié**, avec la vérification attendue. C'est la règle du projet, et
> elle a déjà évité deux briefs faux.

---

## Ce que cette installation peut, et ne peut pas, valider aujourd'hui

⚠️ **L'horloge ne sert pas encore le contrat.** Son firmware `/collect/` est
écrit, testé et committé — il n'a **jamais été flashé**. Le binaire au mur publie
encore en MQTT vers un broker qui n'existe plus. La première collecte réelle
n'aura donc lieu qu'après la dépose.

Ce n'est pas une raison d'attendre. **Tout le reste peut être validé maintenant**,
contre le simulateur : les minuteries, l'archive, les rapports, la sauvegarde et
sa restauration. Le jour de la repose, l'horloge sera alors la **seule** inconnue
— au lieu d'être une inconnue parmi six.

C'est exactement la logique du lot 0007 côté firmware : faire d'abord tout ce qui
ne demande pas le matériel.

---

## 0. Ce qui a été vérifié le 2026-08-14

Exécuté sur la machine de développement, Python 3.10.12, sans réseau :

| Vérification | Résultat |
|---|---|
| ✅ Suite de tests complète | **79 tests, tous verts**, en 18 s |
| ✅ Surface de la ligne de commande | `python3 -m collector [--config CHEMIN] {poll,report,export}` ; `report` exige `--weekly` ou `--monthly` |
| ✅ Chaîne complète simulateur → collecteur → archive | CSV produit, en-tête de 17 colonnes conforme au contrat |
| ✅ Second passage sans nouveauté | Idempotent, aucune ligne dupliquée |
| ✅ Capteur muet | **Champ vide et compteur à zéro, jamais une valeur reportée.** Les grandeurs intérieures continuent d'être archivées, et un événement `sensor_mute_start` est journalisé |
| ✅ Anneau écrasé | Événement `overwrite_detected` journalisé |

La cinquième ligne est la plus importante du tableau. **C'est la vérification que
le défaut fondateur du projet ne peut pas se reproduire côté collecteur** — quatre
mois de dernière valeur reçue archivée comme une mesure. Relevé brut :

```
2026-01-01T07:00:00Z  t_out=[18.1]  n_out=[30]
2026-01-01T08:00:00Z  t_out=[]      n_out=[0]     ← le capteur se tait
2026-01-01T09:00:00Z  t_out=[]      n_out=[0]
```

C'est la deuxième des trois barrières d'`US-45`. La première est dans le firmware
non flashé ; la troisième est la restitution, à instruire.

---

## 1. Le système

**Raspberry Pi OS Lite (64 bits), sur la carte de 58 Go.** Elle est saine :
`ddrescue` l'a lue intégralement sans une erreur, et `fsck_msdos` a passé ses
trois phases. Inutile d'en acheter une autre.

**Ni Home Assistant, ni Mosquitto.** Il n'y a plus de broker dans l'architecture,
et le tableau de bord que personne n'ouvrait est précisément l'hypothèse qui a
échoué.

⚠️ **non vérifié — le nom d'utilisateur.** Les unités `systemd` livrées dans
`docs/systemd/` déclarent `User=pi`. Depuis 2022, Raspberry Pi OS **ne crée plus
d'utilisateur `pi` par défaut** : le premier démarrage demande un nom. Si le vôtre
diffère, les cinq unités échoueront au démarrage avec un message peu explicite.

> **À faire** : soit créer l'utilisateur au premier démarrage sous le nom `pi`,
> soit remplacer `User=pi` dans les cinq unités. Vérifier avec `id <nom>` avant
> d'activer quoi que ce soit.

**L'heure du Pi doit être juste** avant la première collecte : le collecteur
raisonne sur des fenêtres mensuelles et sur un paramètre `since`. `timedatectl`
doit montrer la synchronisation active.

---

## 2. Le collecteur

```bash
sudo mkdir -p /opt/data-collector
sudo rsync -a --exclude='.git' --exclude='__pycache__' \
    <source>/data-collector/ /opt/data-collector/
sudo chown -R <utilisateur>:<utilisateur> /opt/data-collector
```

Aucune installation de dépendances : **bibliothèque standard Python uniquement**.
Pas de `pip`, pas d'environnement virtuel. C'est délibéré — un script sans
installation se réinstalle dans dix ans, y compris quand on aura oublié comment il
marche.

Vérifier tout de suite, sur le Pi :

```bash
cd /opt/data-collector
python3 -m unittest discover -s tests -t .    # doit afficher : Ran 79 tests ... OK
python3 -m collector --help
```

⚠️ **non vérifié — la version de Python du Pi.** Testé ici sur 3.10.12. Raspberry
Pi OS *bookworm* fournit 3.11, *bullseye* 3.9. La suite de tests ci-dessus est la
vérification : si elle passe sur le Pi, la version convient.

---

## 3. La configuration, hors dépôt

```bash
sudo mkdir -p /etc/data-collector
sudo cp /opt/data-collector/config.ini.template /etc/data-collector/config.ini
sudo chmod 640 /etc/data-collector/config.ini
sudo chown root:<utilisateur> /etc/data-collector/config.ini
```

Renseigner les valeurs réelles : adresse de l'horloge, répertoire d'archive, sujet
`ntfy`, paramètres SMTP.

Le mot de passe SMTP **ne va pas dans ce fichier** :

```bash
sudo sh -c 'umask 077; echo "DATA_COLLECTOR_SMTP_PASSWORD=..." > /etc/data-collector/env'
sudo chown root:<utilisateur> /etc/data-collector/env
sudo chmod 640 /etc/data-collector/env
```

⚠️ **Le sujet `ntfy` est un secret.** Un sujet public est une adresse devinable :
qui le connaît reçoit les notifications. Il ne figure jamais dans un fichier
versionné, ni en commentaire, ni à titre d'exemple.

### ⚠️ Une divergence à trancher avant d'activer les unités

Les unités livrées **ne passent pas `--config`**. Le collecteur retombe alors sur
son défaut, `~/.config/data-collector/config.ini` — c'est-à-dire le **home de
l'utilisateur du service**, pas `/etc`.

✅ Vérifié : `python3 -m collector --help` confirme ce chemin par défaut.

Deux voies cohérentes, une seule à choisir :

| Voie | Ce qu'il faut faire |
|---|---|
| **Configuration dans `/etc`** *(recommandée)* | Ajouter `--config /etc/data-collector/config.ini` aux trois `ExecStart`. Un service système ne devrait pas dépendre du home d'un utilisateur |
| Configuration dans le home | Placer le fichier dans `~<utilisateur>/.config/data-collector/config.ini` et ne rien changer aux unités. Mais le secret SMTP reste dans `/etc` — deux emplacements pour une même configuration |

**Ne pas laisser les deux exister.** Un fichier de configuration en double est
exactement la classe de défaut que le dépôt horloge vient de supprimer côté
firmware.

### L'archive

```bash
sudo mkdir -p /var/lib/data-collector
sudo chown <utilisateur>:<utilisateur> /var/lib/data-collector
```

Le chemin doit correspondre à `[archive] dir` du fichier de configuration.

---

## 4. Les minuteries

```bash
sudo cp /opt/data-collector/docs/systemd/*.service /etc/systemd/system/
sudo cp /opt/data-collector/docs/systemd/*.timer   /etc/systemd/system/
sudo systemctl daemon-reload
```

Avant d'activer quoi que ce soit, **un passage à la main** :

```bash
sudo systemctl start data-collector-poll.service
systemctl status data-collector-poll.service
journalctl -u data-collector-poll.service -n 50
```

Puis seulement :

```bash
sudo systemctl enable --now data-collector-poll.timer
sudo systemctl enable --now data-collector-report-weekly.timer
sudo systemctl enable --now data-collector-report-monthly.timer
systemctl list-timers 'data-collector-*'
```

| Minuterie | Déclenchement |
|---|---|
| `poll` | Toutes les heures, avec un retard aléatoire d'au plus 60 s |
| `report-weekly` | Lundi 07:00 — résumé bref par `ntfy` |
| `report-monthly` | Le 1er à 06:00 — courriel avec **l'archive complète en pièce jointe** |

`Persistent=true` sur les trois : une minuterie manquée pendant un arrêt se
rattrape au démarrage suivant. C'est ce qui fait qu'une coupure coûte un retard,
pas une perte.

---

## 5. La validation, avant l'horloge

Tout ce qui suit se fait **sur le Pi, contre le simulateur**, sans toucher à
l'horloge.

```bash
cd /opt/data-collector
python3 -m simulator --port 8099 --scenario nominal &
```

Pointer temporairement `[device:clock] url` sur `http://127.0.0.1:8099`, puis :

```bash
python3 -m collector --config /etc/data-collector/config.ini poll
```

✅ Vérifié sur la machine de développement : produit un CSV de 17 colonnes et un
`clock.state.json`, et un second passage n'ajoute rien.

**Les scénarios qui comptent**, à passer un par un — le simulateur les expose tous
par `--scenario` :

| Scénario | Ce qu'il prouve |
|---|---|
| `dead_sensor` | Un capteur muet donne un champ vide et un compteur à zéro. **Le défaut fondateur du projet ne peut pas se reproduire** |
| `gap` | Un trou d'horodatage reste un trou |
| `overwritten` | Un écrasement d'anneau est signalé, jamais silencieux |
| `unreachable` | Un appareil injoignable est un événement, pas « rien de neuf » |
| `time_untrusted` | Une heure dont la source n'est pas fiable n'entre pas dans l'archive |
| `device_restart` | Un redémarrage de l'appareil ne produit ni doublon ni trou artificiel |

⚠️ **non vérifié — les rapports.** `report --weekly` et `report --monthly`
demandent un réseau et des identifiants réels ; ils n'ont pas pu être exécutés
depuis la machine de développement. **À passer à la main tous les deux** avant
d'activer les minuteries, et à vérifier par la réception effective du message.

---

## 6. Vérifier la sauvegarde en la restaurant

**C'est le critère 5 d'`ADR-0004`, dans le dépôt `smart-led-clock`, et il ne se
coche pas sans l'avoir fait.**

Le courriel mensuel **est** la sauvegarde : il emporte l'archive entière, pas le
mois écoulé. Chaque message est donc censé être un point de restauration autonome.
« Censé » n'est pas une propriété — la dernière fois, `supervisor/backup/` était
vide et personne ne le savait.

```bash
python3 -m collector --config /etc/data-collector/config.ini report --monthly
```

Puis, depuis la pièce jointe reçue, sur une **autre machine** :

1. Décompresser dans un répertoire vide
2. Vérifier que l'arborescence contient les CSV de mesures, `events.csv` et l'état
3. Comparer octet à octet avec l'archive du Pi
4. Pointer un collecteur neuf sur ce répertoire restauré et lancer un `poll` :
   il doit reprendre là où l'archive s'arrête, sans redemander ce qu'il détient

⚠️ **Le point 4 est celui qui a de la valeur.** Les trois premiers vérifient qu'un
fichier existe ; le quatrième vérifie qu'il est **utilisable**. Une sauvegarde
qu'on n'a jamais restaurée n'est pas une sauvegarde, c'est une intention.

**Tant que ce point n'est pas passé, le critère 5 d'ADR-0004 reste ouvert**, quel
que soit le nombre de courriels reçus.

---

## 7. Résilience — ce qui survit à l'architecture précédente

Ces mesures venaient de `docs/BROKER.md` §5 du dépôt horloge. Le broker a disparu ;
**elles portaient sur le Pi, pas sur lui.** Elles valent donc telles quelles.

**Réservation DHCP pour l'horloge.** Son adresse est dans le fichier de
configuration du collecteur. C'est déjà un progrès considérable : avant, elle était
**codée en dur dans le firmware**, et la changer imposait un reflashage — donc une
dépose. Aujourd'hui c'est une ligne à éditer. La réservation reste préférable à un
bail qui bouge sans prévenir.

**Réduction des écritures sur la carte SD.** L'usure des cellules est la panne la
plus courante sur un Pi qui écrit en continu. `log2ram` pour les journaux. Une
écriture horaire d'archive est négligeable.

**Sauvegarde hors de la machine qu'elle protège.** C'est le rôle du courriel
mensuel. Une sauvegarde qui vit sur la carte SD du Pi n'en est pas une.

⚠️ **Ce que le Pi ne risque plus.** Il ne détient plus la seule copie des mesures :
l'horloge conserve 170 jours. **Une panne du collecteur coûte des rapports, pas des
mesures** — il rattrape en repartant. C'est tout l'objet de la bascule en pull, et
c'est ce qui distingue cette installation de celle qui a échoué.

---

## 8. Le jour de la dépose

L'ordre compte :

1. L'horloge est reflashée avec le firmware `/collect/` et le lot matériel
2. Elle est remontée, et son adresse vérifiée
3. `[device:clock] url` est repointé sur elle
4. **Un `poll` à la main**, avant toute minuterie
5. Le CSV produit est relu ligne à ligne : horodatage UTC, compteurs cohérents,
   aucune valeur là où un compteur vaut zéro
6. Les minuteries sont réactivées

⚠️ **C'est à ce moment que le collecteur devient le banc de test du firmware.**
Il a déjà trouvé un défaut que 118 tests natifs ne pouvaient pas attraper — le
paramètre `since` lu en epoch Unix là où le collecteur envoie de l'ISO 8601. Faire
dialoguer les implémentations réelles est la seule vérification qui compte.

---

## Voir aussi

- [`COLLECTE.md`](../COLLECTE.md) — le contrat, autorité de référence
- [`README.md`](../README.md) — ce qu'est le collecteur et pourquoi il vit ici
- `smart-led-clock/design/INTENTION.md` — pourquoi ces règles existent
