# tracer_session_llm

> Quels sont les prompts, réponses et métriques associés à chaque run LLM ?

## Comment s'en servir

```
python outils/tracer_session_llm.py log --prompt "test" --reponse "ok" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: tracer_session_llm.py [-h] [--json] [--racine RACINE]
                             {log,batch,experiment,trace} ...

Traceur de sessions LLM

positional arguments:
  {log,batch,experiment,trace}
    log                 Enregistre un appel LLM
    batch               Évalue un lot de prompts
    experiment          Expérimentations LLM
    trace               Gestion des médias et tableau de bord

options:
  -h, --help            Affiche ce message d'aide et quitte avec le code 0
  --json                Sortie au format JSON sur stdout (rien d'autre)
  --racine RACINE       Racine du projet (défaut: répertoire du script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Aucun suivi réseau n’est tenté si l'option correspondante est absente.

## Contre‑exemples

Un appel `log --prompt "hi" --reponse "hello"` sans `--metadata` crée une trace sans lien explicite.

## Ce qu'il lui faut

langfuse — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

