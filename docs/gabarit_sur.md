# gabarit_sur

> Ce texte peut‑il être construit sans risque d'injection ?

## Comment s'en servir

```
python gabarit_sur.py verifier mon_fichier.py --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: gabarit_sur.py [-h] [--racine RACINE] [--json] {verifier,selftest} ...

Inspection et rendu sécurisé des t‑strings (PEP 750).

positional arguments:
  {verifier,selftest}
    verifier           Analyse un fichier texte et signale les caractères
                       invisibles.
    selftest           Test interne : appelle rendre et inspecter sur un
                       gabarit simple.

options:
  -h, --help           show this help message and exit
  --racine RACINE      Chemin racine à utiliser à la place de celui dérivé du
                       script.
  --json               Produire la sortie au format JSON unique.

Exemple : python gabarit_sur.py verifier mon_fichier.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne protège que ce qui passe par le gabarit ; une chaîne assemblée ailleurs puis injectée échappe à tout contrôle. Ne remplace pas les paramètres liés d'un pilote SQL. CONTRE‑EXEMPLE Une f‑string à la même apparence ne donne AUCUNE séparation – la ressemblance syntaxique est le piège principal.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

