# format_harmony

> Les réponses de différents modèles d'IA sont-elles cohérentes entre elles ? Un prompt est-il optimisé pour minimiser les hallucinations ? Une réponse contient-elle des biais de genre ou culturels ?

## Comment s'en servir

```
python outils/format_harmony.py harmoniser --reponses ./reponses.txt --modeles ./modeles.txt --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: format_harmony.py [-h] [--json] [--racine RACINE]
                         {harmoniser,optimiser,biais} ...

Outil d'analyse de cohérence entre réponses de modèles d'IA.

positional arguments:
  {harmoniser,optimiser,biais}
    harmoniser          Détecte les contradictions entre réponses de modèles
    optimiser           Optimise un prompt pour un modèle spécifique
    biais               Analyse les biais dans une réponse de modèle

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON (un seul objet sur stdout)
  --racine RACINE       Racine des chemins relatifs (défaut: répertoire de
                        l'outil)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne détecte pas les contradictions implicites.
- Les biais culturels dépendent du corpus de référence de openai_harmony.
- Mode dégradé moins précis que openai_harmony.

## Contre‑exemples

Un prompt optimisé pour réduire les hallucinations peut introduire des biais de genre.

## Ce qu'il lui faut

openai_harmony — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

