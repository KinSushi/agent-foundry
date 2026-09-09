# transferer_modele

> Le fichier distant ou local a‑t‑il été entièrement récupéré ou vérifié ?

## Comment s'en servir

```
python outils/transferer_modele.py verifier exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: transferer_modele.py [-h] [--json] [--racine RACINE]
                            {verifier,reprendre,extraire,historique,dedup} ...

Outil de transfert et vérification de fichiers depuis XetHub ou localement.

positional arguments:
  {verifier,reprendre,extraire,historique,dedup}
    verifier            Vérifie un fichier local et calcule son empreinte.
    reprendre           Reprend ou démarre le téléchargement d’un fichier
                        distant.
    extraire            Télécharge uniquement le fichier indiqué.
    historique          Affiche la liste des commits modifiant le fichier.
    dedup               Indique si le dépôt utilise la déduplication par
                        blocs.

options:
  -h, --help            show this help message and exit
  --json                Produit la sortie au format JSON unique.
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les métadonnées distantes peuvent être obsolètes si le dépôt a changé entre deux requêtes.

## Contre‑exemples

Un dépôt a été réécrit (force‑push) après le début du téléchargement ; la taille attendue diffère de la réalité et l’outil signale un succès erroné.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

