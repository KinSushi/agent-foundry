# optimiseur_cpu

> Quelles fonctions consomment le plus de CPU ?

## Comment s'en servir

```
python optimiseur_cpu.py mon_script.py --json
```

Sous-commande `cible` : Chemin du fichier Python a analyser.

## Toutes les options

```
usage: optimiseur_cpu.py [-h] [--racine RACINE] [--json] cible

Identifie les fonctions qui consomment le plus de CPU par analyse statique du
bytecode apres import reel.

positional arguments:
  cible            Chemin du fichier Python a analyser

options:
  -h, --help       show this help message and exit
  --racine RACINE  Repertoire racine a inserer en tete de sys.path pour
                   l'import
  --json           Rend un seul objet JSON sur stdout

Exemple: python optimiseur_cpu.py mon_script.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne mesure pas le temps d'execution reel, ignore les entrees/sorties bloquantes et les appels systeme. Les fonctions generees dynamiquement non presentes dans l'espace de noms du module sont ignorees.

## Contre‑exemples

Une fonction avec une seule instruction `time.sleep(10)` aura un cout statique faible mais un cout temps reel eleve. Une fonction recursive aura un cout statique sous-estime.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

