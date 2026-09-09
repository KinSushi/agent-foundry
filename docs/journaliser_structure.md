# journaliser_structure

> Comment enregistrer les événements de manière exploitable ?

## Comment s'en servir

```
python outils/journaliser_structure.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: journaliser_structure.py [-h] [--racine RACINE] [--json] cible

Analyse comment le code enregistre les événements de manière exploitable.

positional arguments:
  cible            Fichier ou répertoire à analyser

options:
  -h, --help       show this help message and exit
  --racine RACINE  Surcharge la racine et l'insère dans sys.path
  --json           Sortie JSON structurée

Exemple: python journaliser_structure.py ./mon_script.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

L'outil ne voit que le code source, pas le comportement à l'exécution ni la configuration dynamique des handlers.

## Contre‑exemples

Un script qui écrit dans un fichier binaire ou qui utilise sys.stdout.write directement sans passer par logging.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

