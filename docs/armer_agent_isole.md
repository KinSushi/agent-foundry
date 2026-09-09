# armer_agent_isole

> L'outil doit déterminer si un agent IA lancé en isolation possède réellement toute la matière du dépôt ou s'il répond de mémoire.

## Comment s'en servir

```
python outils/armer_agent_isole.py diagnostiquer exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: armer_agent_isole.py [-h] [--racine RACINE] [--json]
                            {diagnostiquer,armer,verifier} ...

Outil d’audit d’un dépôt Git en worktree isolé.

positional arguments:
  {diagnostiquer,armer,verifier}
    diagnostiquer       Comparer le worktree au dépôt.
    armer               Copier les fichiers manquants dans le worktree.
    verifier            Vérifier l’état après armement.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine du dépôt (défaut : répertoire du
                        script).
  --json                Sortie JSON unique.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Les fichiers non lisibles sont marqués NON MESURABLE et exclus du comptage.
- Les répertoires vides ne sont pas considérés comme manquants.
- Le volume copié est limité par `--plafond-mo`.

## Contre‑exemples

"un agent lance en worktree isole a conclu sur un tiers de la matiere sans le savoir : doc/ etait dans .gitignore, et rien ne le signalait."

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

