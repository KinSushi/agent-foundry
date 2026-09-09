# parser_yaml_toml

> Que contient ce fichier de configuration ?

## Comment s'en servir

```
python outils/parser_yaml_toml.py ./valide.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: parser_yaml_toml.py [-h] [--json] [--racine RACINE] fichier

Affiche le contenu d'un fichier de configuration YAML/TOML.

positional arguments:
  fichier          Chemin vers le fichier YAML ou TOML à analyser.

options:
  -h, --help       show this help message and exit
  --json           Sortir un seul objet JSON sur stdout au lieu de la représentation lisible.
  --racine RACINE  Surcharge la racine du projet et l'ajoute au sys.path.

Exemple : parser_yaml_toml.py config.yaml --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Aucun parsing structuré n'est effectué car aucune bibliothèque tierce n'est
utilisée (stdlib uniquement).
- La liste `examines` est tronquée à 200 éléments ; au‑delà, le drapeau
`examines_tronques` est activé.
- Un fichier binaire (contient le caractère nul) est détecté et entraîne un
dénominateur nul.

## Contre‑exemples

- Un fichier JSON n'est pas pris en charge ; l'outil le traite comme texte
brut.
- Un fichier vide ou ne contenant que des lignes blanches entraîne un
dénominateur nul et un code de sortie 3 (refus de conclure).

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

