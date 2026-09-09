# parser_json_partiel

> Que contient ce flux JSON incomplet ?

## Comment s'en servir

```
python outils/parser_json_partiel.py --texte '{"a": 1, "b": [2, 3' --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: parser_json_partiel.py [-h] [--texte TEXTE] [--racine RACINE] [--json]
                              [cible]

Analyse un flux JSON potentiellement incomplet et extrait ce qu'il contient.

positional arguments:
  cible            Chemin du fichier JSON ou chaîne de caractères JSON

options:
  -h, --help       show this help message and exit
  --texte TEXTE    Chaîne JSON à analyser directement
  --racine RACINE  Racine pour résoudre les chemins relatifs et insertion dans
                   sys.path
  --json           Rend un seul objet JSON sur stdout

Exemple : python parser_json_partiel.py --texte '{"a": 1, "b": [2, 3' --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

La réparation naïve ne gère pas les chaînes multilignes ni les échappements complexes.

## Contre‑exemples

Un flux tronqué au milieu d'une clé de dictionnaire peut être mal réparé.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

