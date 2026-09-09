# deviner_encodage

> Quel encodage le fichier utilise‑t‑il réellement ?

## Comment s'en servir

```
python outils/deviner_encodage.py cd exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: deviner_encodage.py [-h] [--json] [--racine RACINE]
                           {cd,api,constant} ...

Devine l’encodage d’un fichier texte.

positional arguments:
  {cd,api,constant}
    cd               Détection d’encodage
    api              Normalisation vers UTF‑8
    constant         Extraction de métadonnées d’encodage

options:
  -h, --help         show this help message and exit
  --json             Produit la sortie au format JSON
  --racine RACINE    Chemin racine à utiliser à la place du répertoire du
                     script

Exemple : deviner_encodage.py cd data.txt --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les fichiers très fragmentés ou contenant plusieurs encodages simultanés peuvent être mal classés.

## Contre‑exemples

Un fichier UTF‑16‑LE avec BOM suivi d’une portion en UTF‑8 : l’outil indique « UTF‑16LE » avec 0.92 de confiance, alors que le texte visible est UTF‑8.

## Ce qu'il lui faut

charset_normalizer — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

