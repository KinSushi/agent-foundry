# decrire_paquet

> Le processus d'importation utilise-t-il réellement setuptools ? Quelle est la configuration complète du paquet (pyproject.toml/setup.cfg) ?

## Comment s'en servir

```
python outils/decrire_paquet.py prevenir_conflit --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: decrire_paquet.py [-h] [--json] [--racine RACINE]
                         {prevenir_conflit,forcer_setuptools,decrire_configuration} ...

Analyse et force l'usage de setuptools comme moteur d'empaquetage.

positional arguments:
  {prevenir_conflit,forcer_setuptools,decrire_configuration}
    prevenir_conflit    Vérifie que setuptools a déjà injecté _distutils_hack
    forcer_setuptools   Force l'usage de setuptools en retirant distutils de
                        sys.modules
    decrire_configuration
                        Décrit la configuration complète du paquet
                        (pyproject.toml/setup.cfg)

options:
  -h, --help            show this help message and exit
  --json                Sortie en JSON
  --racine RACINE       Racine du projet (défaut: répertoire du script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

L'outil ne peut pas détecter des imports faits dans des sous-processus. Le mode dégradé (stdlib) ne développe pas les valeurs dynamiques.

## Contre‑exemples

Un script qui importe distutils puis setuptools dans un thread secondaire. Un pyproject.toml avec des valeurs dynamiques non résolues en mode stdlib.

## Ce qu'il lui faut

setuptools — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

