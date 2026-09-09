# simulateur_erreurs

> OMISE

## Comment s'en servir

```
simulateur_erreurs.py --code 'with open("test.txt", "w") as f:
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: simulateur_erreurs.py [-h] --code CODE [--erreur {ENOSPC}] [--json]
                             [--racine RACINE]

Simule des erreurs système et analyse la réaction d'un code Python.

options:
  -h, --help         show this help message and exit
  --code CODE        Code Python à analyser (entre guillemets).
  --erreur {ENOSPC}  Erreur système à simuler (ex: ENOSPC pour disque plein).
  --json             Produit une sortie JSON sur stdout.
  --racine RACINE    Répertoire racine pour l'exécution (défaut: répertoire du
                     script).

Exemple: simulateur_erreurs.py --code 'with open("test.txt", "w") as f:
f.write("test")' --erreur ENOSPC --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

La simulation dépend des permissions et de l'environnement d'exécution.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

