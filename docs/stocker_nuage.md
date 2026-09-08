# stocker_nuage

> OMISE

## Comment s'en servir

```
python stocker_nuage.py lister --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: stocker_nuage.py [-h] [--racine RACINE] {lister,lire,ecrire} ...

Lire, écrire et lister des objets dans un stockage cloud.

positional arguments:
  {lister,lire,ecrire}
    lister              Lister les objets
    lire                Lire un objet
    ecrire              Écrire un objet

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du stockage local

Exemple: python stocker_nuage.py lister --json
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
