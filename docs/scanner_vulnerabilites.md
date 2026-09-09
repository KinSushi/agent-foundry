# scanner_vulnerabilites

> OMISE

## Comment s'en servir

```
python scanner_vulnerabilites.py mon_fichier.py --racine /mon/projet
```

Sous-commande `cible` : Chemin vers le fichier Python à analyser.

## Toutes les options

```
usage: scanner_vulnerabilites.py [-h] [--racine RACINE] [--json] cible

Détecte des patterns dangereux dans du code Python.

positional arguments:
  cible            Chemin vers le fichier Python à analyser.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine pour les imports relatifs. Par défaut :
                   répertoire du script.
  --json           Produit une sortie JSON au lieu d'un rapport humain.

Exemple : python scanner_vulnerabilites.py mon_fichier.py --racine /mon/projet
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

les dépendances tierces. Peut produire des faux positifs/négatifs.

## Contre‑exemples

dans un contexte spécifique. Les paramètres inexistants comme all_threads=True dans faulthandler.dump_traceback_later() sont détectés.

## Ce qu'il lui faut

RestrictedPython — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

