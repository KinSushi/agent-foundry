# json_rapide

> Quelle est la performance et la compatibilité de `json_rapide` pour manipuler du JSON en production ?

## Comment s'en servir

```
python outils/json_rapide.py serialiser '{"exemple": 1}' --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: json_rapide.py [-h]
                      {serialiser,deserialiser,encoder_types,reseau,compact} ...

Outil rapide de manipulation JSON.

positional arguments:
  {serialiser,deserialiser,encoder_types,reseau,compact}
    serialiser          Sérialiser un objet JSON.
    deserialiser        Désérialiser un fichier JSON ou stdin.
    encoder_types       Encoder les types natifs supportés.
    reseau              Préparer le JSON pour envoi réseau.
    compact             Compacte le JSON avec options.

options:
  -h, --help            show this help message and exit

Exemple : json_rapide serialiser '{"a":1}' --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne gère pas les flux binaires non JSON (ex: Protobuf).
- En mode dégradé, les types natifs lèvent `TypeError`.

## Contre‑exemples

Un objet contenant un `set` Python : `orjson` lève `TypeError` (non sérialisable).

## Ce qu'il lui faut

orjson — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

