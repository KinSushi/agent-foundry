# verifier_docstring

> OMISE

## Comment s'en servir

```
python -m outils.verifier_docstring mon_module.py --json
```

Sous-commande `module` : Chemin (relatif à la racine) du fichier .py à analyser.

## Toutes les options

```
usage: verifier_docstring.py [-h] [--racine RACINE] [--json] module

Vérifie la conformité des docstrings d'un module Python.

positional arguments:
  module           Chemin (relatif à la racine) du fichier .py à analyser.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Chemin racine du projet (défaut : répertoire contenant cet
                   outil).
  --json           Produit une sortie JSON unique sur stdout.

Exemple : python -m outils.verifier_docstring mon_module.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

n'est pas comparable ; *args et **kwargs échappent au croisement

## Contre‑exemples

les confondre accuserait toute docstring sans types

## Ce qu'il lui faut

docstring_parser — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
