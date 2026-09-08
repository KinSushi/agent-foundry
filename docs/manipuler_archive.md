# manipuler_archive

> Que contient cette archive, et comment l’extraire ?

## Comment s'en servir

```
python outils/manipuler_archive.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: manipuler_archive.py [-h] [--dest DEST] [--racine RACINE] [--json]
                            [--extract]
                            [archive]

Analyse et extraction sécurisée d’archives tar ou zip.

positional arguments:
  archive          Chemin vers l’archive à analyser.

options:
  -h, --help       show this help message and exit
  --dest DEST      Répertoire de destination pour l’extraction (défaut :
                   répertoire courant).
  --racine RACINE  Chemin racine à placer en tête de sys.path.
  --json           Émettre la sortie au format JSON unique sur stdout.
  --extract        Effectuer l’extraction après l’analyse.

Exemple : python manipuler_archive.py mon.tar.gz --dest ./out --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

* Les archives compressées avec des algorithmes non supportés par la
bibliothèque standard (ex. zstd sans le module ``compression``) ne seront pas reconnues.
* Les archives vides ne produisent aucun résultat exploitable.

## Contre‑exemples

Une archive contenant un lien symbolique pointant hors du répertoire de destination sera rejetée par le filtre ``data`` et ne sera pas extrait.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
