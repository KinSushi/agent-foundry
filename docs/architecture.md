# architecture

> casse le plus de choses ?

## Comment s'en servir

```
python outils/architecture.py carte --racine ./a --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: architecture.py [-h] [--racine RACINE] [--json]
                       {carte,critiques,cycles,all,lire} ...

Analyse d’un dépôt Python : graphe d’imports, centralité, cycles, ordre de
lecture.

positional arguments:
  {carte,critiques,cycles,all,lire}
    carte               Construire le graphe d’imports et afficher ses
                        métriques.
    critiques           Lister les modules les plus critiques (centralité).
    cycles              Détecter les cycles avec graphlib et networkx.
    all                 Toutes les informations (critiques, cycles, …).
    lire                Proposer l’ordre de lecture d’un module.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine du dépôt à analyser (défaut : répertoire
                        du script).
  --json                Émettre la sortie au format JSON unique sur stdout.

Exemple : python -m outils.architecture carte --racine ./mon_projet
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

(level > 0) sont résolus de façon simple, ce qui peut manquer d’arêtes internes à un paquet CONTRE‑EXEMPLES la mesure initiale ne prenait que level == 0 et sous‑comptait les arêtes — le défaut était dans l’instrument, pas dans networkx

## Ce qu'il lui faut

networkx — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
