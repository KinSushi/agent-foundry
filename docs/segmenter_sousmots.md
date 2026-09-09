# segmenter_sousmots

> Peut-on exploiter un modèle de tokenisation pré‑entraîné sans connaître son format interne ?

## Comment s'en servir

```
python outils/segmenter_sousmots.py exemple exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: segmenter_sousmots.py [-h] [--json] [--racine RACINE]
                             {charger,metadonnees,exemple} ...

Segmentation de modèles de tokenisation SentencePiece.

positional arguments:
  {charger,metadonnees,exemple}
    charger             Charge un modèle SentencePiece et affiche sa taille.
    metadonnees         Extrait les métadonnées d’un modèle SentencePiece.
    exemple             Exemple d’examen : compte les lignes d’un fichier
                        texte.

options:
  -h, --help            show this help message and exit
  --json                Sortie JSON sur stdout (par défaut : sortie lisible)
  --racine RACINE       Répertoire racine pour les chemins relatifs (défaut :
                        répertoire du script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne vérifie pas la cohérence des tokens (ex : doublons) ; ignore les modèles non‑SentencePiece. CONTRE‑EXEMPLE Un fichier .model avec un vocabulaire vide (taille=0) est accepté, mais inutilisable en pratique.

## Ce qu'il lui faut

google, sentencepiece — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

