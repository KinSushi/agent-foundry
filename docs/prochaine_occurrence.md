# prochaine_occurrence

> La prochaine occurrence d'une tâche récurrente selon une expression cron.

## Comment s'en servir

```
python outils/prochaine_occurrence.py entre "0 0 * * *" "2023-01-01T00:00:00" "2023-01-02T00:00:00" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: prochaine_occurrence.py [--json] [--racine RACINE]
                               {entre,verifie,precedente} ...

Outil de calcul d'occurrences cron.

positional arguments:
  {entre,verifie,precedente}
    entre               Génère les occurrences entre deux dates.
    verifie             Vérifie si une date correspond à l'expression cron.
    precedente          Calcule l'occurrence précédente.

options:
  --json                Produit la sortie au format JSON unique.
  --racine RACINE       Chemin racine à utiliser à la place de la valeur par
                        défaut.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Sans croniter : ne gère pas les expressions avec secondes, années, ou caractères spéciaux L/W/#.

## Contre‑exemples

L'expression `0 0 * * 1-5/2` (lundi à vendredi, tous les 2 jours) peut être mal interprétée sans croniter.

## Ce qu'il lui faut

croniter — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

