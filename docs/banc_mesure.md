# banc_mesure

> OMISE

## Comment s'en servir

```
python outils/banc_mesure.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: banc_mesure.py [-h] [--repetitions REPETITIONS] [--json]
                      [--racine RACINE]
                      module

Compare des voies de calcul, et refuse de conclure quand la comparaison n'est pas honnête.

positional arguments:
  module                fichier Python exposant des fonctions mesure_*

options:
  -h, --help            show this help message and exit
  --repetitions REPETITIONS
                        nombre de mesures par voie (défaut 5, minimum 3)
  --json                rend un objet JSON sur stdout, et rien d'autre
  --racine RACINE       racine de résolution des chemins relatifs (défaut : le
                        répertoire courant)

Le module cible doit exposer des fonctions nommées mesure_*.
Exemple : python banc_mesure.py mesures_exemple.py --repetitions 7
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

la fréquence rendue par py-cpuinfo est ANNONCÉE, pas mesurée ; un cache chaud change le classement ; une seule mesure aberrante (ramasse-miettes, préemption) fausse l'étendue min→max

## Contre‑exemples

même à +50 % de travail -- l'étendue ne décroît pas avec les répétitions, le bruit standard si ; une seule mesure NaN empoisonne la médiane en silence

## Ce qu'il lui faut

cpuinfo — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
