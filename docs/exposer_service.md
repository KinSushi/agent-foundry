# exposer_service

> Le serveur expose-t-il correctement la spécification OpenAPI ?

## Comment s'en servir

```
python outils/exposer_service.py augmenter-doc . --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: exposer_service.py [-h] [--json] [--racine RACINE]
                          {augmenter-doc,ameliorer-valider,injecter-db,background-tache,concurrency-async} ...

Exposer un service HTTP avec validation et documentation OpenAPI

positional arguments:
  {augmenter-doc,ameliorer-valider,injecter-db,background-tache,concurrency-async}
    augmenter-doc       Génère openapi.json dans le répertoire indiqué
    ameliorer-valider   Valide un payload JSON contre un schéma JSON Schema
    injecter-db         Crée une dépendance FastAPI pour une connexion DB
    background-tache    Exécute une commande en arrière-plan après la réponse
                        HTTP
    concurrency-async   Démarre un serveur FastAPI en mode asyncio

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON
  --racine RACINE       Répertoire racine du projet (défaut: répertoire du
                        script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

La validation ne couvre pas les extensions propriétaires du schéma.

## Contre‑exemples

Un endpoint défini avec @app.get("/items") mais sans modèle de réponse génère une spécification incomplète.

## Ce qu'il lui faut

fastapi, uvicorn — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

