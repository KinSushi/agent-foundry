# eprouver_mcp

> Le processus identifié par PID X est‑il réellement bloqué ?

## Comment s'en servir

```
python outils/eprouver_mcp.py unblock --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: eprouver_mcp.py [-h] [-v]
                       {unblock,inventaire,inspect-server,negotiate-format,share-proxy,validate-type} ...

Outil d’évaluation de processus et services MCP.

positional arguments:
  {unblock,inventaire,inspect-server,negotiate-format,share-proxy,validate-type}
    unblock             Envoie SIGCONT à un processus bloqué.
    inventaire          Cherche des manifestes MCP dans un dossier.
    inspect-server      Inspecte un serveur MCP.
    negotiate-format    Négocie le format préféré avec un service MCP.
    share-proxy         Crée un proxy à partir d’un fichier picklé.
    validate-type       Valide une valeur JSON contre un schéma JSON.

options:
  -h, --help            show this help message and exit
  -v, --version         show program's version number and exit
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Les conteneurs sans accès à /proc ou les processus appartenant à un autre UID ne sont pas observables. CONTRE‑EXEMPLE Un processus en état « D » (uninterruptible sleep) ne répond pas à SIGCONT ; l’outil le signale comme bloqué mais ne le débloque pas.

## Ce qu'il lui faut

mcp — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

