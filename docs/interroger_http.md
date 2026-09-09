# interroger_http

> Interroger un service HTTP et déterminer sa réponse ainsi que les modalités de négociation.

## Comment s'en servir

```
python outils/interroger_http.py https://example.com --json --reseau
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: interroger_http.py [-h] [--json] [--racine RACINE] [--reseau] cible

Interroge un service HTTP et rapporte sa réponse.

positional arguments:
  cible            URL du service HTTP à interroger (ex. https://example.com)

options:
  -h, --help       show this help message and exit
  --json           Émettre la sortie au format JSON unique sur stdout.
  --racine RACINE  Chemin racine à préfixer à sys.path avant importation
                   éventuelle.
  --reseau         Autoriser l'accès réseau (désactivé par défaut).

Exemple d’appel réel : python interroger_http.py https://example.com --json
--reseau
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Pas de support natif HTTP/2 ou de retries avancés sans bibliothèques tierces.

## Contre‑exemples

Un service qui nécessite une authentification complexe ou un protocole propriétaire ne sera pas géré.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

