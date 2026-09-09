# cache_distribue

> OMISE

## Comment s'en servir

```
python outils/cache_distribue.py --racine exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: cache_distribue.py [-h] [--racine RACINE] [--json]

Analyse le partage de cache entre processus dans du code Python.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Racine du projet à analyser (défaut : répertoire de
                   l'outil).
  --json           Sortie JSON au lieu de texte humain.

Exemple : python cache_distribue.py --racine ./src
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

OMISE

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

