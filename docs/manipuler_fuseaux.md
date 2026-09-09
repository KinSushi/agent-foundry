# manipuler_fuseaux

> Quelle heure est‑il à cet endroit ?

## Comment s'en servir

```
python outils/manipuler_fuseaux.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: manipuler_fuseaux.py [-h] [--json] [--racine RACINE] [lieu]

Affiche l'heure actuelle pour un fuseau horaire donné.

positional arguments:
  lieu             Nom IANA du fuseau horaire (ex. Europe/Paris). Si omis, le
                   fuseau local est utilisé.

options:
  -h, --help       show this help message and exit
  --json           Sortie au format JSON unique sur stdout.
  --racine RACINE  Chemin racine à utiliser au lieu du répertoire du script.

Exemple : python manipuler_fuseaux.py Europe/Paris
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

L’outil ne tient pas compte de l’éventuelle inexactitude de l’horloge système ni des sauts de seconde intercalaires.

## Contre‑exemples

Si le fuseau demandé n’existe pas dans la base IANA, l’outil retourne une erreur.

## Ce qu'il lui faut

tzlocal — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

