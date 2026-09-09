# tenseurs_locaux

> Mesure de gradients, Jacobiennes, Hessiennes, réarrangements et compilation de modèles PyTorch.

## Comment s'en servir

```
python outils/tenseurs_locaux.py compiler exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: tenseurs_locaux.py [-h] [--json] [--racine RACINE]
                          {gradient,batch_apply,jacobienne,hessienne,rearranger,compiler,ajouter_batch} ...

Outil de manipulation locale de tenseurs PyTorch

positional arguments:
  {gradient,batch_apply,jacobienne,hessienne,rearranger,compiler,ajouter_batch}
    gradient            Calcule le gradient
    batch_apply         Applique la fonction à chaque tenseur du batch
    jacobienne          Calcule la Jacobienne
    hessienne           Calcule la Hessienne
    rearranger          Réarrange le tenseur avec einops
    compiler            Compile un script PyTorch
    ajouter_batch       Enveloppe la fonction avec unsqueeze

options:
  -h, --help            show this help message and exit
  --json                Produit un objet JSON unique sur stdout
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Pas de support GPU avancé, précision flottante non garantie, aucune optimisation réseau.

## Contre‑exemples

Fonction non différentiable ou pattern einops invalide entraîne une erreur.

## Ce qu'il lui faut

torch — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

