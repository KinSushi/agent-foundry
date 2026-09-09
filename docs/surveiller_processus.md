# surveiller_processus

> Quels processus tournent, et comment les superviser ?

## Comment s'en servir

```
d'appel réel :
```

Sous-commande `processus` : Noms des processus à surveiller (ex. nginx, python).

## Toutes les options

```
usage: surveiller_processus.py [-h] [--cmd COMMANDE] [--json]
                               [--racine RACINE]
                               [processus ...]

Supervise les processus indiqués et, le cas échéant, les redémarre.
Exemple d'appel réel :
  python surveiller_processus.py nginx mysqld --cmd '/usr/sbin/nginx' --json

positional arguments:
  processus        Noms des processus à surveiller (ex. nginx, python).

options:
  -h, --help       show this help message and exit
  --cmd COMMANDE   Commande à exécuter pour redémarrer les processus absents.
  --json           Produit la sortie au format JSON unique sur stdout.
  --racine RACINE  Chemin racine à insérer en tête de sys.path (défaut : répertoire du script).
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Redémarrage limité à la commande fournie via --cmd, sinon aucun redémarrage.

## Contre‑exemples

Processus système protégés ne peuvent être redémarrés.

## Ce qu'il lui faut

psutil — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

