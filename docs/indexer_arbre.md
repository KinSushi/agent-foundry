# indexer_arbre

> où est ce symbole, et cette citation existe-t-elle vraiment ?

## Comment s'en servir

```
indexer_arbre.py [-h] {indexer,chercher,citation,verifier,comparer} ...
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: indexer_arbre.py [-h] {indexer,chercher,citation,verifier,comparer} ...

Indexer et chercher dans un arbre de fichiers texte.

positional arguments:
  {indexer,chercher,citation,verifier,comparer}

options:
  -h, --help            show this help message and exit
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

un index PÉRIMÉ répond faux sans le dire -- d'où l'empreinte ; FTS5 tokenise, donc une recherche de ponctuation échoue ; os.walk ne suit pas les liens par défaut

## Contre‑exemples

37 lignes citées introuvables sur 39 : un « introuvable » sans plus proche voisin se lit comme « ce code n'existe pas »

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

