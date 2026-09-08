# carte_appels

> OMISE

## Comment s'en servir

```
python carte_appels.py analyser --racine /chemin/depot
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: carte_appels.py [-h] [--racine RACINE] [--json]
                       {analyser,lire,orphelins} ...

Cartographie statique des appels Python.

positional arguments:
  {analyser,lire,orphelins}
    analyser            Analyse le dépôt et compte les appels par niveau.
    lire                Remonte les appelants et appelés d'une fonction.
    orphelins           Trouve les fonctions non appelées.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du projet à analyser.
  --json                Sortie JSON sur stdout.

Exemple: python carte_appels.py analyser --racine /chemin/depot
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
