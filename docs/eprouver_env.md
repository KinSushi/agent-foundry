# eprouver_env

> Ce paquet marche‑t‑il ICI, et si non, pourquoi ?  L’outil doit déterminer si les binaires natifs d’une distribution peuvent être chargés par le chargeur du système, sans importer le module Python complet.

## Comment s'en servir

```
python outils/eprouver_env.py ./a ./b --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: eprouver_env.py [-h] [--json] [--racine RACINE] [--seuil-ms SEUIL_MS]
                       [--detail NOM] [--verbeux]

Dit ce que cet interprète peut réellement charger.

options:
  -h, --help           show this help message and exit
  --json               rend un objet JSON sur stdout, et rien d'autre
  --racine RACINE      racine de travail (défaut : le dossier de ce script)
  --seuil-ms SEUIL_MS  signale un binaire mettant plus de N ms à charger
                       (défaut 1000)
  --detail NOM         n'éprouve qu'une seule distribution
  --verbeux            affiche les messages d'erreur complets

Exemple : python eprouver_env.py --seuil-ms 500 --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

« charge » ne veut pas dire « travaille » : un binaire qui se charge peut tout de même échouer à l’exécution d’une fonction.  L’outil ne détecte pas les erreurs d’exécution, seulement les échecs de chargement. CONTRE‑EXEMPLES Si ``_ctypes`` est bloqué par une politique système, l’outil ne peut rien conclure et doit l’indiquer explicitement, jamais retourner « tout va bien ».

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

