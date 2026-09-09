# detecter_os

> Sur quel système cette machine tourne-t-elle ?

## Comment s'en servir

```
python detecter_os.py --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: detecter_os.py [-h] [--json] [--racine RACINE]

Détecte le système d'exploitation, la distribution, le CPU et la configuration
Python.

options:
  -h, --help       show this help message and exit
  --json           Affiche le résultat au format JSON.
  --racine RACINE  Chemin racine pour surcharger sys.path (non utilisé ici).

Exemple: python detecter_os.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les conteneurs ou machines virtuelles comme entités séparées. La détection CPU est limitée aux informations accessibles sans privilèges root.

## Contre‑exemples

Sur certains systèmes BSD, la détection de la distribution peut échouer.

## Ce qu'il lui faut

cpuinfo, distro — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

