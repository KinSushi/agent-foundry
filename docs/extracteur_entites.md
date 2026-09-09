# extracteur_entites

> Quelles entités (noms, dates) sont présentes ?

## Comment s'en servir

```
d'appel réel: python extracteur_entites.py --json mon_fichier.txt
```

Sous-commande `cible` : Chemin du fichier à analyser.

## Toutes les options

```
usage: extracteur_entites.py [-h] [--json] [--racine RACINE] cible

Extrait les entités (noms, dates) d'un fichier.

positional arguments:
  cible            Chemin du fichier à analyser

options:
  -h, --help       show this help message and exit
  --json           Rend un seul objet JSON sur stdout
  --racine RACINE  Surcharge la racine et l'insère en tête de sys.path

Exemple d'appel réel: python extracteur_entites.py --json mon_fichier.txt
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Python pur ne fait pas de véritable NER. La détection des noms est basique. pycountry améliore la détection des pays.

## Contre‑exemples

Paris peut être un nom commun. Avril peut être un prénom.

## Ce qu'il lui faut

pycountry — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

