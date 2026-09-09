# verifier_signature_jwt

> OMISE

## Comment s'en servir

```
python outils/verifier_signature_jwt.py exemple.py --secret secret --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: verifier_signature_jwt.py --secret SECRET [--json] [--racine RACINE]
                                 token

Vérifie la signature et l'expiration d'un jeton JWT.

positional arguments:
  token            Jeton JWT à vérifier (chaîne contenant trois parties
                   séparées par '.')

options:
  --secret SECRET  Clé secrète utilisée pour la signature HMAC (texte UTF-8)
  --json           Sortie JSON unique sur stdout (résultat brut)
  --racine RACINE  Chemin racine à ajouter au sys.path avant toute importation

Exemple: verifier_signature_jwt.py "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzd
WIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJ
SMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c" --secret monsecret
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

claims (nbf, iat, aud, iss, …) ni les signatures RSA/ECDSA.

## Contre‑exemples

est correcte car l’outil ne supporte que HS256.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

