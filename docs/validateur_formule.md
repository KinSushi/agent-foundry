# validateur_formule

> Ces deux formules mathématiques sont-elles équivalentes ?

## Comment s'en servir

```
python outils/validateur_formule.py '1+1' '2' --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: validateur_formule.py [-h] [--variables VARIABLES] [--racine RACINE]
                             [--json]
                             formule1 formule2

Vérifie si deux formules mathématiques sont équivalentes.

positional arguments:
  formule1              Première formule mathématique.
  formule2              Deuxième formule mathématique.

options:
  -h, --help            show this help message and exit
  --variables VARIABLES
                        Variables au format JSON (ex: '{"x": 1, "y": 2}').
  --racine RACINE       Répertoire racine pour les imports (défaut: répertoire
                        du script).
  --json                Sortie au format JSON.

Exemple : validateur_formule.py 'x + y' 'y + x' --variables '{"x": 1, "y": 2}'
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne gère pas les expressions avec variables non déclarées
- La précision numérique peut être insuffisante pour certaines expressions
- Sans sympy, certaines équivalences algébriques ne sont pas détectées

## Contre‑exemples

- Formules équivalentes mais non détectées sans sympy (ex: x+1 et 1+x)
- Formules avec singularités (division par zéro) non gérées

## Ce qu'il lui faut

sympy — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

