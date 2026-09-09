# memoire_partagee

> Peut-on partager un état ou exécuter des tâches sans bloquer le flux principal ?

## Comment s'en servir

```
python outils/memoire_partagee.py client --cle test --valeur "{"data": 42}" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: memoire_partagee.py [-h] [--json] [--racine RACINE]
                           {client,cache,backoff,background} ...

Partage d'état persistant entre processus via Redis.

positional arguments:
  {client,cache,backoff,background}
    client              Stocker un état partagé dans Redis.
    cache               Vérifier ou forcer un cache Redis.
    backoff             Exécuter une commande Redis avec reconnexions
                        exponentielles.
    background          Ajouter une tâche à une liste Redis pour traitement en
                        arrière-plan.

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON (un seul objet)
  --racine RACINE       Racine des chemins relatifs (défaut: répertoire du
                        script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne gère pas les conflits de version (ex: deux processus écrivant la même clé).

## Contre‑exemples

Un processus écrit une clé pendant qu'un autre la lit : la lecture peut retourner une valeur obsolète.

## Ce qu'il lui faut

redis — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

