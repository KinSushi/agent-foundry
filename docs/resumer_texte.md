# resumer_texte

> OMISE

## Comment s'en servir

```
d’appel réel :
```

Sous-commande `cible` : Chemin vers le fichier texte à résumer.

## Toutes les options

```
usage: resumer_texte.py [-h] [--json] [--racine RACINE] cible

Outil de résumé de texte. Lit un fichier texte et renvoie son résumé.
Exemple d’appel réel :
  python resumer_texte.py mon_fichier.txt --json

positional arguments:
  cible            Chemin vers le fichier texte à résumer.

options:
  -h, --help       show this help message and exit
  --json           Émettre la sortie au format JSON unique sur stdout.
  --racine RACINE  Chemin racine à préfixer à sys.path (remplace la valeur par défaut).
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

transformers — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

