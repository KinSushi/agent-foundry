# envoyer_alerte_sentry

> Cette erreur doit-elle être rapportée ?

## Comment s'en servir

```
python outils/envoyer_alerte_sentry.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: envoyer_alerte_sentry.py [-h] [--json] [--racine RACINE] [--envoyer]

Détermine si une erreur doit être rapportée à Sentry.

options:
  -h, --help       show this help message and exit
  --json           Sortie JSON machine-lisible sur stdout.
  --racine RACINE  Répertoire racine du projet (override du répertoire du
                   script).
  --envoyer        Envoie réel de l'alerte à Sentry (hors ligne par défaut).

Exemple d'appel : python -m envoyer_alerte_sentry --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Aucun contexte supplémentaire (pile, variables locales, etc.) n'est pris en compte.
- La décision repose uniquement sur le type d'exception.

## Contre‑exemples

- Une KeyboardInterrupt lors d'un Ctrl+C ne doit pas être rapportée.
- Une SystemExit levée délibérément ne doit pas être rapportée.

## Ce qu'il lui faut

sentry_sdk — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
