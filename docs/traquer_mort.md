# traquer_mort

> Quelles fonctions ce scénario n'entre‑t‑il jamais ?

## Comment s'en servir

```
python outils/traquer_mort.py exemple.py --scenario "import valide; valide.f()" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: traquer_mort.py [-h] --scenario SCENARIO [--racine RACINE]
                       [--exiger-lignes EXIGER_LIGNES] [--json]
                       cible

Détecte les fonctions jamais appelées par un scénario donné.

positional arguments:
  cible                 Chemin du module Python à analyser (fichier .py).

options:
  -h, --help            show this help message and exit
  --scenario SCENARIO   Code Python à exécuter pour exercer le module (entre
                        guillemets).
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script.
  --exiger-lignes EXIGER_LIGNES
                        Nombre minimal de lignes exécutées dans le fichier
                        cible (défaut : 1).
  --json                Émettre la sortie au format JSON (un seul objet).

Exemple : python -m outils.traquer_mort mon_module.py --scenario "import
mon_module; mon_module.main()"
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

« morte pour CE scénario » ≠ « morte » en général ; les appels par réflexion ou depuis un autre processus ne sont pas détectés. CONTRE‑EXEMPLES def f(): return 2          # corps sur la même ligne → INDÉCIDABLE Aucun ligne tracée       → instrument cassé, refus de conclure

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
