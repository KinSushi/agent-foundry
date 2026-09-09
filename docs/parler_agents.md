# parler_agents

> Peut-on échanger des messages fiables entre agents sans blocage ni perte ?

## Comment s'en servir

```
python outils/parler_agents.py serialiser exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: parler_agents.py [-h] [--json] [--racine RACINE]
                        {echanger,garantie,superviser,chiffrer,router,groupe,serialiser,attendre} ...

Échange de messages entre agents IA

positional arguments:
  {echanger,garantie,superviser,chiffrer,router,groupe,serialiser,attendre}
    echanger            Échange un message sans blocage
    garantie            Envoie un message avec garantie de livraison
    superviser          Supervise un broker ZeroMQ
    chiffrer            Génère une paire de clés Curve
    router              Route dynamiquement les messages
    groupe              Envoie un message à un groupe
    serialiser          Sérialise un objet Python
    attendre            Attend un événement parmi plusieurs sources

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON
  --racine RACINE       Racine du projet (défaut: répertoire du script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Aucun test de perte réseau extrême, pas de simulation de redémarrage complet du broker.

## Contre‑exemples

Un broker redémarré pendant l'envoi d'un message avec `garantie` renvoie un timeout alors que le message a été stocké côté client.

## Ce qu'il lui faut

msgpack, zmq — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

