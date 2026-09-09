# ecart_declare_reel

> Ce que je lis dans ce fichier est-il ce qui s'exécutera ?

## Comment s'en servir

```
python outils/ecart_declare_reel.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: ecart_declare_reel.py [-h] [--racine RACINE] [--sans-importer] [--json]
                             [--citation CITATION]
                             module

Compare l'AST d'un module Python avec ce qui est réellement exécuté. Exemple :
python outils/ecart_declare_reel.py mon_module --json

positional arguments:
  module               Nom complet du module à analyser (exemple :
                       package.module) ou chemin vers un fichier .py.

options:
  -h, --help           show this help message and exit
  --racine RACINE      Chemin racine à utiliser à la place du répertoire du
                       script.
  --sans-importer      Utilise uniquement pyclbr (pas d'import) pour éviter
                       les effets de bord.
  --json               Émet le résultat au format JSON unique.
  --citation CITATION  Confronte une signature prétendue au réel (exemple:
                       'ma_fonction(x, y=2)').
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne peut PAS énumérer un __getattr__ dynamique ; ne voit pas ce qu'un import conditionnel n'a pas exécuté.

## Contre‑exemples

`isfunction|isclass` déclare `typing.ClassVar` absent alors qu'il existe — un filtre trop étroit fabrique de fausses divergences.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

