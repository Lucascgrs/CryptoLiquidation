# CryptoLiquidation

Voir les liquidations des cryptos **en direct**, sur dix plateformes à la fois,
et les **niveaux de liquidation réels** des positions ouvertes sur Hyperliquid.

Rien n'est estimé :
- chaque liquidation du flux a été publiée par sa plateforme (ou, pour
  Hyperliquid, confirmée une à une, voir plus bas) ;
- chaque niveau de la carte est le prix de liquidation d'une position réelle,
  lu dans le compte de son propriétaire.

Les plateformes centralisées (Binance, Bybit, OKX…) ne publient pas les
positions de leurs clients : leurs niveaux de liquidation ne peuvent être
qu'estimés, et ce projet ne le fait pas. Seul Hyperliquid, où tout est public,
permet de voir les vrais niveaux.

## Lancer

```bash
cd CryptoLiquidation
pip install -r requirements.txt
python Dashboard.py
```

Puis, dans la page **Collecteur**, « Démarrer la collecte ». Rien n'est
collecté tant que la collecte est arrêtée. Elle peut aussi tourner sans
l'interface :

```bash
python main.py collecter              # Ctrl+C pour arrêter
python main.py collecter --duree 2h   # arrêt automatique
```

L'interface lit ce que le collecteur écrit, qu'il ait été lancé par elle ou
dans une console. On peut la fermer et la rouvrir sans couper la collecte, si
l'option « Arrêter la collecte en fermant l'interface » est décochée.

## Les plateformes

| Plateforme | Ce qu'elle publie | Complète ? |
|---|---|---|
| Binance | la plus grosse liquidation de chaque contrat, par seconde | non |
| Bybit | toutes les liquidations, toutes les 500 ms | oui |
| OKX | environ la moitié par le flux, complétée toutes les 15 s par l'API REST | presque |
| Bitget | la plus grosse liquidation long et la plus grosse short de chaque contrat, par seconde | non |
| Gate | toutes les liquidations des contrats USDT, regroupées par seconde | oui |
| HTX | les liquidations des contrats USDT (HTX ne dit pas si son flux les contient toutes) | ? |
| Kraken | toutes les transactions de liquidation, totales ou partielles | oui |
| Deribit | toutes les transactions marquées « liquidation » | oui |
| dYdX | toutes les transactions de liquidation | oui |
| Hyperliquid | rien de marqué : liquidations reconnues et confirmées une à une | non |

BitMEX n'a pas pu être joint depuis cette machine (adresse introuvable) : il
n'est pas collecté.

Mesures et vérifications faites le 30/10/2026 :
- **sens long / short** : sur une vague de ventes, toutes les plateformes
  donnaient des longs liquidés. Attention, chacune l'écrit à sa façon. Bybit
  et Bitget donnent le côté de la position (« Buy » = un long liquidé), les
  autres le côté de l'ordre de liquidation (« sell » = un long liquidé) ;
- **OKX** : 17 liquidations sur 30 pour ETH-USDT en 13 minutes par le flux,
  toutes présentes dans l'API REST, d'où le complément REST ;
- **Gate** : flux et API REST d'accord (6 sur 6 pour BTC, 25 sur 30 pour ETH) ;
- **prix** : une fois ramenés à une unité, les prix d'un même actif concordent
  d'une plateforme à l'autre. Par exemple, PEPE vaut 4,6 × 10⁻⁶ $ partout,
  qu'il soit coté 1000PEPE (Binance), kPEPE (Hyperliquid) ou PEPE (OKX).

## Hyperliquid

### Les positions (page Niveaux)

Hyperliquid ne donne pas la liste des positions : il faut lire les comptes un
par un. Le collecteur en lit environ 380 par minute, la limite de l'API (1200
« poids » par minute et par adresse IP). Les comptes viennent de deux sources :
- le **classement public** : environ 24 000 comptes de plus de 1000 $, lus des
  plus riches aux plus modestes ;
- le **flux des transactions** : toute adresse qui trade est relue dans les
  secondes qui suivent, les plus gros montants d'abord. Toute position qui
  change passe par une transaction : c'est ce qui garde la base à jour.

Une position proche de son prix de liquidation (moins de 2 %) est relue chaque
minute, une grosse position toutes les 5 minutes, une petite toutes les
30 minutes.

La part de l'open interest connue s'affiche en haut de la page. Elle grandit
avec le temps de collecte : environ 30 % pour BTC et 40 % pour ETH après
20 minutes, sur une base vide. Les positions restent dans la base entre deux
sessions, avec leur heure de lecture (filtre « Lues »).

### Les liquidations

Le flux public d'Hyperliquid ne marque pas les liquidations, mais il donne
l'agresseur de chaque transaction. Une liquidation est un ordre au marché
passé au nom du liquidé : il en est l'agresseur. Elle n'est publiée que si
l'une de ces trois preuves la confirme :
1. **contrepartie HLP** : les coffres HLP et quatre gros teneurs de marché
   sont suivis en direct. Hyperliquid marque leurs transactions quand l'autre
   partie est liquidée ;
2. **prix franchi** : l'agresseur ferme une position connue à un prix qui a
   dépassé son prix de liquidation ;
3. **vérification** : l'historique du compte est lu, et la plateforme y marque
   la liquidation. Cela concerne les cas douteux et les comptes inconnus au-delà
   de 2000 $.

Un stop déclenché dans le même bloc ressemble à une liquidation. C'est pourquoi
aucun cas douteux n'est publié sans vérification. Les petites liquidations de
comptes jamais lus peuvent manquer.

## L'interface

### En direct

- **Filtres** : la crypto (liste des plus liquidées, ou tape un nom puis
  Entrée), la fenêtre (5 min à 24 h), la taille minimale, la vue, et les
  plateformes. Un clic droit sur une plateforme ne garde qu'elle.
- **Chiffres de la fenêtre** : le total, les longs, les shorts, la plus grosse
  liquidation, et la dernière minute comparée à la moyenne de la fenêtre (un
  « 3 × la moyenne » signale une cascade). La barre rouge et cyan montre la
  part des longs et des shorts.
- **La rivière** : le temps défile de droite à gauche.
  - En vue **Volcan**, les shorts liquidés jaillissent au-dessus de l'axe et
    les longs liquidés tombent en dessous. Plus une bulle est loin de l'axe,
    plus la liquidation est grosse.
  - En vue **Prix** (une crypto), chaque bulle est placée au prix de la
    liquidation, sur la courbe du prix. Le voile gris montre le plus haut et
    le plus bas de chaque minute.
  - Sous la rivière, un histogramme montre le montant liquidé par tranche de
    temps.
  - Le survol d'une bulle donne le détail, et un clic la retrouve dans le fil.
- **Le fil** : les dernières liquidations, la plus récente en haut.
- **Le bilan** : par plateforme et par crypto, les longs à gauche, les shorts
  à droite. Un clic sur une crypto la choisit. Une astérisque marque une
  plateforme qui ne publie qu'une partie de ses liquidations.

Rouge : longs liquidés (le prix a baissé). Cyan : shorts liquidés (le prix a
monté). Le côté est aussi toujours donné par la position et le texte.

### Niveaux Hyperliquid

La carte d'une crypto, prix courant au centre, a trois bandes :
1. **les positions**, une bulle chacune, à leur prix de liquidation. Une bulle
   est d'autant plus haute et large que la position est grosse, et les plus
   grosses portent leur valeur et leur levier. Une bulle pâle signale une
   position lue il y a plus d'une heure ;
2. **le montant à liquider** par tranche de prix ;
3. **le cumul** : tout ce qui serait liquidé si le prix allait jusque-là.

En bas, des traits marquent le prix des liquidations des 10 dernières minutes,
toutes plateformes. Le survol d'une bulle détaille la position (taille, prix
d'entrée, levier, marge, compte). Le survol des bandes 2 et 3 dit ce qui serait
liquidé à ce prix.

Le tableau de droite liste les positions de la plage. Un clic sur un titre de
colonne trie le tableau. Un double-clic ouvre le compte dans l'explorateur
Hyperliquid. Filtres : plage de prix, taille, marge (croisée ou isolée),
ancienneté de la lecture.

### Collecteur

- Le bouton Démarrer / Arrêter.
- Le choix des plateformes.
- Les options : lire les positions Hyperliquid, arrêter en fermant,
  démarrer à l'ouverture.
- L'état de chaque connexion.
- Le travail d'Hyperliquid : comptes lus, file d'attente, liquidations
  reconnues par preuve, débit de l'API.
- Le journal.

## Ligne de commande

```bash
python main.py collecter [--duree 2h] [--sources binance,bybit] [--sans-positions]
python main.py etat                          # collecteur en marche ? contenu des données
python main.py arreter                       # arrête le collecteur en marche
python main.py liquidations --depuis 1h [--crypto BTC]
python main.py positions --crypto BTC [--plage 10]
```

## Les données (`donnees/`)

| Fichier | Contenu |
|---|---|
| `liquidations/AAAA-MM-JJ.csv` | une ligne par liquidation (jour UTC) : `t` (ms), `plateforme`, `marche`, `actif`, `cote` (position liquidée), `prix` (par unité), `quantite` (en unités), `valeur` ($), `detail` |
| `hyperliquid.sqlite` | comptes, positions (prix de liquidation, levier, marge…) et prix de marque |
| `etat_collecteur.json` | l'état du collecteur, réécrit chaque seconde |
| `collecteur.log` | le journal |
| `reglages.json` | les choix de l'interface et du collecteur |

Rien n'est jamais effacé : le registre ne fait que grandir, un fichier par
jour. L'interface garde en mémoire les dernières 24 heures.

## Organisation du code

```
Dashboard.py                  ouvre l'interface
main.py                       ligne de commande
liquidation_lab/
  config.py                   chemins, plateformes, réglages
  modele.py                   une liquidation
  symboles.py                 1000PEPE, kPEPE… -> PEPE
  stockage.py                 registre CSV (écriture, relecture, suivi)
  collecteur.py               toutes les sources, l'état, l'arrêt
  niveaux.py                  carte des niveaux (cumuls, tranches)
  sources/                    une source par plateforme
  hyperliquid/                API, base SQLite, lecture des comptes, détection
  interface/                  pages, vues dessinées avec cairo
```
