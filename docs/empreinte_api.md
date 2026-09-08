# empreinte_api

> OMISE

## Comment s'en servir

```
python empreinte_api.py monmodule_v1.py monmodule_v2.py --json
```

Sous-commande `modules` : Nom ou chemin du module à analyser (un seul → empreinte, deux → comparaison).

## Toutes les options

```
usage: empreinte_api.py [options] module1 [module2]

Analyse d’empreinte d’API et comparaison entre deux versions.

positional arguments:
  modules          Nom ou chemin du module à analyser (un seul → empreinte, deux → comparaison).

options:
  -h, --help       Affiche cette aide et quitte
  --racine RACINE  Chemin racine à utiliser pour les chemins relatifs.
  --json           Émettre la sortie au format JSON unique.
  --docstring      Vérifier la cohérence des docstrings (requiert docstring_parser).

Exemple : python empreinte_api.py monmodule_v1.py monmodule_v2.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

isfunction|isclass rate les formes spéciales et les fonctions C ; un __getattr__ dynamique n'est pas énumérable

## Contre‑exemples

qu'aucune API n'ait changé — faux positif qui décrédibilise

## Ce qu'il lui faut

docstring_parser — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
