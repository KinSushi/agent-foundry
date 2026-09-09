# validateur_i18n

> Ces chaînes sont-elles bien encodées ?

## Comment s'en servir

```
python outils/validateur_i18n.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: validateur_i18n.py [-h] [--racine RACINE] [--json] fichier

Vérifie si les chaînes d'un fichier sont bien encodées pour toutes les
locales.

positional arguments:
  fichier          Chemin vers le fichier à analyser (.py ou .po)

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine pour les imports relatifs (défaut:
                   répertoire du script)
  --json           Sortie au format JSON sur stdout

Exemple: python validateur_i18n.py mon_fichier.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne vérifie pas les encodages dynamiques ou non standard. Se base sur les encodages connus de la stdlib.

## Contre‑exemples

Un encodage valide mais non supporté par Python (ex: encodages propriétaires).

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

