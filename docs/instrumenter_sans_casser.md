# instrumenter_sans_casser

> Peut-on instrumenter du code Python sans en altérer le comportement observable ?

## Comment s'en servir

```
python outils/instrumenter_sans_casser.py decorators --cible exemple.py --nom mon_decorateur --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: instrumenter_sans_casser.py [-h] [--json] [--racine RACINE]
                                   {decorators,proxies,importer,patches,caching} ...

Instrumenter du code Python sans en casser le comportement.

positional arguments:
  {decorators,proxies,importer,patches,caching}
    decorators          Créer un décorateur préservant la signature
    proxies             Générer un proxy transmettant les méthodes spéciales
    importer            Intercepter les importations pour wrapper un module
    patches             Appliquer un patch temporaire avec restauration
                        garantie
    caching             Créer un cache acceptant des arguments non hashables

options:
  -h, --help            show this help message and exit
  --json                Sortie en JSON
  --racine RACINE       Racine pour les chemins relatifs
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne gère pas les décorateurs C‑extension, les objets avec __slots__ personnalisés, ou les modules compilés.

## Contre‑exemples

Un décorateur appliqué à une fonction avec des annotations de type complexes peut perdre les annotations dans la signature retournée.

## Ce qu'il lui faut

wrapt — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

