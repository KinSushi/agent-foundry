# protocole_http_brut

> Le message HTTP est‑il strictement conforme à la RFC 7230 ?

## Comment s'en servir

```
python outils/protocole_http_brut.py valider exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: protocole_http_brut.py [-h] [--json] [--racine RACINE]
                              {client,serveur,valider,flux} ...

Analyse et manipulation de messages HTTP/1.1 bruts

positional arguments:
  {client,serveur,valider,flux}
    client              Envoie une requête HTTP brute
    serveur             Lance un serveur HTTP brut
    valider             Valide un message HTTP brut
    flux                Analyse un flux HTTP brut

options:
  -h, --help            show this help message and exit
  --json                Produit un objet JSON sur stdout
  --racine RACINE       Redéfinit la racine du projet
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne détecte pas les violations dépendant du contexte d’application (exigences de sécurité spécifiques). CONTRE‑EXEMPLE Un message contenant `Transfer‑Encoding: chunked` suivi d’un corps non‑hexadécimal déclenche une fausse conformité : l’outil signale « aucune erreur » alors que le flux est invalide.

## Ce qu'il lui faut

h11 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

