# servir_modele_local

> Comment servir un modèle avec une performance optimale en local ?

## Comment s'en servir

```
python outils/servir_modele_local.py servir --modele exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: servir_modele_local.py [-h] [--json] [--racine RACINE]
                              {servir,concurrents,optimiser-memoire,batch-dynamique,mesurer-performance,inventaire} ...

Outil pour servir un modèle de langage en local avec optimisations GPU.

positional arguments:
  {servir,concurrents,optimiser-memoire,batch-dynamique,mesurer-performance,inventaire}
    servir              Serve un modèle localement
    concurrents         Simule des requêtes concurrentes
    optimiser-memoire   Configure l'allocation mémoire dynamique
    batch-dynamique     Configure le batch dynamique
    mesurer-performance
                        Mesure la performance du modèle
    inventaire          Inventaire des fichiers modèles dans un dossier

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON
  --racine RACINE       Racine du projet (défaut : répertoire du script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne mesure pas la qualité des réponses (seulement la performance brute).

## Contre‑exemples

Un modèle quantifié (ex : GGUF) peut avoir une latence plus faible mais un débit réduit à cause des opérations CPU/GPU.

## Ce qu'il lui faut

vllm — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

