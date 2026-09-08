# reecrire_sur

> OMISE

## Comment s'en servir

```
python reecrire_sur.py remplacer mon_fichier.py --nom seuil --par
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: reecrire_sur.py [-h] [--json] {remplacer,diff,commentaires} ...

Réécrit un fichier Python en préservant les commentaires.

positional arguments:
  {remplacer,diff,commentaires}
    remplacer           Remplace un nom dans un fichier.
    diff                Compare deux fichiers jeton à jeton.
    commentaires        Liste les commentaires d'un fichier.

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON.

Exemple : python reecrire_sur.py remplacer mon_fichier.py --nom seuil --par
limite
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

d'autres fichiers) ; untokenize sur 2-uplets ne restitue pas les espaces ; ne traite pas les f-strings imbriquées comme un seul jeton en 3.12+

## Contre‑exemples

« équivalent » n'est pas « identique », et la différence s'appelle `# noqa`

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
