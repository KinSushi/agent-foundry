# afficher_progression

> OMISE

## Comment s'en servir

```
afficher_progression.py --json --description 'Analyse' fichier.txt
```

Sous-commande `cible` : Chemin vers le fichier ou dossier à traiter.

## Toutes les options

```
usage: afficher_progression.py [-h] [--json] [--racine RACINE]
                               [--description DESCRIPTION]
                               cible

Où en est ce traitement long ?

positional arguments:
  cible                 Chemin vers le fichier ou dossier à traiter.

options:
  -h, --help            show this help message and exit
  --json                Produit une sortie JSON sur stdout au lieu d'une
                        sortie humaine.
  --racine RACINE       Chemin racine pour les imports relatifs (défaut :
                        répertoire du script).
  --description DESCRIPTION
                        Description affichée avant la barre de progression.

Exemple : afficher_progression.py --json --description 'Analyse' fichier.txt
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

être imprécis si le temps par élément varie fortement. Sans module tierce, seule une sortie textuelle basique est disponible.

## Contre‑exemples

hétérogènes (ex : lecture de fichiers de tailles très différentes).

## Ce qu'il lui faut

rich, tqdm — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
