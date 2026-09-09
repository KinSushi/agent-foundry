# montrer_borne

> OMISE

## Comment s'en servir

```
python outils/montrer_borne.py --fichier exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: montrer_borne.py [-h] [--racine RACINE] [--json] [--jetons JETONS]
                        [--structure] [--comparer] [--fichier FICHIER]

Montre une structure bornée pour économiser des jetons.

options:
  -h, --help         show this help message and exit
  --racine RACINE    Surcharge la racine du projet
  --json             Sortie JSON
  --jetons JETONS    Budget en jetons
  --structure        Afficher le squelette
  --comparer         Comparer les formes
  --fichier FICHIER  Chemin vers un fichier JSON ou Python contenant l'objet

Exemple: python montrer_borne.py --fichier data.json --jetons 100
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

n'est plus du JSON valide ; un objet à __repr__ coûteux ou récursif peut piéger

## Contre‑exemples

dire sans mesure de la sortie

## Ce qu'il lui faut

tiktoken, wcwidth — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

