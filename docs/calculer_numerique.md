# calculer_numerique

> OMISE

## Comment s'en servir

```
d'appel réel : python calculer_numerique.py '2+2'
```

Sous-commande `expression` : L'expression à évaluer.

## Toutes les options

```
usage: calculer_numerique.py [-h] [--json] [--racine RACINE] expression

Calcule le résultat numérique d'une expression.

positional arguments:
  expression       L'expression à évaluer

options:
  -h, --help       show this help message and exit
  --json           Rend un seul objet JSON sur stdout
  --racine RACINE  Surcharge la racine et l'insère en tête de sys.path

Exemple d'appel réel : python calculer_numerique.py '2+2'
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

mpmath, numpy, sympy — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

