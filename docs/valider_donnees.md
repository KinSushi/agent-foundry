# valider_donnees

> OMISE

## Comment s'en servir

```
python outils/valider_donnees.py '{"x": 1}' --schema 'dict' --module valide --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: valider_donnees.py [-h] --schema SCHEMA --module MODULE
                          [--racine RACINE] [--json] [--pydantic]
                          donnee

Valide une donnée JSON par rapport à un schéma Python.

positional arguments:
  donnee           Donnée JSON à valider.

options:
  -h, --help       show this help message and exit
  --schema SCHEMA  Nom du schéma (classe, dataclass, etc.) à utiliser pour la
                   validation.
  --module MODULE  Module Python contenant le schéma.
  --racine RACINE  Répertoire racine pour l'import du module. Par défaut:
                   répertoire du script.
  --json           Sortie au format JSON.
  --pydantic       Utiliser pydantic pour la validation si disponible.

Exemple: valider_donnees.py '{"x": 1, "y": 2}' --schema 'Point' --module
exemple.schemas
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne gère pas les références circulaires dans les données.

## Ce qu'il lui faut

pydantic — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

