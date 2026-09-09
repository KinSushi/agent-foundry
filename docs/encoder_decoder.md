# encoder_decoder

> Comment convertir ces données entre formats binaires/textuels ?

## Comment s'en servir

```
python outils/encoder_decoder.py encoder --base64 --donnees test --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: encoder_decoder.py [-h] [--racine RACINE] [--json]
                          {encoder,decoder,uuid} ...

Outil de conversion entre formats binaires et textuels

positional arguments:
  {encoder,decoder,uuid}
    encoder             Encoder des données
    decoder             Décoder des données
    uuid                Générer un UUID

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine à utiliser (surcharge la racine par
                        défaut)
  --json                Sortie au format JSON

Exemple: encoder_decoder.py encoder --base64 --donnees 'test' --optimise
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

UUID v7 natif en Python 3.14 est lent, pas de UUID v8 natif

## Contre‑exemples

UUID v7 généré avec random() peut avoir des collisions

## Ce qu'il lui faut

fastuuid, pybase64 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

