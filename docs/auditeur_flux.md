# auditeur_flux

> Quels flux réseau sont actifs ?

## Comment s'en servir

```
python outils/auditeur_flux.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: auditeur_flux.py [-h] [--json] [--racine RACINE]

Auditeur de flux réseau actifs.

options:
  -h, --help       show this help message and exit
  --json           Émettre la sortie au format JSON unique sur stdout.
  --racine RACINE  Chemin racine à préfixer dans sys.path (par défaut le
                   répertoire du script).

Exemple d’appel réel : python auditeur_flux.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne fonctionne que sur les systèmes Linux disposant de /proc/net ; ne détecte pas les flux HTTP/2/TLS non établis.

## Contre‑exemples

Sur Windows, macOS ou tout système sans /proc, l'outil indique l'impossibilité d'analyse.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
