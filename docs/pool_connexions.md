# pool_connexions

> Comment optimiser les connexions HTTP pour réduire la latence et les ressources ?

## Comment s'en servir

```
python outils/pool_connexions.py analyser exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: pool_connexions.py [-h] [--json] [--racine RACINE]
                          {réutiliser,retry,stream,multipart,ssl-personnalisé,décompresser,analyser} ...

Optimise les connexions HTTP pour réduire la latence et les ressources.

positional arguments:
  {réutiliser,retry,stream,multipart,ssl-personnalisé,décompresser,analyser}
    réutiliser          Réutilise les connexions HTTP pour réduire le nombre
                        de sockets ouvertes.
    retry               Effectue des tentatives de connexion avec retries sur
                        erreurs spécifiques.
    stream              Télécharge un fichier en flux continu pour limiter
                        l'usage mémoire.
    multipart           Envoie un formulaire multipart avec champs et
                        fichiers.
    ssl-personnalisé    Vérifie un certificat SSL avec un bundle CA
                        personnalisé.
    décompresser        Détecte et décompresse le contenu encodé (gzip,
                        deflate).
    analyser            Analyse un fichier local sans ouvrir de connexion
                        réseau.

options:
  -h, --help            show this help message and exit
  --json                Rendre le résultat au format JSON sur stdout.
  --racine RACINE       Racine pour les chemins relatifs (défaut: répertoire
                        du script).
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne mesure pas la bande passante réseau ni la charge CPU du serveur.

## Contre‑exemples

Un serveur qui ferme les connexions après 1 requête (HTTP/1.0) fera échouer `réutiliser`.

## Ce qu'il lui faut

urllib3 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

