# compresseur_rapide

> Comment compresser/décompresser sans zlib ?

## Comment s'en servir

```
python outils/compresseur_rapide.py compress exemple.py ./resultat --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: compresseur_rapide.py [-h] [--racine RACINE] [--json]
                             {compress,decompress} ...

Compresse ou décompresse un fichier avec bz2 ou lzma, optionnellement en base64.

positional arguments:
  {compress,decompress}
    compress            Compresser un fichier.
    decompress          Décompresser un fichier.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine à préfixer à sys.path (par défaut le répertoire du script).
  --json                Produit une sortie JSON unique sur stdout.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Pas de support zstd, pas de compression personnalisée au‑delà de bz2/lzma. Si pybase64 est absent, l'option --base64 est ignorée.

## Contre‑exemples

Un fichier inexistant ou non lisible entraîne une erreur claire et un code de sortie non nul, jamais d'exception non interceptée.

## Ce qu'il lui faut

pybase64 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
