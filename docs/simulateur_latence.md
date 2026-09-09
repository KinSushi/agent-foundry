# simulateur_latence

> OMISE

## Comment s'en servir

```
python outils/simulateur_latence.py --code "import time; time.sleep(0.001)" --latences 50 --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: simulateur_latence.py [-h] --code CODE
                             --latences LATENCES [LATENCES ...]
                             [--nombre-executions NOMBRE_EXECUTIONS]
                             [--setup SETUP] [--avec-httpcore]
                             [--racine RACINE] [--json]

Simule l'impact de la latence réseau sur un code Python.

options:
  -h, --help            show this help message and exit
  --code CODE           Code Python à analyser (doit contenir un appel
                        réseau).
  --latences LATENCES [LATENCES ...]
                        Latences réseau à simuler en millisecondes.
  --nombre-executions NOMBRE_EXECUTIONS
                        Nombre d'exécutions pour chaque latence (défaut : 10).
  --setup SETUP         Code d'initialisation à exécuter avant le code
                        principal (défaut : 'pass').
  --avec-httpcore       Utilise httpcore pour simuler un vrai appel réseau (si
                        disponible).
  --racine RACINE       Répertoire racine pour les imports relatifs.
  --json                Sortie au format JSON.

Exemple : simulateur_latence.py --code 'requests.get("https://example.com")'
--latences 50 100 200
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

httpcore — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

