# chiffrer_payload

> OMISE

## Comment s'en servir

```
python outils/chiffrer_payload.py chiffrer -i exemple.py -m secret --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: chiffrer_payload.py [-h] [--json] [--racine RACINE]
                           {chiffrer,dechiffrer} ...

Chiffrer ou déchiffrer un payload pour le transporter.

positional arguments:
  {chiffrer,dechiffrer}
    chiffrer            Chiffrer un payload
    dechiffrer          Déchiffrer un payload

options:
  -h, --help            show this help message and exit
  --json                Sortie JSON sur stdout
  --racine RACINE       Racine surchargeant la racine par défaut, insérée en
                        tête de sys.path

Exemple:
  python chiffrer_payload.py chiffrer -i payload.py -m secret
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

OMISE

## Contre‑exemples

Mot de passe vide : refuse.

## Ce qu'il lui faut

cryptography — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
