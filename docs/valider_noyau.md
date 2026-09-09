# valider_noyau

> Le flux ou le document respecte-t-il le schéma fourni ?

## Comment s'en servir

```
python outils/valider_noyau.py compat --old exemple.py --new exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: valider_noyau.py [-h] [--json] [--racine RACINE]
                        {stream,transform,compat} ...

Valide un flux JSON ou un document contre un schéma Pydantic.

positional arguments:
  {stream,transform,compat}
    stream              Valide un flux JSON en streaming
    transform           Transforme un document JSON selon un schéma
    compat              Vérifie la compatibilité entre deux schémas

options:
  -h, --help            show this help message and exit
  --json                Sortie JSON uniquement
  --racine RACINE       Racine pour les chemins relatifs (défaut: répertoire
                        de l'outil)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les flux binaires non‑JSON ne sont pas supportés ; les schémas très grands peuvent dépasser la mémoire.

## Contre‑exemples

Le schéma {"type": "integer"} appliqué à la chaîne "123abc" est rejeté correctement.

## Ce qu'il lui faut

pydantic_core — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

