# auditeur_fichiers

> Quels fichiers sont modifiés en temps réel ?

## Comment s'en servir

```
python auditeur_fichiers.py --duree 10 --intervalle 1 --racine
```

Sous-commande `cible` : Chemin du fichier ou du répertoire à observer.

## Toutes les options

```
usage: auditeur_fichiers.py [-h] [--racine RACINE] [--duree DUREE]
                            [--intervalle INTERVALLE] [--json]
                            [cible]

Détecte les fichiers modifiés en temps réel.

positional arguments:
  cible                 Chemin du fichier ou du répertoire à observer.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine à préfixer dans sys.path (remplace le
                        répertoire du script).
  --duree DUREE         Durée d'observation en secondes (défaut : 5).
  --intervalle INTERVALLE
                        Intervalle entre deux sondages en secondes (défaut :
                        0.5).
  --json                Émettre la sortie au format JSON unique sur stdout.

Exemple : python auditeur_fichiers.py --duree 10 --intervalle 1 --racine
C:\projet mon_dossier
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Seules les modifications détectées par variation de st_mtime sont prises en compte ; les changements de contenu qui n'affectent pas l'horodatage, les modifications de métadonnées (ex. attributs Windows) ou les fichiers ouvertes en mode exclusif peuvent être manqués.

## Contre‑exemples

Un fichier dont le contenu est modifié mais dont le système ne met pas à jour st_mtime (ex. caches réseau) ne sera pas signalé.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
