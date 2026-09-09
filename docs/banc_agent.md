# banc_agent

> OMISE

## Comment s'en servir

```
python banc_agent.py mesurer python travail.py
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: banc_agent.py [-h] [--racine RACINE] [--json]
                     {mesurer,session,jetons} ...

Banc d'essai pour mesurer le coût réel d'un agent (processus, mémoire, CPU,
jetons).

positional arguments:
  {mesurer,session,jetons}
    mesurer             Mesure le coût complet d'une commande
    session             Agrège le coût de N commandes séparées par ';'
    jetons              Compte les jetons d'un texte

options:
  -h, --help            show this help message and exit
  --racine RACINE       Surcharge la racine du projet
  --json                Rend un seul objet JSON sur stdout

Exemple: python banc_agent.py mesurer python travail.py
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

l'échantillon est invisible ; le coût d'un appel réseau (modèle distant) n'est pas mesurable localement

## Contre‑exemples

travaillé 192 ms — mesuré, et le zéro passe pour un résultat

## Ce qu'il lui faut

cpuinfo, psutil, tiktoken — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

