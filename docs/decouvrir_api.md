# decouvrir_api

> comment se sert‑on de cette API que je ne connais pas ?

## Comment s'en servir

```
python -m outils.decouvrir_api decouvrir interegular.parse_pattern --essai "('a+',)"
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: decouvrir_api [-h] [--racine RACINE] [--json]
                     {decouvrir,voisins,carte} ...

Explore dynamiquement une API Python inconnue.

positional arguments:
  {decouvrir,voisins,carte}
    decouvrir           Découvre une API, éventuellement en l'appelant.
    voisins             Propose des noms proches lorsqu'un attribut est introuvable.
    carte               Affiche la surface publique d'un module.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine à utiliser (défaut : répertoire du script).
  --json                Émettre la sortie au format JSON unique.

Exemple : python -m outils.decouvrir_api decouvrir interegular.parse_pattern --essai "('a+',)"
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

appeler, c'est exécuter ; le mode passif (sans ``--essai``) ne fait aucun appel. ``inspect.getmembers`` peut déclencher des propriétés ; un objet à ``__getattr__`` dynamique n'est pas énumérable. CONTRE‑EXEMPLE ``interegular.parse_pattern`` n'a aucune docstring : la signature seule ne révèle pas que le ``Pattern`` rendu possède ``.to_fsm()`` ni que le ``FSM`` possède ``empty()``.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
