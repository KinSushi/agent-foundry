# servir_api

> OMISE

## Comment s'en servir

```
python outils/servir_api.py mesurer --chemin exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: servir_api.py [-h] {servir,mesurer} ...

Outil pour exposer des résultats d'analyse et de métriques via HTTP.

positional arguments:
  {servir,mesurer}
    servir          Exposer les résultats via HTTP.
    mesurer         Mesurer des métriques sur un fichier Python.

options:
  -h, --help        show this help message and exit

Exemple: python servir_api.py servir --chemin mon_fichier.py --host 0.0.0.0
--port 8000
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

pas de HTTPS natif. Mode dégradé avec wsgiref si modules tiers absents.

## Contre‑exemples

faulthandler ou dedent=True dans Interpreter.exec est évitée.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
