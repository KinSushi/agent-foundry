# ecouter_evenements

> Quels événements ce flux émet‑il en continu ?

## Comment s'en servir

```
python outils/ecouter_evenements.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: ecouter_evenements.py [-h] [--timeout TIMEOUT] [--json]
                             [--racine RACINE]
                             url

Écoute les événements d’un flux SSE ou WebSocket.

positional arguments:
  url                URL du flux ou chemin d’un fichier texte.

options:
  -h, --help         show this help message and exit
  --timeout TIMEOUT  Timeout en secondes (défaut : 10)
  --json             Sortie au format JSON
  --racine RACINE    Répertoire racine à ajouter au PYTHONPATH

Exemple : python ecouter_evenements.py http://exemple.com/flux --timeout 15
--json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les événements émis avant la connexion. Ne gère pas les flux chiffrés sans bibliothèques tierces.

## Contre‑exemples

Un flux qui n’émet que des keep‑alive sans données utiles.

## Ce qu'il lui faut

httpx, httpx_sse, websockets — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

