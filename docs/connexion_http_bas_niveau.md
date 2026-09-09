# connexion_http_bas_niveau

> L'outil permet-il d'effectuer des requêtes HTTP sans bloquer l'agent ?

## Comment s'en servir

```
python outils/connexion_http_bas_niveau.py envoyer_non_bloquant exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: connexion_http_bas_niveau.py [-h] [--json] [--racine RACINE]
                                    {envoyer_non_bloquant,gerer_connexions_simultanees,reutiliser_connexion,surveiller_requete} ...

Outil de connexion HTTP bas niveau non bloquant.

positional arguments:
  {envoyer_non_bloquant,gerer_connexions_simultanees,reutiliser_connexion,surveiller_requete}
    envoyer_non_bloquant
                        Lance une requête HTTP en arrière-plan et retourne un
                        identifiant.
    gerer_connexions_simultanees
                        Gère plusieurs connexions simultanées.
    reutiliser_connexion
                        Réutilise une seule connexion pour plusieurs requêtes.
    surveiller_requete  Surveille l'état d'une requête en cours.

options:
  -h, --help            show this help message and exit
  --json                Sortie au format JSON (un seul objet)
  --racine RACINE       Racine pour les chemins relatifs (défaut: répertoire
                        du script)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne mesure pas la latence réseau ou la bande passante.
- En mode dégradé, le parallélisme est limité par le GIL.

## Contre‑exemples

Un serveur répondant en 5 secondes avec un corps de 10 Mo : en mode dégradé, asyncio.gather() peut saturer la mémoire si trop de requêtes sont lancées simultanément.

## Ce qu'il lui faut

httpcore2 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

