# saisir_blocage

> OMISE

## Comment s'en servir

```
python outils/saisir_blocage.py borner --secondes 0.1 exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: saisir_blocage.py [-h] [--racine RACINE] [--json]
                         {borner,photographier,armer} ...

Saisir un blocage de processus Python via faulthandler.

positional arguments:
  {borner,photographier,armer}
    borner              Lance une commande Python bornée en temps.
    photographier       Photographie les threads d'un processus.
    armer               Exécute une commande dans un contexte armé et annulé.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du projet (surcharge la déduction automatique).
  --json                Rend un objet JSON sur stdout.

Exemple: python saisir_blocage.py borner --secondes 1 script.py
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
