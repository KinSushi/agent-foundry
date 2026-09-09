# parser_argument_ligne

> OMISE

## Comment s'en servir

```
python outils/parser_argument_ligne.py --cible exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: parser_argument_ligne.py [-h] --cible CIBLE [--racine RACINE] [--json]

Analyse statiquement les déclarations d'arguments CLI dans un fichier Python.

options:
  -h, --help       show this help message and exit
  --cible CIBLE    Chemin vers le fichier Python cible à analyser
  --racine RACINE  Dossier racine à insérer en tête de sys.path (défaut:
                   D:\Puissance_60+_bibliothèques_python\outils)
  --json           Sortie au format JSON unique sur stdout, rien d'autre

Exemple réel: python parser_argument_ligne.py --cible mon_script.py --json
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

click, rich, typer — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

