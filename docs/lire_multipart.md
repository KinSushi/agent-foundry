# lire_multipart

> Décoder correctement un flux multipart sans dépasser la RAM disponible.

## Comment s'en servir

```
python outils/lire_multipart.py decoder --input exemple.py --boundary=----WebKitFormBoundary7MA4YWxkTr0gW --texte --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: lire_multipart.py [-h] [--json] [--racine RACINE]
                         {decoder,builder,check-errors} ...

Outil de manipulation de flux multipart (streaming si possible).

positional arguments:
  {decoder,builder,check-errors}
    decoder             Décode un corps multipart et décrit les parties.
    builder             Construit un corps multipart à partir de fichiers.
    check-errors        Vérifie la présence d’erreurs dans un flux multipart.

options:
  -h, --help            show this help message and exit
  --json                Produit une sortie JSON unique sur stdout.
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script.

Exemple : lire_multipart.py decoder --input payload.bin --boundary=----B
--json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Le décodage ne vérifie pas l’intégrité cryptographique du contenu.

## Contre‑exemples

Un fichier de 5 GiB avec un boundary manquant provoque une levée de BoundaryMissingError, mais l’outil continue à consommer de la RAM (bug connu dans la version 0.1).

## Ce qu'il lui faut

multipart — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

