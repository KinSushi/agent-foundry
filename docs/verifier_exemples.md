# verifier_exemples

> OMISE

## Comment s'en servir

```
python outils/verifier_exemples.py verifier mon_module.py
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: verifier_exemples.py [-h] [--racine RACINE] [--json] [--exiger-execute]
                            {verifier,couverture} ...

Vérifie les exemples doctest d'un fichier Python — exécute réellement chaque exemple.

positional arguments:
  {verifier,couverture}
    verifier            vérifier les exemples d'un fichier
    couverture          taux de fonctions avec exemple

options:
  -h, --help            show this help message and exit
  --racine RACINE       racine du projet (par défaut : parent du dossier
                        outils)
  --json                sortie JSON unique sur stdout
  --exiger-execute      refuse de conclure si l'AST voit des exemples mais
                        doctest n'en exécute aucun

Exemple : python outils/verifier_exemples.py verifier mon_module.py
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

échoue sans être faux ; doctest compare la REPRÉSENTATION textuelle — 0.1+0.2 ne vaut pas 0.3 ; exemples non déterministes (aléatoire, heure, etc.) échouent sans être faux

## Contre‑exemples

sans le compte `attempted`

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

