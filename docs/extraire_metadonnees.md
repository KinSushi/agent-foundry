# extraire_metadonnees

> OMISE

## Comment s'en servir

```
extraire_metadonnees.py --json mon_fichier.pdf
```

Sous-commande `fichier` : Chemin vers le fichier à analyser.

## Toutes les options

```
warning: The `fitz` API is deprecated and will be removed in future. Use `import pymupdf` instead.
usage: extraire_metadonnees.py [-h] [--racine RACINE] [--json] fichier

Extrait les métadonnées d’un fichier.

positional arguments:
  fichier          Chemin vers le fichier à analyser

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine pour les imports relatifs
  --json           Sortie au format JSON (objet unique)

Exemple : extraire_metadonnees.py --json mon_fichier.pdf
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

propriétaires non documentés. CONTRE‑EXEMPLES: PDF sans XMP, image sans EXIF.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
