# mesurer_dette

> OMISE

## Comment s'en servir

```
python -m outils.mesurer_dette mesurer mon_module.py --seuil 20
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: mesurer_dette.py [-h] [--racine RACINE] [--json] {mesurer,deriver} ...

Mesure la dette technique d'un fichier Python.

positional arguments:
  {mesurer,deriver}
    mesurer          Mesurer la dette d'un fichier Python.
    deriver          Comparer deux versions d'un même fichier.

options:
  -h, --help         show this help message and exit
  --racine RACINE    Chemin racine du projet (défaut : répertoire du script).
  --json             Émettre la sortie au format JSON unique.

Exemple : python -m outils.mesurer_dette mesurer mon_module.py --seuil 20
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

peut être fausse, une fonction à 30 branches peut être un automate légitime ; la formule de maintenabilité n'est pas normalisée ; ast.walk additionne les fonctions imbriquées

## Contre‑exemples

un score faible signale, il ne condamne pas

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

