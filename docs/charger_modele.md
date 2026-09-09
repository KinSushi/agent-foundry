# charger_modele

> Quel modèle est disponible localement ?

## Comment s'en servir

```
python outils/charger_modele.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: charger_modele.py [-h] [--racine RACINE] [--json]

Liste les modèles disponibles localement.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine à scanner pour les modèles. Par défaut,
                   utilise les caches standard.
  --json           Affiche le résultat au format JSON sur stdout.

Exemple: python charger_modele.py --racine /mon/dossier --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les modèles qui ne suivent pas la structure standard ou qui sont partiellement téléchargés.

## Contre‑exemples

Un répertoire sans fichiers caractéristiques n'est pas considéré comme un modèle.

## Ce qu'il lui faut

huggingface_hub, transformers — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

