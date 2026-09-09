# sceller

> OMISE

## Comment s'en servir

```
python -m outils.sceller sceller doc/stdlib_314/ --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: sceller [-h] [--racine RACINE] [--json] [--accepter-partiel]
               {sceller,verifier,relire} ...

Scelle, vérifie ou relit un arbre de fichiers.

positional arguments:
  {sceller,verifier,relire}
    sceller             Produit un sceau JSON de l’arbre.
    verifier            Vérifie un sceau contre l’arbre actuel.
    relire              Relit un fragment de fichier et compare.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine de l’arbre à analyser (défaut :
                        répertoire de l’outil).
  --json                Produit la sortie au format JSON uniquement.
  --accepter-partiel    Accepte les résultats même si des erreurs de lecture
                        sont survenues.

Exemple : python -m outils.sceller sceller doc/stdlib_314/ --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

rien, il constate ; il repose sur _hashlib.pyd, dont l’immunité est empirique

## Contre‑exemples

et ne détecte plus rien

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

