# proxy_transparent

> OMISE

## Comment s'en servir

```
python outils/proxy_transparent.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: proxy_transparent.py [-h] --cible CIBLE [--racine RACINE] [--json]

Outil d'analyse statique pour détecter l'interception ou modification de
requêtes HTTP.

options:
  -h, --help       show this help message and exit
  --cible CIBLE    Fichier ou répertoire Python à analyser
  --racine RACINE  Racine du projet (surcharge la racine par défaut)
  --json           Rend un seul objet JSON sur stdout

Exemple: python proxy_transparent.py --cible ./mon_projet --json
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

httpcore, httpx — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
