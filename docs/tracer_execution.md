# tracer_execution

> Quel est le chemin d’exécution de cette requête ?

## Comment s'en servir

```
python tracer_execution.py mon_script.py --json
```

Sous-commande `cible` : Chemin du script Python à analyser.

## Toutes les options

```
usage: tracer_execution.py [-h] [--racine RACINE] [--json] cible

Tracer l’exécution d’un script Python et restituer le chemin d’appel.

positional arguments:
  cible            Chemin du script Python à analyser.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire à préfixer dans sys.path (défaut : répertoire du
                   script).
  --json           Émettre le résultat sous forme d’un unique objet JSON sur
                   stdout.

Exemple : python tracer_execution.py mon_script.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les appels exécutés dans du code C (extensions, modules intégrés) ne sont pas visibles ; seules les fonctions Python sont tracées.

## Contre‑exemples

Un script qui utilise uniquement du code C (ex. : module `math`) ne produira aucun élément dans le chemin d’exécution.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

