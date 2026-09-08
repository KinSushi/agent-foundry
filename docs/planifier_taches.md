# planifier_taches

> OMISE

## Comment s'en servir

```
python outils/planifier_taches.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: planifier_taches.py [-h] [--json] [--racine RACINE] fichier

Analyse un fichier Python pour déterminer quand une tâche doit s'exécuter.

positional arguments:
  fichier          Chemin vers le fichier Python à analyser

options:
  -h, --help       show this help message and exit
  --json           Sortie au format JSON sur stdout
  --racine RACINE  Répertoire racine pour les imports relatifs

Exemple : planifier_taches.py mon_script.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

CONTRE‑EXEMPLES Un appel à apscheduler.add_job() avec un trigger non cron (date, interval).

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
