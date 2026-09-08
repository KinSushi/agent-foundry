# indexeur_fichiers

> OMISE

## Comment s'en servir

```
python outils/indexeur_fichiers.py --racine . --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: indexeur_fichiers.py [-h] [--racine RACINE] [--json]

Indexe les fichiers binaires à la recherche de motifs connus.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire ou fichier à examiner. Par défaut, le répertoire du script.
  --json           Sortie JSON unique sur stdout (résultat machine‑lisible).

Exemple d'appel : python indexeur_fichiers.py --racine ./data --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

interne n'est effectuée. L'outil fonctionne en mode dégradé si les bibliothèques tierces sont absentes.

## Contre‑exemples

d'un flux compressé sera néanmoins signalé comme une occurrence.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
