# validateur_regex

> Cette regex est-elle valide ?

## Comment s'en servir

```
python validateur_regex.py
```

Sous-commande `pattern` : La pattern de regex à valider.

## Toutes les options

```
usage: validateur_regex.py [-h] [--json] [--racine CHEMIN] [pattern]

Vérifie la validité d'une expression régulière Python.

positional arguments:
  pattern          La pattern de regex à valider.

options:
  -h, --help       show this help message and exit
  --json           Sortie JSON unique sur stdout (au lieu du texte lisible).
  --racine CHEMIN  Surcharge la racine du projet et l'ajoute à sys.path si
                   nécessaire.

Exemple : python validateur_regex.py
'(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne vérifie pas la compatibilité avec d'autres moteurs de regex.
- Ne teste pas l'exécution effective de la regex (correspondance,
substitution, etc.).
- Ne détecte pas les erreurs qui ne surviennent qu'à l'exécution
(ex. lookbehind de longueur variable).

## Contre‑exemples

La pattern "(?<=a*)b" compile sans erreur mais lève une re.error lors de la recherche d'une correspondance à cause d'un lookbehind de longueur variable. Cet outil la déclarera valide.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
