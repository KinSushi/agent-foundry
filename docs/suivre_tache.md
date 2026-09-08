# suivre_tache

> laquelle de mes N tâches parallèles a produit cette ligne ?

## Comment s'en servir

```
python outils/suivre_tache.py relire ./journal.json --tache agent-42 --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: suivre_tache.py [-h] [--racine RACINE] [--json] {relire} ...

Outil de suivi d'identités de tâches concurrentes.

positional arguments:
  {relire}
    relire         Reconstitue le fil d'une tâche depuis un journal
                   JSON‑lines.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Chemin racine du projet (défaut : répertoire du script).
  --json           Émettre le résultat au format JSON unique.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

ne traverse PAS un sous‑processus ; un %(tache)s sans filtre lève KeyError

## Contre‑exemples

threading.local() rend la même valeur aux 4 tâches asyncio

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
