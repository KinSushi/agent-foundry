# contrat_mcp

> L'outil peut-il échanger, valider, documenter et vérifier la version d'un service JSON-RPC sans bloquer l'agent ?

## Comment s'en servir

```
python outils/contrat_mcp.py examiner ./a ./b --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: contrat_mcp.py [-h] [--json] [--racine RACINE]
                      {augmenter,valider,documenter,compatibilite,examiner} ...

Outil CLI pour interagir avec des services JSON-RPC 2.0

positional arguments:
  {augmenter,valider,documenter,compatibilite,examiner}
    augmenter           Envoie une requête JSON-RPC
    valider             Valide les méthodes exposées
    documenter          Retourne la documentation d'une méthode
    compatibilite       Vérifie la compatibilité de version
    examiner            Examine un fichier Python et/ou un message JSON‑RPC

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON (un seul objet)
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

Aucun test de charge, pas de support HTTP/2, pas de vérification TLS avancée.

## Contre‑exemples

Le service renvoie `{ "jsonrpc": "2.0", "result": 42 }` (absence du champ `id`). L'outil signale l’erreur mais continue d’exécuter les autres sous-commandes.

## Ce qu'il lui faut

mcp_types — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

