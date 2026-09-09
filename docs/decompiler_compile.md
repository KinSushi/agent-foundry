# decompiler_compile

> Peut-on inspecter/modifier du bytecode Python sans outils natifs ?

## Comment s'en servir

```
python outils/decompiler_compile.py optimiser exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: decompiler_compile.py [-h]
                             {decompiler,transformer,optimiser,comparer} ...

Outil d’inspection et de transformation du bytecode Python.

positional arguments:
  {decompiler,transformer,optimiser,comparer}
    decompiler          Décompile un fichier .pyc en source lisible.
    transformer         Transforme le bytecode d’un fichier .py selon des
                        remplacements.
    optimiser           Optimise le bytecode d’un fichier .py.
    comparer            Compare deux fichiers .py et affiche les différences
                        d’instructions.

options:
  -h, --help            show this help message and exit
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Aucune garantie sur la sémantique du code reconstitué (ex: noms de variables perdus).

## Contre‑exemples

Un fichier `.pyc` généré avec une version de Python différente (ex: 3.8 vs 3.14).

## Ce qu'il lui faut

depyf — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

