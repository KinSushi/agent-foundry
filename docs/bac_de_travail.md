# bac_de_travail

> Puis‑je essayer ce code sans conséquence ?

## Comment s'en servir

```
python outils/bac_de_travail.py exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: bac_de_travail.py [--racine RACINE] [--json] [fichier]

Essayer rapidement du code Python avec RestrictedPython + sous‑interpréteur.

positional arguments:
  fichier          Chemin du fichier Python à tester.

options:
  --racine RACINE  Répertoire racine à utiliser à la place du répertoire du
                   script.
  --json           Émettre la sortie au format JSON unique.

Exemple : python -m outils.bac_de_travail --racine . script.py
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne borne NI le temps NI la mémoire ; une boucle infinie bloque le processus. L'isolation ne protège pas le système de fichiers (open est retiré, mais cela n'est pas équivalent). Un plantage natif emporte le processus. CONTRE‑EXEMPLE `safe_globals` seul fait échouer `sum(range(10))` — un garde trop étroit se fait désarmer.

## Ce qu'il lui faut

RestrictedPython — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
