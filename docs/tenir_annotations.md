# tenir_annotations

> ce code tient‑il les promesses de ses annotations ?

## Comment s'en servir

```
python outils/tenir_annotations.py ./a ./b --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: tenir_annotations.py [-h] [--racine RACINE] [--json]
                            {couverture,verifier} ...

Vérifie la conformité des retours de fonctions aux annotations.

positional arguments:
  {couverture,verifier}
    couverture          Analyse sans exécution.
    verifier            Analyse avec appel éventuel.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine du projet (défaut : répertoire du
                        script).
  --json                Émettre la sortie au format JSON unique.

Exemple : python -m outils.tenir_annotations verifier mon_module --appeler
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

les paramètres de généricité ne sont pas vérifiés (list[int] → list) ; Any n’affirme rien ; appeler une fonction l’exécute. La fonction `conforme` ne vérifie pas les paramètres de fonction, seulement les retours.

## Contre‑exemples

un vérificateur sans traitement des unions déclare VIOLÉE une fonction Optional[float] rendant None — faux positif mesuré.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
