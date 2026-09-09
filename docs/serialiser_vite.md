# serialiser_vite

> Peut‑on désérialiser et valider des données de manière sécurisée et performante ?

## Comment s'en servir

```
python outils/serialiser_vite.py structs --format json --input exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: serialiser_vite.py [-h] [--json] [--racine RACINE]
                          {structs,flux,types,schema,generer-schema} ...

Outil de sérialisation rapide avec validation.

positional arguments:
  {structs,flux,types,schema,generer-schema}
    structs             Valider/désérialiser sans schéma explicite.
    flux                Désérialiser un flux JSON/MessagePack en continu.
    types               Encoder/décoder avec types natifs Python.
    schema              Valider TOML/YAML contre un schéma.
    generer-schema      Générer un schéma depuis une structure Python.

options:
  -h, --help            show this help message and exit
  --json                Produit une sortie JSON unique.
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du script.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les attaques par pollution de prototype (ex. : "__proto__" dans JSON).

## Contre‑exemples

Un fichier JSON contenant {"__reduce__": ["os.system", ["rm -rf /"]]} est rejeté, mais {"a": {"__proto__": {"polluted": true}}} peut passer.

## Ce qu'il lui faut

msgspec — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

