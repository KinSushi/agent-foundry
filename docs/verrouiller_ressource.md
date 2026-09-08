# verrouiller_ressource

> OMISE

## Comment s'en servir

```
python outils/verrouiller_ressource.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: verrouiller_ressource.py [-h] [--racine RACINE] [--json] ressource

Vérifie si une ressource (fichier) est déjà utilisée par un autre processus.

positional arguments:
  ressource        Chemin vers la ressource (fichier) à vérifier.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Override la racine du projet (utilisée pour ajouter au
                   sys.path si l'outil importe une cible).
  --json           Sortie au format JSON au lieu du texte lisible par un
                   humain.

Exemple : python verrouiller_ressource.py mon_fichier.txt
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

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
