# analyser_portees

> À quelle portée appartient chaque nom, et quelles fermetures capturent variable qui évolue ?

## Comment s'en servir

```
python -m outils.analyser_portees captures mon_module.py --racine .
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: analyser_portees.py [-h] [--racine RACINE] [--json]
                           {portees,captures,valider,morts} ...

Analyse les portées Python et détecte les captures libres, les erreurs de
portée et les variables mortes.

positional arguments:
  {portees,captures,valider,morts}
    portees             Affiche l’arbre des portées.
    captures            Détecte les captures libres des lambdas.
    valider             Valide les règles de portée (équivalent compile).
    morts               Liste les variables locales assignées mais jamais
                        lues.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine du projet (défaut : répertoire contenant
                        cet outil).
  --json                Émettre la sortie au format JSON unique.

Exemple : python -m outils.analyser_portees captures mon_module.py --racine .
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne voit pas les noms créés à l’exécution (``setattr``, ``exec``, ``globals()[...]``) ; une capture libre n’est pas toujours un bug ; les portées « annotation » de Python 3.14 ne sont pas comptées comme fonctions. CONTRE‑EXEMPLE ``lambda: i`` et ``lambda i=i: i`` ont le même arbre AST mais des portées différentes ; l'AST ne peut pas les distinguer.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
