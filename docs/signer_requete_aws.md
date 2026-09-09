# signer_requete_aws

> L'outil signe‑t‑il correctement une requête AWS SigV4 ?

## Comment s'en servir

```
python outils/signer_requete_aws.py auth exemple.py --service s3 --region us-east-1 --access-key AKIATEST --secret-key testsecret --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: signer_requete_aws.py [-h] [--json] [--racine RACINE]
                             {client,auth,args,awsrequest,error} ...

Outil de signature et d'appel AWS (SigV4).

positional arguments:
  {client,auth,args,awsrequest,error}
    client              Appel dynamique via botocore
    auth                Signature d'une requête brute
    args                Validation des paramètres
    awsrequest          Envoi d'une requête HTTP AWS
    error               Analyse d'une réponse d'erreur AWS

options:
  -h, --help            show this help message and exit
  --json                Produit la sortie au format JSON unique
  --racine RACINE       Chemin racine du projet (défaut : répertoire du
                        script)

Exemple : signer_requete_aws.py client --service s3 --action ListBuckets
--region us-east-1 --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

L'outil ne vérifie pas la validité du secret (pas d'appel AWS réel).

## Contre‑exemples

Une clé expirée produit une signature qui passe la mesure mais est rejetée par le service.

## Ce qu'il lui faut

botocore — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

