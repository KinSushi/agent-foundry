# chercher_tolerant

> Ce fragment existe‑t‑il, même mal cité, et où exactement ?

## Comment s'en servir

```
chercher_tolerant [-h] [--racine RACINE] [--json]
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: chercher_tolerant [-h] [--racine RACINE] [--json]
                         {chercher,structure,graphemes} ...

Recherche tolérante de fragments dans des fichiers Python.

positional arguments:
  {chercher,structure,graphemes}
    chercher            Recherche un fragment avec tolérance.
    structure           Extrait les blocs correctement imbriqués.
    graphemes           Compte les graphemes d’un texte.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Répertoire racine à parcourir (défaut : répertoire du
                        script).
  --json                Produit une sortie JSON unique sur stdout.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

La recherche fuzzy est coûteuse ; la tolérance ne peut dépasser ``len(fragment)//3``. Au‑delà, l’outil refuse de conclure. CONTRE‑EXEMPLE Avec ``--fautes 10`` sur un fragment de 12 caractères, la recherche renverrait presque tout ; l’outil refuse donc.

## Ce qu'il lui faut

regex — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

