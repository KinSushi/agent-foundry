# appeler_natif

> Comment étendre Python avec du code C de manière sûre et dynamique ?

## Comment s'en servir

```
python outils/appeler_natif.py augmenter exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: appeler_natif.py [-h] [--json] [--racine RACINE]
                        {augmenter,compiler,verifier,manipuler,opaque} ...

Outil d’appel natif C depuis Python.

positional arguments:
  {augmenter,compiler,verifier,manipuler,opaque}
    augmenter           Appeler une fonction C.
    compiler            Compiler du code C à la volée.
    verifier            Vérifier la correspondance des types C.
    manipuler           Manipuler des pointeurs/structures C.
    opaque              Accéder à des champs de structures opaques.

options:
  -h, --help            show this help message and exit
  --json                Produit la sortie au format JSON unique.
  --racine RACINE       Chemin racine à utiliser (défaut : répertoire du
                        script).
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les erreurs logiques du code C, ni les incompatibilités ABI.

## Contre‑exemples

Un type C mal aligné (ex : `struct { char c; int i; }` sur 32 bits) peut corrompre la mémoire sans avertissement.

## Ce qu'il lui faut

_cffi_backend — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

