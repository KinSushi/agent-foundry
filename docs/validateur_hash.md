# validateur_hash

> OMISE

## Comment s'en servir

```
d'appel réel :
```

Sous-commande `cible` : Chemin du fichier à vérifier.

## Toutes les options

```
usage: validateur_hash.py [-h] [--algo ALGO] --hash ATTENDU [--racine RACINE]
                          [--json]
                          cible

Vérifie qu'un fichier correspond à un hash donné.

positional arguments:
  cible            Chemin du fichier à vérifier.

options:
  -h, --help       show this help message and exit
  --algo ALGO      Algorithme de hash (nom reconnu par hashlib).
  --hash ATTENDU   Digest hexadécimal attendu.
  --racine RACINE  Chemin racine à préfixer à sys.path (défaut: répertoire du script).
  --json           Produit une sortie JSON unique sur stdout.

Exemple d'appel réel :
  python validateur_hash.py --algo sha256 --hash 031edd7d41651593c5fe5c006fa5752b37fddff7bc4e843aa6af0c950f4b9406 chemin/vers/fichier.txt
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
