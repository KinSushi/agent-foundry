# valider_schema_json

> Le JSON cible respecte-t-il le schéma donné ?

## Comment s'en servir

```
python outils/valider_schema_json.py valider_flux exemple.py --schema exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: valider_schema_json.py [-h] [--json] [--racine RACINE]
                              {valider_flux,valider_ref,verifier_conformite,valider_contrainte} ...

Outil de validation JSON selon différents modes.

positional arguments:
  {valider_flux,valider_ref,verifier_conformite,valider_contrainte}
    valider_flux        validation syntaxique uniquement
    valider_ref         validation avec résolution de $ref
    verifier_conformite
                        vérifier la conformité du schéma à un draft
    valider_contrainte  validation avec contraintes personnalisées

options:
  -h, --help            show this help message and exit
  --json                produire une sortie machine au format JSON unique
  --racine RACINE       chemin racine à utiliser pour les chemins relatifs

Exemples d'appel:
  {outil} valider_flux --schema schema.json --cible data.json --json
  {outil} valider_ref --schema schema.json --cible data.json --resolver file:///chemin/absolu
  {outil} verifier_conformite --schema schema.json --draft 7
  {outil} valider_contrainte --schema schema.json --cible data.json --format format_module.py
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les erreurs de logique métier hors des contraintes déclarées.

## Contre‑exemples

Un schéma avec "type": "integer" accepte 1.0 (float) car jsonschema tolère les conversions implicites.

## Ce qu'il lui faut

jsonschema — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

