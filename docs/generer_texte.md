# generer_texte

> Comment produire ce texte à partir d’un modèle ?

## Comment s'en servir

```
python outils/generer_texte.py --modele exemple.py --template exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: generer_texte.py [-h] --modele MODELE --template TEMPLATE
                        [--racine RACINE] [--json]

Génère du texte à partir d’un modèle et d’un template.

options:
  -h, --help           show this help message and exit
  --modele MODELE      Chemin vers le fichier contenant le modèle (JSON ou
                       dict Python).
  --template TEMPLATE  Chemin vers le fichier contenant le template.
  --racine RACINE      Chemin racine à insérer en tête de sys.path (par défaut
                       le répertoire du script).
  --json               Émettre la sortie sous forme d’un unique objet JSON sur
                       stdout.

Exemple : python generer_texte.py --modele data.json --template tmpl.txt
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Sans jinja2, seules les substitutions via str.format sont supportées. Aucun sandbox complet n’est fourni si RestrictedPython est absent.

## Contre‑exemples

Un modèle binaire ou non‑lisible déclenche une erreur claire. Un template contenant des constructions Jinja2 avancées échoue sans jinja2.

## Ce qu'il lui faut

jinja2 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
