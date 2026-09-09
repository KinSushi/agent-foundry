# interroger_paquets

> Quelle version, quels points d'entrée, quelle distribution ou quelles dépendances pour un paquet Python installé dans l'environnement courant ?

## Comment s'en servir

```
python outils/interroger_paquets.py version-requests --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: interroger_paquets.py [-h] [--json] [--racine RACINE]
                             {version-requests,entrypoints-pytest,distribution-de-module,dependencies-pandas} ...

Interroge les métadonnées des paquets Python installés.

positional arguments:
  {version-requests,entrypoints-pytest,distribution-de-module,dependencies-pandas}
    version-requests    Affiche la version installée de requests.
    entrypoints-pytest  Affiche les points d'entrée console_scripts de pytest.
    distribution-de-module
                        Affiche la distribution qui a installé un module
                        donné.
    dependencies-pandas
                        Affiche les dépendances de pandas.

options:
  -h, --help            show this help message and exit
  --json                Produit une sortie JSON sur stdout (par défaut :
                        sortie lisible).
  --racine RACINE       Racine du projet (par défaut : répertoire du script).
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne voit pas les paquets installés en mode "editable" (pip install -e) dont
les métadonnées ne sont pas dans site-packages.
- Ne résout pas les dépendances optionnelles non installées.
- En mode dégradé, seules les fonctions compatibles avec importlib.metadata sont disponibles.

## Contre‑exemples

- Un paquet installé via un lien symbolique hors de site-packages ne sera pas vu.
- Un paquet avec des métadonnées corrompues lèvera une exception.
- Un module importé depuis un zip ne sera pas associé à sa distribution.

## Ce qu'il lui faut

importlib_metadata — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

