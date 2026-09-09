# paralleliser

> OMISE

## Comment s'en servir

```
python outils/paralleliser.py --demo --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: paralleliser.py [-h] [--racine RACINE] [--json] [--seuil SEUIL]
                       [--taches TACHES] [--iterations ITERATIONS] [--demo]

Parallélise une fonction CPU-bound avec un pool d'interpréteurs.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine du projet (défaut : répertoire du script).
  --json                Émettre la sortie au format JSON unique.
  --seuil SEUIL         Seuil de gain (exemple : 1.5 → 1,5×) pour accepter la parallélisation.
  --taches TACHES       Nombre d'interpréteurs dans le pool (défaut : 4).
  --iterations ITERATIONS
                        Nombre d'itérations de la tâche d'exemple (défaut : 3 000 000).
  --demo                Utiliser la fonction d'exemple intégrée.

Exemple :
  python -m outils.paralleliser --taches 4 --iterations 3000000 --seuil 1.5
  python -m outils.paralleliser --demo --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

La création d'un interpréteur coûte ≈ 9,7 ms → un pool est obligatoire. Les objets de classe personnalisée lèvent ``NotShareableError``.

## Contre‑exemples

à « aucun gain ». Avec ``call_in_thread`` le gain est 2,5×-3,5×.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

