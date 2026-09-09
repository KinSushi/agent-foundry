# contraindre_sortie

> La sortie du LLM respecte‑t‑elle le format ou le schéma attendu ?

## Comment s'en servir

```
python outils/contraindre_sortie.py detecter-erreurs --format json --fichier exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: contraindre_sortie.py [-h] [--json] [--racine RACINE]
                             {verifier-schema,detecter-erreurs,convertir-objet,imposer-format} ...

Contraindre la sortie d’un LLM à un format structuré.

positional arguments:
  {verifier-schema,detecter-erreurs,convertir-objet,imposer-format}
    verifier-schema     Vérifie qu’un texte JSON respecte un schéma.
    detecter-erreurs    Détecte la première erreur de syntaxe d’un texte.
    convertir-objet     Convertit un texte JSON en objet Python conforme au schéma.
    imposer-format      Réécrit le texte dans le format strict demandé.

options:
  -h, --help            show this help message and exit
  --json                Produit la sortie au format JSON unique.
  --racine RACINE       Chemin racine à utiliser pour les chemins relatifs.

Exemple : {outil} verifier-schema --schema schema.json --texte '{"a":1}' --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Pas de validation de types profonds, pas de correction sémantique, formats non‑JSON supportés uniquement si la bibliothèque tierce est disponible. CONTRE‑EXEMPLES Un JSON valide mais avec une clé manquante est accepté en mode dégradé (validation partielle).

## Ce qu'il lui faut

lmformatenforcer — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

