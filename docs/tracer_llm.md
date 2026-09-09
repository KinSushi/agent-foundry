# tracer_llm

> Quelles traces ce flux LLM a-t-il laissées ?

## Comment s'en servir

```
d'appel :
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: tracer_llm.py [-h] [--racine RACINE] [--json]

Détecte les traces laissées par un flux LLM dans l'environnement.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine du projet (par défaut : répertoire du
                   script)
  --json           Sortie au format JSON (un seul objet sur stdout)

Exemple d'appel :
  python tracer_llm.py --racine /path/to/project --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Nous ne détectons pas les traces envoyées à un point de terminaison distant (sans fichier local) si aucune variable d'environnement pertinente n'est définie. Nous ne examinons pas les traces dans les journaux standards ou autres systèmes de stockage.

## Contre‑exemples

Un flux LLM qui utilise Langfuse mais avec toutes les variables d'environnement définies sur des chaînes vides ne serait pas détecté. De même, un exportateur fichier configuré avec un répertoire inaccessible ou sans préfixe correspondant à aucun fichier ne serait pas détecté.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

