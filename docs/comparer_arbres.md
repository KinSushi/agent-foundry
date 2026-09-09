# comparer_arbres

> qu'est-ce qui a changé entre ces deux arbres ?

## Comment s'en servir

```
python outils/comparer_arbres.py ./a ./b --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: comparer_arbres.py [-h] [--json] [--rapide] [--contenu] [--partiel]
                          [--racine RACINE]
                          arbre_a arbre_b

Compare deux arbres de fichiers en profondeur.

positional arguments:
  arbre_a          Premier arbre à comparer
  arbre_b          Deuxième arbre à comparer

options:
  -h, --help       show this help message and exit
  --json           Rend un seul objet JSON sur stdout
  --rapide         Comparaison par taille et date (peut manquer des
                   modifications)
  --contenu        Affiche le contenu des changements (diff)
  --partiel        Accepte un résultat partiel si des erreurs de lecture
                   surviennent
  --racine RACINE  Surcharge la racine du projet

Exemple: python outils/comparer_arbres.py v1 v2 --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

ne suit pas les renommages (un fichier renommé paraît supprimé + ajouté) ; ne compare pas les permissions ni les liens symboliques ; le hachage coûte une lecture complète

## Contre‑exemples

dircmp rend « identiques » deux fichiers de même taille et même mtime au contenu DIFFÉRENT — mesuré, c'est la raison de l'outil

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

