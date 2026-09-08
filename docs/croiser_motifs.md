# croiser_motifs

> OMISE

## Comment s'en servir

```
python outils/croiser_motifs.py croiser ./motifs.txt --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: croiser_motifs.py [-h] [--racine RACINE] [--json]
                         {croiser,couvre,ordre} ...

Croise des motifs regex pour détecter collisions et règles mortes.

positional arguments:
  {croiser,couvre,ordre}
    croiser             Croise une liste de motifs.
    couvre              Vérifie si un motif couvre un autre.
    ordre               Signale les motifs morts dans une liste ordonnée.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du projet (défaut : répertoire de l'outil)
  --json                Sortie JSON (un seul objet)

Exemple : croiser_motifs.py croiser motifs.txt --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

motifs ; le témoin est UNE chaîne, pas toutes

## Contre‑exemples

l'est pas — d'où INDÉCIDABLE, et un code de sortie distinct

## Ce qu'il lui faut

interegular, lark — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
