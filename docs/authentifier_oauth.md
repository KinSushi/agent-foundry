# authentifier_oauth

> Comment obtenir un jeton d’accès pour ce service ?

## Comment s'en servir

```
python outils/authentifier_oauth.py --client-id test --authority https://login.microsoftonline.com/common --scope User.Read --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: authentifier_oauth.py [-h] [--client-id CLIENT_ID]
                             [--authority AUTHORITY] [--scope SCOPE]
                             [--redirect-uri REDIRECT_URI] [--cache CACHE]
                             [--racine RACINE] [--json]

Obtenir un jeton d’accès OAuth2 via MSAL.

options:
  -h, --help            show this help message and exit
  --client-id CLIENT_ID
                        Identifiant du client (application).
  --authority AUTHORITY
                        URL de l’autorité (ex.
                        https://login.microsoftonline.com/common).
  --scope SCOPE         Scope à demander (peut être répété).
  --redirect-uri REDIRECT_URI
                        URI de redirection (facultatif).
  --cache CACHE         Chemin du fichier de cache token (facultatif).
  --racine RACINE       Chemin racine à préfixer dans sys.path (remplace le
                        répertoire du script).
  --json                Sortie JSON unique.

Exemple : python authentifier_oauth.py --client-id <id> --authority
https://login.microsoftonline.com/common --scope User.Read
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Sans bibliothèque tierce (msal) le flux complet n’est pas implémenté.

## Contre‑exemples

Un appel sans client_id ou authority ne peut aboutir.

## Ce qu'il lui faut

msal, msal_extensions — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

