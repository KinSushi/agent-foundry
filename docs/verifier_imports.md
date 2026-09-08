# verifier_imports

> OMISE

## Comment s'en servir

```
verifier_imports.py mon_fichier.py --racine . --proches
```

Sous-commande `fichier` : Fichier Python à vérifier.

## Toutes les options

```
usage: verifier_imports.py [-h] [--racine RACINE] [--hors-ligne] [--proches]
                           [--json]
                           fichier

positional arguments:
  fichier          Fichier Python à vérifier

options:
  -h, --help       show this help message and exit
  --racine RACINE  Racine du projet (défaut: répertoire de l'outil)
  --hors-ligne     Ne pas interroger PyPI
  --proches        Afficher les noms proches
  --json           Sortie JSON

Exemple : verifier_imports.py mon_fichier.py --racine . --proches
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

le nom d'import diffère parfois du nom de distribution ; un import dans un try/except ImportError est légitime ; les imports dynamiques (importlib.import_module(nom)) échappent ; modulefinder rate les imports dynamiques et rapporte les imports conditionnels même non exécutés

## Contre‑exemples

0.8.0 sur PyPI — accuser un import légitime est la faute que cet outil doit éviter

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
