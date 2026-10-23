# TP Kafka : gérer une flotte de camions

Votre équipe achète des matières premières, les transporte à l'usine,
puis vend les produits fabriqués à des clients.

Deux scores comptent : l'argent disponible et la réputation.
Le classement les présente séparément.
Un partenaire moins cher peut aussi faire baisser votre réputation.

Visez une première vente simple, puis améliorez votre stratégie pendant la partie.

## 1. Installer le client

Il faut [uv](https://docs.astral.sh/uv/getting-started/installation/).
Pas besoin de Docker ni d'installer Kafka sur votre poste.

1. Installez uv avec la procédure adaptée à votre système, puis ouvrez un nouveau terminal.
2. Ouvrez le dossier du client : celui qui contient ce guide et
   [pyproject.toml](pyproject.toml).
   **Lancez toutes les commandes suivantes depuis ce dossier.**
3. Installez les dépendances :

   ```bash
   uv sync
   ```

   Python 3.12 ou plus récent est nécessaire. uv peut le télécharger si besoin.

### Configurer votre équipe

1. Ouvrez la page `/register` du site donné par l'encadrant.
2. Choisissez un nom d'équipe unique, puis cliquez sur **Register**.
3. Le serveur génère automatiquement votre couple de clés : une clé privée et une clé publique.
   Vous n'avez pas de commande de génération à lancer.
4. Sur la page suivante, copiez le bloc **Ready-to-use .env for client-student**
   dans un fichier `.env`, à la racine du client.
5. Sauvegardez aussi le mot de passe affiché. Il sert à vous connecter au site.
   Copiez ces informations avant de quitter la page : elles ne sont affichées qu'une fois.

`TEAM_PRIVATE_KEY` sert à signer vos commandes. `TEAM_PUBLIC_KEY` est la clé publique de votre équipe.
Le bloc contient aussi `ADMIN_PUBLIC_KEY`, la clé publique du serveur, pour vérifier ses messages.
Le fichier [.env.example](.env.example) montre les réglages disponibles.

Ajoutez l'adresse Kafka :

```dotenv
KAFKA_BOOTSTRAP_SERVERS=ADRESSE_KAFKA:PORT
```

Remplacez `ADRESSE_KAFKA:PORT` par la vraie adresse.
Ce n'est pas l'adresse du site web. Ne gardez pas `localhost:9092` pour un serveur distant.

Gardez les guillemets et les `\n` dans les clés.
Ne partagez pas votre clé privée et ne commitez pas le fichier `.env`.

## 2. Comprendre les échanges

Un **topic** est un flux de messages Kafka.
Il est découpé en partitions. Dans chaque partition, un message a une position : son **offset**.

Le client utilise deux topics :

| Topic           | Sens                         | Contenu                  |
| --------------- | ---------------------------- | ------------------------ |
| `game.commands` | Votre client vers le serveur | Les actions demandées    |
| `game.facts`    | Le serveur vers votre client | Les événements confirmés |

Une commande demande une action. Un fait indique ce qui s'est réellement passé.
**Envoyer une commande ne suffit pas à modifier l'état du jeu.**

Au démarrage, le client relit les faits depuis le début pour retrouver son état.
Les lieux, les prix et les recettes sont annoncés dans un fait `world_snapshot`.
Les faits suivants mettent à jour l'état du jeu.

Les achats de camions passent aussi par Kafka, avec la commande `buy_truck`.
Le site sert à l'inscription, au classement et au suivi des erreurs.

Les signatures et la protection contre les messages rejoués sont déjà fournies.
Vous n'avez pas à réécrire cette partie.

## 3. Exercices de démarrage

Les endroits à compléter lèvent une erreur `NotImplementedError` avec un numéro `TODO`.
Les tests correspondants échouent au départ : c'est normal.

Les tests fonctionnent **sans serveur Kafka**. Ils ne prouvent donc pas que votre connexion au serveur distant fonctionne.
Lancez un groupe de tests à la fois pour voir votre progression.

### Exercice 1 : envoyer une commande

Dans [kafka_io.py](student_client/kafka_io.py), complétez `CommandProducer.send`.

- Publiez les octets reçus dans `value`, sans modifier le message signé.
- Utilisez le topic demandé et le nom de l'équipe comme clé Kafka.
- Donnez `_delivery_report` à Kafka comme fonction à appeler après la livraison du message.
- Traitez ces retours sans bloquer la boucle, pour afficher les échecs d'envoi.

Une livraison confirme l'envoi à Kafka, pas l'exécution de l'action dans le jeu.

```bash
uv run pytest -m step1
```

### Exercice 2 : relire l'historique

Dans [kafka_io.py](student_client/kafka_io.py), complétez `FactsConsumer._assign_from_beginning`.

Affectez toutes les partitions reçues, en commençant au début de chacune.
Ne partez pas de la fin du topic ni d'une position enregistrée lors d'un ancien lancement.

Chaque équipe doit lire tous les faits.
Le filtrage des destinataires vient ensuite.

```bash
uv run pytest -m step2
```

### Exercice 3 : trier les messages

Dans [client.py](student_client/client.py), complétez :

- `_decode_envelope` : décoder les octets UTF-8 en objet JSON ;
- `_message_for_team` : valider le contenu avec `FactMessage` et vérifier le destinataire.

Gardez les faits de votre équipe et les diffusions générales (`team: null`).
Ignorez ceux des autres équipes.
Un message mal formé ou un type de fait inconnu doit être signalé dans les logs,
puis ignoré sans arrêter la lecture.

```bash
uv run pytest -m step3
```

Vous pouvez maintenant tester un aller-retour avec le serveur :

```bash
uv run python -m student_client ping
```

Attendez `Ping OK`. Le ping ne lance pas votre stratégie.
Il fonctionne même si le jeu n'a pas encore démarré.

### Exercice 4 : mettre à jour les camions

Dans [state.py](student_client/state.py), complétez :

- `_apply_loaded` : retirer la quantité chargée du stock et l'ajouter au camion ;
- `_apply_travel` : mettre à jour la position, la destination et l'heure d'arrivée.

Un camion parti n'est plus garé à son point de départ.
À l'arrivée, son trajet est terminé et sa cargaison reste inchangée.
Appuyez-vous sur les faits reçus, pas sur une attente calculée dans votre stratégie.

```bash
uv run pytest -m step4
```

## 4. Écrire une première stratégie

Complétez `StudentStrategy` dans [strategy.py](student_client/strategy.py).

| Méthode    | Quand est-elle appelée ?                                |
| ---------- | ------------------------------------------------------- |
| `on_live`  | Une fois l'historique relu                              |
| `on_facts` | Après un lot de nouveaux faits, déjà appliqués à l'état |
| `on_tick`  | Environ chaque seconde ; son usage est facultatif       |

`ctx.state` contient les lieux, les camions, les stocks et les scores.
Utilisez `ctx.state.stock_at(lieu_id, resource_code)` pour consulter un stock.
Attendez que `ctx.state.world_known` soit vrai avant de choisir un trajet :
ce drapeau indique que le premier `world_snapshot` a été reçu.
Prenez les identifiants des lieux dans cet état, sans les inventer.

Construisez les commandes avec les classes de [models.py](student_client/models.py),
puis envoyez-les avec `ctx.send(commande)`.
La signature et l'envoi sont pris en charge.

### Objectif : une première vente

Commencez avec un camion et un circuit fixe :

1. Choisissez un modèle dans `ctx.state.truck_models`, puis demandez son achat avec
   `buy_truck` et son `model_code`.
   Attendez `truck_purchased` : ce fait fournit le `truck_id` à utiliser pour la suite.
   Le camion démarre au dépôt, en position `(0, 0)`.
2. Envoyez-le chez un fournisseur (`dispatch_truck`) et attendez `truck_arrived`.
3. Achetez une matière (`buy_resource`), puis chargez-la (`load_truck`).
   L'achat seul laisse la matière chez le fournisseur.
4. Livrez-la à l'usine avec `dispatch_truck`, puis `unload_truck`.
   Répétez pour tous les ingrédients de la recette.
5. Lancez la recette (`start_recipe`) et attendez `recipe_completed`.
   Les produits restent en stock à l'usine.
6. Chargez les produits, livrez-les chez un client et déchargez : c'est la vente.

Choisissez des lieux ouverts (`active`).
Le fournisseur doit vendre les ingrédients de la recette, et le client acheter le produit fabriqué.
Respectez les quantités de la recette et la capacité du camion.

Attendez la confirmation de chaque action avant d'envoyer la suivante.
Pour un trajet, attendez l'arrivée, pas seulement le fait `truck_dispatched`.
Ne renvoyez pas la même commande à chaque appel de votre stratégie.

Un camion porte un seul type de ressource à la fois, dans la limite de sa capacité.
Gardez assez d'argent pour les matières et la fabrication : n'achetez pas trop de camions d'un coup.

### Si l'encadrant remet votre équipe à zéro

Le serveur envoie `score_updated` avec `reset: true`.
Le client efface vos camions et stocks, puis applique les nouveaux scores.
Les lieux, prix et recettes restent connus.

Si votre stratégie garde des actions en attente, redéfinissez `on_reset(ctx)`
pour les oublier. N'envoyez pas de commande dans cette méthode :
`on_facts` reçoit ensuite le reset et les faits suivants.

L'historique Kafka est conservé. Au redémarrage, le client le relit et applique
le reset avant les nouveaux achats. La stratégie démarre après cette lecture.

### Tester et lancer

#### Tester une décision sans Kafka

[test_05_strategy_example.py](tests/test_05_strategy_example.py) utilise une petite
`ExampleStrategy`, pas votre `StudentStrategy`.
Elle demande seulement un premier camion.

Les tests vérifient les décisions sans Kafka ni dépense dans la partie.
Les exemples montrent comment vérifier :

- un achat avec assez d'argent, ou aucun achat si le budget manque ;
- l'attente de l'état du jeu et de la confirmation d'achat ;
- l'absence d'achats en double, y compris après un redémarrage.

Pour écrire un test :

1. Préparez l'état et la stratégie.
2. Appelez la méthode à tester.
3. Vérifiez les commandes demandées.

`sent.append` garde les commandes dans une liste au lieu de les envoyer.
Dans les exemples, [conftest.py](tests/conftest.py) prépare un état neuf pour chaque test :
100 unités d'argent et un modèle à 10.

Pour tester une réaction à un fait, appelez `state.apply(fait)` puis
`strategy.on_facts(context, [fait])`, comme le client.
Les faits sont fournis par le test, pas par un serveur.

```bash
uv run pytest -m step5 -v
```

Le cas du solde à 10 détecte un `>` utilisé à la place de `>=`.

#### Écrire vos propres tests

Créez `tests/test_my_strategy.py` avec votre `StudentStrategy`.
Par exemple, vérifiez qu'elle attend le premier snapshot :

```python
from student_client.models import Command
from student_client.state import GameState
from student_client.strategy import Context, StudentStrategy


def test_waits_for_the_first_snapshot():
    state = GameState()  # Aucun lieu ni modèle connu.
    sent: list[Command] = []
    context = Context(state=state, send=sent.append, now=lambda: 0.0)
    strategy = StudentStrategy()

    strategy.on_live(context)
    strategy.on_tick(context)

    assert sent == []
```

Ce test échoue tant que `on_live` lève son erreur TODO.

```bash
uv run pytest tests/test_my_strategy.py -v
```

Ajoutez vos cas : camion en route, stock déjà acheté, usine fermée ou commande refusée.
Les décisions attendues dépendent de votre stratégie.
Les tests de `ExampleStrategy` ne valident pas votre code.

#### Jouer avec le serveur

Une fois les exercices 1 à 4 terminés, votre stratégie écrite et le ping réussi, lancez votre client :

```bash
uv run python -m student_client
```

Ce mode envoie vraiment les commandes : vos achats et vos ventes comptent dans la partie.
`Ping OK` indique que le serveur répond. `History replayed` indique que l'historique a été lu.
Ajoutez des logs sur les commandes choisies et vérifiez les résultats sur `/scoreboard`.

Arrêtez-le avec `Ctrl+C`, modifiez votre code, puis relancez-le.
Le client relira l'historique ; votre argent et vos camions ne sont pas remis à zéro.
Les variables de votre stratégie, elles, sont recréées.
Repartez des camions et des stocks existants au lieu de répéter les achats.
Ne lancez qu'un client à la fois pour votre équipe.

## 5. Améliorer votre stratégie

Une fois la première vente réussie, choisissez une amélioration :

- après chaque vente, lancer automatiquement le lot de production suivant ;
- comparer les prix, les distances et la réputation des partenaires ;
- répartir les transports entre plusieurs camions ;
- changer de trajet si un lieu ferme ou si un prix change ;
- reprendre une action refusée sans envoyer des commandes en boucle.

Mesurez l'effet d'un changement avant d'en ajouter un autre.
Vous pouvez lancer tous les tests avec `uv run pytest`.

## 6. En cas de problème

Sur le site du jeu :

- `/scoreboard` : argent, réputation et classement ;
- `/debug/teams/VOTRE_EQUIPE` : commandes refusées de votre équipe ;
- `/debug/global` : messages que le serveur n'a pas pu attribuer à une équipe.

Connectez-vous avec le mot de passe reçu à l'inscription pour ouvrir les pages de debug.

| Symptôme                             | À vérifier                                                                 |
| ------------------------------------ | -------------------------------------------------------------------------- |
| `NotImplementedError: TODO ...`      | Complétez la méthode indiquée.                                             |
| `Ping FAILED`                        | Vérifiez l'adresse Kafka, le réseau, les clés et si le serveur fonctionne. |
| État du jeu absent                   | Vérifiez le rejeu, le filtrage et si la partie a démarré.                  |
| `command_rejected`                   | Lisez `reason` et la page de debug de votre équipe.                        |
| Jeu non démarré, en pause ou terminé | Les commandes de jeu sont refusées. Le ping reste accepté.                 |
