# verifier_code_genere

> ce code généré fait-il ce que l'original faisait, sans danger ?

## Comment s'en servir

```
d'appel réel:
```

Sous-commande `genere` : Code généré à vérifier (expression ou fonction).

## Toutes les options

```
usage: verifier_code_genere.py [-h] [--racine RACINE] [--json]
                               genere [original]

Vérifie que le code généré est équivalent et sûr.

positional arguments:
  genere           Code généré à vérifier (expression ou fonction).
  original         Code original de référence (optionnel). Si omis, l'étape
                   d'équivalence est sautée.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine du projet (surcharge la valeur déduite de
                   __file__).
  --json           Sortie JSON unique sur stdout, rien d'autre.

Exemple d'appel réel:
  python -m outils.verifier_code_genere "pi * r ** 2" "pi * r * r"
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

ne voit pas les effets de bord, les boucles, les E/S, les structures de données. Ne prouve pas la correction d'un PROGRAMME, seulement l'équivalence d'une EXPRESSION

## Contre‑exemples

`x/x` vs `1` rend ÉQUIVALENT alors que c'est faux en x=0 — d'où le verdict DOMAINES DIVERGENTS

## Ce qu'il lui faut

RestrictedPython, sympy — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
