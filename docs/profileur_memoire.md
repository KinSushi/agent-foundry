# profileur_memoire

> Quels objets consomment le plus de mémoire ?

## Comment s'en servir

```
d'appel : python profileur_memoire.py --racine . --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: profileur_memoire.py [-h] [--racine RACINE] [--json]

Profileur mémoire : identifie les objets consommant le plus de mémoire.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine à ajouter en tête de sys.path (override du répertoire du script).
  --json           Sortie JSON unique sur stdout (sinon affichage lisible).

Exemple d'appel : python profileur_memoire.py --racine . --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne compte pas la mémoire référencée indirectement (référents).
- Ne distingue pas les objets partagés ou les structures internes du gc.
- L'estimation peut sous‑évaluer la taille réelle pour certains types d'extension.

## Contre‑exemples

- Un objet très petit qui référence un grand tableau (ex. bytearray) apparaîtra
comme peu consommateur alors que la mémoire réelle est dominée par le référent.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

