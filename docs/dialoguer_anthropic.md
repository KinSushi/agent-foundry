# dialoguer_anthropic

> Le texte généré correspond‑il à la demande du prompt ?

## Comment s'en servir

```
python outils/dialoguer_anthropic.py generer --prompt "Bonjour" --api-key "dummy" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: dialoguer_anthropic.py [-h] [--json] [--racine RACINE]
                              {generer,paginer,outils,types} ...

Dialoguer avec l'API Anthropic

positional arguments:
  {generer,paginer,outils,types}
    generer             Générer du texte à partir d'un prompt
    paginer             Paginer une réponse longue
    outils              Lister les outils disponibles
    types               Valider un type Anthropic

options:
  -h, --help            show this help message and exit
  --json                Sortie en JSON
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

Le serveur peut tronquer le texte si max_tokens trop bas.

## Contre‑exemples

Prompt très long > 10 000 tokens → l'outil renvoie partiellement le texte sans avertir.

## Ce qu'il lui faut

anthropic — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

