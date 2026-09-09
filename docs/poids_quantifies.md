# poids_quantifies

> Comment réduire la taille mémoire d'un tenseur ou modèle PyTorch sans perdre trop de précision ?

## Comment s'en servir

```
python outils/poids_quantifies.py quantifier-tensor --input exemple.py --output ./out.pt --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: poids_quantifies.py [-h] [--json] [--racine RACINE]
                           {augmenter-taille,charger-modele,accelerer-inference,sync-distribue,choisir-compression,quantifier-tensor,decompresser-mmap,remplacer-couches} ...

Outil de compression et quantification de poids PyTorch.

positional arguments:
  {augmenter-taille,charger-modele,accelerer-inference,sync-distribue,choisir-compression,quantifier-tensor,decompresser-mmap,remplacer-couches}
    augmenter-taille    Compresse un tenseur.
    charger-modele      Charge un checkpoint compressé.
    accelerer-inference
                        Compresse les couches linéaires d’un modèle.
    sync-distribue      Synchronise un tenseur compressé.
    choisir-compression
                        Détermine la configuration optimale.
    quantifier-tensor   Quantifie un tenseur.
    decompresser-mmap   Dé‑compresse via mmap.
    remplacer-couches   Remplace les couches linéaires.

options:
  -h, --help            show this help message and exit
  --json                Produit une sortie JSON unique sur stdout.
  --racine RACINE       Chemin racine à utiliser pour les chemins relatifs.

Exemple :
  poids_quantifies.py quantifier-tensor --input gros.pt --output petit.pt --bits 8 --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

La précision peut varier selon le taux de compression et le type de données.

## Contre‑exemples

Un modèle avec des poids déjà très dispersés perdra plus de précision à la quantification.

## Ce qu'il lui faut

compressed_tensors — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

