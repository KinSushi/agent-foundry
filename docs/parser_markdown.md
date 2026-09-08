# parser_markdown

> OMISE

## Comment s'en servir

```
python outils/parser_markdown.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: parser_markdown.py [-h] [--json] [--racine RACINE] fichier

Analyser la structure d'un document Markdown.

positional arguments:
  fichier          Chemin vers le fichier Markdown à analyser.

options:
  -h, --help       show this help message and exit
  --json           Sortie au format JSON sur stdout.
  --racine RACINE  Répertoire racine pour les imports relatifs (défaut :
                   répertoire du script).

Exemple : python parser_markdown.py document.md --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne valide pas la syntaxe Markdown, seulement extrait les éléments visibles.

## Ce qu'il lui faut

markdown_it — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
