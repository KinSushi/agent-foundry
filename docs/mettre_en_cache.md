# mettre_en_cache

> Comment éviter de recalculer ce résultat coûteux ?

## Comment s'en servir

```
python outils/mettre_en_cache.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: mettre_en_cache.py [-h] [--racine RACINE] [--json] cible

Analyse un source Python pour détecter l'absence de mise en cache des
résultats de fonctions.

positional arguments:
  cible            Chemin du fichier source Python à analyser

options:
  -h, --help       show this help message and exit
  --racine RACINE  Surcharge la racine et l'insère en tête de sys.path
  --json           Rend un seul objet JSON sur stdout

Exemple : python mettre_en_cache.py mon_script.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les caches personnalisés non standard (ex: dictionnaire global). N'évalue pas le coût réel de la fonction.

## Contre‑exemples

Une fonction simple sans arguments ou avec effets de bord ne nécessite pas de cache, mais sera signalée.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

