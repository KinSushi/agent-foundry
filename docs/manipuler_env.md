# manipuler_env

> Quelles variables d'environnement ce processus voit-il ?

## Comment s'en servir

```
python manipuler_env.py --env .env --source app.py --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: manipuler_env.py [-h] [--env ENV] [--source SOURCE] [--racine RACINE]
                        [--json]

Examine les variables d'environnement visibles par le processus.

options:
  -h, --help       show this help message and exit
  --env ENV        Chemin vers un fichier .env a charger.
  --source SOURCE  Fichier source Python a valider.
  --racine RACINE  Racine pour les imports.
  --json           Sortie JSON.

Exemple: python manipuler_env.py --env .env --source app.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne distingue pas l origine (shell vs .env) apres chargement ; ne decrypte aucune valeur.

## Contre‑exemples

Une variable definie dans un .env mais ecrasee par le shell apparait avec la valeur du shell.

## Ce qu'il lui faut

dotenv, pydantic_settings — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

