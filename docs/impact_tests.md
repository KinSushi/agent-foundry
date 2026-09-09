# impact_tests

> quels tests dois-je relancer après ce changement ?

## Comment s'en servir

```
python outils/impact_tests.py carte . --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: impact_tests.py [-h] {carte,impact,orphelines} ...

Cartographie l'impact des changements sur les tests.

positional arguments:
  {carte,impact,orphelines}
    carte               Génère la carte d'impact
    impact              Trouve les tests impactés par une fonction
    orphelines          Trouve les fonctions orphelines

options:
  -h, --help            show this help message and exit

Exemple : python impact_tests.py carte . --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

une fonction appelée par réflexion n'apparaît pas ; un test qui échoue à l'import ne cartographie rien ; « aucun test » ne veut pas dire « code mort » ; carte périmée dès que le code change

## Contre‑exemples

TDAD mesure que des instructions TDD SANS carte portent les régressions de 6,08 % à 9,94 % — la discipline seule NUIT

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

