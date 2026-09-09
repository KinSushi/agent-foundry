# json_tronque

> Peut‑on extraire la valeur d’une clé profonde sans charger tout le fichier ?

## Comment s'en servir

```
python outils/json_tronque.py tolerant --texte '{"agent": {"etapes": [1, 2, 3], "fin": "oui' --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: json_tronque.py [-h] [--json] [--racine RACINE] [--version]
                       {stream,extract,tolerant,error-pos,resume} ...

Analyse incrémentale de fichiers JSON volumineux.

positional arguments:
  {stream,extract,tolerant,error-pos,resume}
    stream              Compte les objets JSON valides dans un flux.
    extract             Extrait la valeur à un chemin JSON donné.
    tolerant            Parse le fichier en tolérant les erreurs.
    error-pos           Trouve la position de la première erreur JSON.
    resume              Parse le fichier en continuant après les erreurs.

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON (un seul objet)
  --racine RACINE       Répertoire racine pour les chemins relatifs
  --version             show program's version number and exit
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les clés situées après une erreur de syntaxe peuvent être incomplètes.

## Contre‑exemples

Un JSON tronqué comme '{"a": "b' sera complété en '{"a": "b"}'.

## Ce qu'il lui faut

partial_json_parser — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

