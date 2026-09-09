# manipuler_tensors

> Comment transformer ce tenseur ?

## Comment s'en servir

```
python outils/manipuler_tensors.py exemple.py --pattern "b h -> b h" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: manipuler_tensors.py [-h] --pattern PATTERN [--axes_lengths CLE=VALEUR]
                            [--json] [--racine RACINE]
                            [entree] [sortie]

Transforme un tenseur selon un motif einops.

positional arguments:
  entree                Chemin vers le fichier JSON contenant le tenseur d'entrée (liste imbriquée).
  sortie                Chemin vers le fichier JSON où écrire le tenseur transformé. Si omis, le résultat est affiché sur stdout (format JSON).

options:
  -h, --help            show this help message and exit
  --pattern PATTERN     Motif einops de réarrangement (ex: "b h w -> b (h w)").
  --axes_lengths CLE=VALEUR
                        Longueur d'un axe nommé utilisé dans le motif. Peut être répété.
  --json                Sortie au format JSON unique sur stdout (contrat + métriques + résultat).
  --racine RACINE       Surcharge la racine du projet pour les imports locaux.

Exemple d'appel réel :
  manipuler_tensors.py entree.json sortie.json --pattern "b h w -> b (h w)"
  manipuler_tensors.py entree.json --pattern "b h w c -> b c h w" --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Sans les paquets optionnels numpy et/ou einops, l'outil ne peut effectuer que la transformation identité.

## Contre‑exemples

Un fichier d'entrée vide ou ne contenant aucun élément entraîne un refus de conclusion.

## Ce qu'il lui faut

einops, numpy — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

