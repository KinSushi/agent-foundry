# afficher_riche

> Comment présenter ce résultat de manière lisible ?

## Comment s'en servir

```
d'appel :
```

Sous-commande `entree` : Fichier à lire (stdin si omis).

## Toutes les options

```
usage: afficher_riche.py [-h] [--racine RACINE] [--json] [entree]

Affiche un texte de manière lisible, avec enrichissement éventuel via 'rich'.

positional arguments:
  entree           Fichier à lire (stdin si omis)

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine à ajouter en tête de sys.path
  --json           Sortie unique en JSON sur stdout

Exemple d'appel :
  afficher_riche.py mon_fichier.txt
  cat mon_fichier.txt | afficher_riche.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Le largeur de référence est fixée à 80 caractères ; les listes d'examen sont limitées à 200 éléments.

## Contre‑exemples

Une entrée contenant uniquement des lignes de moins de 80 caractères ne déclenche aucun défaut.

## Ce qu'il lui faut

rich — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
