# garde_execution

> puis-je exécuter ce code sans conséquence accidentelle ?

## Comment s'en servir

```
python outils/garde_execution.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: garde_execution.py [-h] [--niveau {safe,limited,utility}] [--surface]
                          [--json] [--racine RACINE]
                          [--source SOURCE | fichier]

Garde d'exécution restreinte – détecte les accidents avant exécution.

positional arguments:
  fichier               Fichier contenant le code à analyser.

options:
  -h, --help            show this help message and exit
  --source SOURCE       Code source fourni directement en ligne de commande.
  --niveau {safe,limited,utility}
                        Niveau d'espace de noms à utiliser (défaut : safe).
  --surface             Affiche la surface des noms autorisés pour le niveau
                        choisi et quitte.
  --json                Produit la sortie au format JSON unique.
  --racine RACINE       Chemin racine à utiliser à la place de celui dérivé du
                        script.

Exemple d'appel :
    python -m outils.garde_execution --source "x = 1 + 1"
    python -m outils.garde_execution mon_script.py --niveau utility --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

ne borne NI le temps NI la mémoire ; n'isole pas le système de fichiers.

## Contre‑exemples

safe_globals seul fait échouer ``sum(range(10))`` : 12 builtins inoffensifs manquent.

## Ce qu'il lui faut

RestrictedPython — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
