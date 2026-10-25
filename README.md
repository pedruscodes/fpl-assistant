# fpl-assistant

Prédire les points Fantasy Premier League de chaque joueur, puis choisir l'effectif de 15 qui maximise les points attendus sous les règles du jeu (budget 100 M£, 2/5/5/3 par poste, 3 joueurs max par club, XI valide et capitaine).

Suite de [RSPL](https://github.com/GonoyaPedrus/RSPL), projet de fin d'études, réécrit proprement.

## Démarrage

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ml]"
fpl pull        # récupère les données déjà publiées (quelques secondes)
fpl ingest      # ou tout retélécharger : vaastav + saison en cours via l'API (~3 min)
fpl baseline    # évalue la baseline « forme récente » sur un split temporel
fpl train       # entraîne LightGBM et le compare à la baseline
fpl recommend   # équipe de 15 recommandée pour la prochaine journée (--budget 100)
fpl transfers --entry 1234567   # transferts conseillés depuis ton équipe FPL
fpl login && fpl autopilot      # règle ton équipe (simulation sans --apply)
pytest
```

## Données

Saisons terminées issues de [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League) ; saison en cours (matchs joués, joueurs, prix, calendrier, ton équipe) via l'API officielle `fantasy.premierleague.com/api` (`fpl_assistant.api`). Les données ne sont pas versionnées : `data/` est ignoré par git.

## Mise à jour automatique

La GitHub Action [Ingestion](.github/workflows/ingest.yml) tourne chaque matin à 6 h UTC. Si une nouvelle journée est terminée et validée par la FPL (`fpl latest`, ex. `2026-27:6:v1`), ou si le format des données a changé (`SCHEMA_VERSION`), elle relance l'ingestion et publie `gw.parquet` dans la release [`data-latest`](https://github.com/GonoyaPedrus/fpl-assistant/releases/tag/data-latest). `fpl pull` télécharge ce fichier. On peut aussi la lancer à la main depuis l'onglet Actions (option « force »). GitHub désactive les workflows planifiés après 60 jours sans activité sur le dépôt : il faut alors la réactiver dans l'onglet Actions.

## Modèle

LightGBM sur des features connues avant la deadline : forme sur 3 et 5 journées (points, minutes, BPS, ICT, buts, passes), prix, poste, nombre de matchs dans la journée, domicile, et force récente de l'équipe et de l'adversaire (buts marqués et encaissés sur 6 journées). Toutes les moyennes sont décalées d'une journée.

| Test | Modèle | RMSE | MAE | RMSE (ont joué) | Top 20 / journée |
|---|---|---|---|---|---|
| 2025-26, J20 → J38 | forme 5 J | 2,07 | 1,02 | 3,20 | 3,77 pts |
| | LightGBM | **1,90** | **0,95** | **2,97** | **4,48 pts** |
| 2024-25, J1 → J38 | forme 5 J | 2,13 | 1,06 | 3,13 | 4,19 pts |
| | LightGBM | **1,96** | **1,01** | **2,86** | **4,73 pts** |

« Top 20 » : points réels moyens des 20 joueurs les mieux classés par le modèle, journée par journée. C'est la métrique la plus proche d'une décision FPL.

## Équipe recommandée

`fpl recommend` lit le calendrier de la prochaine journée dans l'API officielle, entraîne LightGBM sur tout l'historique et prédit les points de chaque joueur (doubles journées comprises, 0 pour un club qui ne joue pas). Il multiplie ensuite par la probabilité de jouer (`chance_of_playing`, 0 si blessé, suspendu ou parti) et choisit les 15 joueurs, le XI et le capitaine avec l'optimiseur. Les prix sont ceux du jour.

À lancer après la validation de la journée en cours (`fpl pull` récupère les données publiées le matin), sinon la forme ignore la dernière journée.

## Transferts

`fpl transfers --entry <ID>` part de ton équipe (l'ID est dans l'URL de la page « Points » sur le site FPL) et cherche les transferts qui maximisent les points attendus de la prochaine journée, pénalité de -4 par transfert payant déduite. Il affiche les départs et arrivées, les points attendus avec et sans transfert, puis l'effectif et le capitaine.

Tout vient de l'API publique, sans connexion, avec trois limites :

- l'effectif est celui de la dernière deadline passée : les transferts déjà faits pour la prochaine journée n'y sont pas ;
- les transferts gratuits sont estimés depuis l'historique de l'équipe (un par journée, jusqu'à cinq, gardés pendant un wildcard ou un free hit) : `--free-transfers N` corrige l'estimation, `--max-transfers N` plafonne le nombre de transferts ;
- les prix de revente suivent la règle FPL (la moitié de la plus-value), mais le prix d'achat d'un joueur pris à la création de l'équipe est celui de la journée de départ, pas celui du jour exact.

L'horizon est d'une seule journée : un transfert gratuit sera utilisé dès qu'il rapporte un peu, même si le garder pour la semaine suivante vaudrait mieux. Le gain affiché aide à trancher.

## Pilote automatique

`fpl autopilot` règle ton équipe pour la prochaine journée : transferts, XI, capitaine et vice-capitaine, envoyés à la FPL avec `--apply` (sans, il simule). Connecté à ton compte, il lit ton équipe exacte : transferts gratuits, prix de revente, transferts déjà faits. Il prend au plus un -4 par journée (`--max-hits` pour changer) et ne joue jamais de chip.

Mise en route, une fois, sur ta machine :

```bash
fpl login          # suis les étapes affichées (fenêtre de navigation privée)
fpl pull && fpl autopilot  # simulation, pour vérifier
fpl login --print  # 2e connexion, pour l'Action : affiche son jeton sans le garder ici
```

Dans le dépôt, Settings > Secrets and variables > Actions, colle ce jeton dans le secret `FPL_REFRESH_TOKEN` et ton ID d'équipe (affiché par `fpl login`) dans la variable `FPL_ENTRY`. Le jeton donne la main sur ton compte : ne le colle nulle part ailleurs. Avec la CLI GitHub, `gh secret set FPL_REFRESH_TOKEN --body "$(fpl login --print)"` évite même de l'afficher.

L'Action a son propre jeton : ainsi les usages locaux et ceux de l'Action ne se gênent pas.

La GitHub Action [Pilote automatique](.github/workflows/autopilot.yml) tourne ensuite plusieurs fois par jour (GitHub espace ses passages de 3 à 6 heures). Le premier passage des 6 h 30 avant chaque deadline vérifie la connexion, attend jusqu'à une heure avant, puis règle l'équipe une fois et publie le compte rendu dans une issue `autopilot`, ce qui t'envoie une notification. Si quelque chose échoue, elle ouvre une issue d'alerte. À chaque passage, elle vérifie aussi que la connexion tient (`fpl whoami`), pour qu'un jeton annulé se voie bien avant la deadline ; un jeton refusé ouvre une seule issue d'alerte, sans mail d'échec à chaque passage, et une simple panne de la FPL n'en ouvre pas. Après un nouveau `fpl login --print`, lance-la à la main depuis l'onglet Actions, sans « force », pour tester le jeton tout de suite. Un chip activé à la main sur le site est conservé ; le pilote n'en joue jamais lui-même.

La FPL donne un nouveau jeton à chaque usage, et une Action ne peut pas réécrire ses secrets. Le pilote garde donc le dernier jeton d'un passage à l'autre dans le cache de l'Action, chiffré avec le secret `FPL_REFRESH_TOKEN` (les forks n'y ont pas accès). Le secret ne sert qu'au premier passage ; après un nouveau `fpl login --print`, le pilote repart du nouveau secret. La FPL annule tous les jetons d'une session de connexion dès que le site s'y reconnecte. Fais donc `fpl login` dans une fenêtre de navigation privée neuve, ferme-la aussitôt, et regarde ton équipe ailleurs (navigateur habituel, appli) : sinon, ta visite suivante sur le site coupe le pilote.

Il faut refaire `fpl login --print` et remplacer le secret au bout de six mois (la FPL le demande), si l'Action reste arrêtée plus d'une semaine (GitHub vide alors son cache) ou si la FPL a annulé le jeton. La vérification de connexion ouvre une alerte dans ces cas-là.

Règlement : l'article 28 (d) des [conditions FPL](https://fantasy.premierleague.com/en/help/terms) interdit d'accéder au jeu par des systèmes automatisés, et l'article 36 prévoit suspension ou disqualification en cas de manquement.

## Feuille de route

1. ~~Ingestion + baseline forme récente évaluée sur split temporel~~
2. ~~Modèle LightGBM qui bat la baseline~~ ; à enrichir : minutes attendues, xG/xA, statut médical
3. ~~Optimiseur PuLP branché sur les prédictions de la prochaine journée~~ (`fpl recommend`)
4. ~~Transferts depuis mon équipe actuelle, avec le coût des transferts (-4)~~ (`fpl transfers`) ; à faire : horizon de plusieurs journées
5. ~~Pilote automatique avant chaque deadline~~ (`fpl autopilot`) ; à faire : chips, puis une interface
