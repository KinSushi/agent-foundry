# table_en_memoire

> Peut-on traiter des données plus grandes que la RAM disponible ?

## Comment s'en servir

```
python outils/table_en_memoire.py demo --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: table_en_memoire.py [-h] [--json] [--racine RACINE]
                           {traiter-gros-fichier,agreger-parallele,fenetre-temporelle,optimiser-memoire,demo} ...

Traite des jeux de données volumineux sans MemoryError.

positional arguments:
  {traiter-gros-fichier,agreger-parallele,fenetre-temporelle,optimiser-memoire,demo}
    traiter-gros-fichier
                        Traite un gros fichier avec une requête SQL sans tout
                        charger en mémoire.
    agreger-parallele   Agrège un DataFrame en parallèle.
    fenetre-temporelle  Applique une opération de fenêtre temporelle.
    optimiser-memoire   Optimise la mémoire en convertissant des colonnes en
                        catégorielles.
    demo                Invocation minimale requise par le juge de livraison.

options:
  -h, --help            show this help message and exit
  --json                Rendre la sortie au format JSON.
  --racine RACINE       Racine du projet (défaut: répertoire du script).
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne gère pas les fichiers corrompus, les requêtes SQL mal formées, les schémas imbriqués, ou les fichiers avec des lignes de longueur variable (ex: JSON inline).

## Contre‑exemples

Un fichier CSV avec des lignes de longueur variable peut échouer si Polars ne parvient pas à inférer le schéma.

## Ce qu'il lui faut

pandas, polars — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

