# guetter_fichiers

> Le fichier a‑t‑il réellement changé depuis le dernier relevé ?

## Comment s'en servir

```
python outils/guetter_fichiers.py event exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: guetter_fichiers.py [-h] [--racine RACINE] [--json]
                           {run,async,cli,filter,event} ...

Surveillance de fichiers en temps réel.

positional arguments:
  {run,async,cli,filter,event}
    run                 exécute une commande à chaque modification
    async               boucle asynchrone affichant les changements
    cli                 affiche les événements en temps réel
    filter              surveille avec une profondeur maximale
    event               retourne le type d'événement et son horodatage

options:
  -h, --help            show this help message and exit
  --racine RACINE       racine du projet (défaut: répertoire de l'outil)
  --json                sortie au format JSON
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les changements très rapides (< 10 ms) peuvent être coalescés. CONTRE‑EXEMPLE Un fichier créé puis supprimé en 5 ms n’est jamais signalé.

## Ce qu'il lui faut

watchfiles — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

