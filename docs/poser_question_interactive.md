# poser_question_interactive

> OMISE

## Comment s'en servir

```
python outils/poser_question_interactive.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: poser_question_interactive.py [-h] [--racine RACINE] [--json]

Pose la question « Comment demander une décision humaine ? » à un humain.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Chemin racine à préfixer à sys.path (défaut : répertoire du
                   script).
  --json           Émettre la réponse sous forme d’un unique objet JSON sur
                   stdout.

Exemple : python poser_question_interactive.py --json
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

InquirerPy, prompt_toolkit — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
