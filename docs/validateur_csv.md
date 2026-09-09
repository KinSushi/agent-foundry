# validateur_csv

> Ce CSV est-il valide selon les règles définies ?

## Comment s'en servir

```
python outils/validateur_csv.py exemple.py --regles ./regles.json --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: validateur_csv.py [-h] --regles REGLES [--racine RACINE] [--json]
                         fichier

Valide un fichier CSV selon des règles complexes.

positional arguments:
  fichier          Chemin vers le fichier CSV à valider.

options:
  -h, --help       show this help message and exit
  --regles REGLES  Chemin vers le fichier JSON définissant les règles de
                   validation.
  --racine RACINE  Répertoire racine pour les chemins relatifs (défaut:
                   répertoire du script).
  --json           Sortie au format JSON sur stdout.

Exemple: python validateur_csv.py donnees.csv --regles regles.json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne gère pas les fichiers CSV avec des champs multi-lignes complexes.
- Les règles de validation doivent être exprimables via regex ou types simples.
- La détection automatique de dialecte CSV peut échouer sur des formats exotiques.
- Fichiers limités à 100 Mo pour des raisons de performance.

## Contre‑exemples

- Un CSV avec des champs contenant des sauts de ligne non échappés.
- Un CSV dont les règles nécessitent une validation contextuelle (ex : somme de colonnes).
- Un CSV avec un nombre inégal de colonnes par ligne.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

