# inspecter_pickle

> que fera ce pickle si je le charge ?

## Comment s'en servir

```
inspecter_pickle.py inspecter mon_fichier.pkl
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: inspecter_pickle.py [-h] {inspecter,noms} ...

Inspecte un fichier pickle sans l'exécuter.

positional arguments:
  {inspecter,noms}
    inspecter       Inspecte un fichier pickle.
    noms            Liste les noms référencés par le pickle.

options:
  -h, --help        show this help message and exit

Exemple: inspecter_pickle.py inspecter mon_fichier.pkl
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

un nom peut être construit dynamiquement et échapper à la liste ; ne dit pas ce que le code appelé FAIT ; ne couvre pas les protocoles futurs

## Contre‑exemples

cloudpickle emploie REDUCE et STACK_GLOBAL comme un pickle piégé — un verdict fondé sur les seuls opcodes refuse tout transport légitime

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

