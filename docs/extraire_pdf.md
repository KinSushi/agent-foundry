# extraire_pdf

> OMISE

## Comment s'en servir

```
python extraire_pdf.py document.pdf --json
```

Sous-commande `cible` : Chemin du fichier PDF a analyser.

## Toutes les options

```
usage: extraire_pdf.py [-h] [--json] [--racine RACINE] cible

Extrait le texte, la structure et les images d'un PDF.

positional arguments:
  cible            Chemin du fichier PDF a analyser

options:
  -h, --help       show this help message and exit
  --json           Rend un seul objet JSON sur stdout
  --racine RACINE  Surcharge la racine pour la resolution des chemins

Exemple : python extraire_pdf.py document.pdf --json
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

pymupdf — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
