# generateur_aleatoire

> OMISE

## Comment s'en servir

```
d'appel réel : python generateur_aleatoire.py --nombre 5 --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: generateur_aleatoire.py [-h] [--nombre NOMBRE] [--json]
                               [--racine RACINE]

Génère des nombres aléatoires sans le module `random` en utilisant `hashlib`.

options:
  -h, --help       show this help message and exit
  --nombre NOMBRE  Nombre de nombres aléatoires à générer.
  --json           Rend un seul objet JSON sur stdout.
  --racine RACINE  Surcharge la racine et l'insère en tête de sys.path.

Exemple d'appel réel : python generateur_aleatoire.py --nombre 5 --json
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

