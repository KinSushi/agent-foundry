# retenir_memoire

> OMISE

## Comment s'en servir

```
python outils/retenir_memoire.py retenteurs "[i for i in range(10)]"
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: retenir_memoire.py [-h] [--racine RACINE] [--json]
                          {retenteurs,cycles,couter} ...

Outil de diagnostic mémoire : nomme les retenteurs d'objets, inspecte les
cycles et mesure le coût.

positional arguments:
  {retenteurs,cycles,couter}
    retenteurs          Nomme les retenteurs d'un objet évalué par
                        l'expression
    cycles              Inspecte les cycles collectés par gc
    couter              Mesure le coût mémoire et temporel d'un scénario

options:
  -h, --help            show this help message and exit
  --racine RACINE       Surcharge la racine du projet
  --json                Renvoie un objet JSON sur stdout

Exemple: python outils/retenir_memoire.py retenteurs "[i for i in range(10)]"
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

OMISE

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
