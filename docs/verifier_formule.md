# verifier_formule

> OMISE

## Comment s'en servir

```
python outils/verifier_formule.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: verifier_formule.py [-h] [--racine RACINE] [--json]
                           [--precision PRECISION] [--points POINTS]
                           [--cible {math,numpy}]
                           expr_a [expr_b]

Vérifie l'équivalence de deux expressions algébriques.

positional arguments:
  expr_a                Première expression algébrique ou chemin vers un
                        fichier contenant deux expressions
  expr_b                Deuxième expression algébrique (optionnel si expr_a
                        est un fichier)

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du projet (défaut: répertoire de l'outil)
  --json                Sortie au format JSON
  --precision PRECISION
                        Précision pour mpmath (défaut: 50)
  --points POINTS       Nombre de points pour mpmath (défaut: 20)
  --cible {math,numpy}  Cible pour lambdify (défaut: math)

Exemple : verifier_formule.py "(x+1)**2" "x**2+2*x+1" --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

concordance mpmath sur 50 chiffres reste un FAISCEAU, jamais une preuve ; ne traite ni boucle, ni effet de bord, ni structure de donnees

## Contre‑exemples

d'ou le verdict DOMAINES DIVERGENTS, distinct des trois autres

## Ce qu'il lui faut

mpmath, networkx, sympy — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

