# pont_outils

> OMISE

## Comment s'en servir

```
python outils/pont_outils.py --racine . lister
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: pont_outils.py [-h] [--racine RACINE] [--json]
                      {lister,contrat,charger,controler} ...

Vérifie que les outils sont utilisables par un autre projet.

positional arguments:
  {lister,contrat,charger,controler}
    lister              liste les outils sans les importer
    contrat             affiche le contrat d'un outil
    charger             charge un outil et rend son module
    controler           contrôle mécanique des outils

options:
  -h, --help            show this help message and exit
  --racine RACINE       racine du projet (défaut :
                        D:\Puissance_60+_bibliothèques_python)
  --json                sortie JSON sur stdout

Exemple : python outils/pont_outils.py --racine . lister
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

l'API soit stable entre deux versions

## Contre‑exemples

importable alors qu'il l'est — mesuré

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
