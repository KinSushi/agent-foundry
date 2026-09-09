# publier_mesures

> Quel(s) type(s) de métriques Prometheus peut‑on exposer, agréger ou mesurer dans un processus Python ?

## Comment s'en servir

```
python outils/publier_mesures.py mesurer --nom duree_tache --fichier exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: publier_mesures.py [-h] [--json] [--racine RACINE]
                          {exposer,agreger,aiohttp,mesurer} ...

Outil de publication de métriques Prometheus.

positional arguments:
  {exposer,agreger,aiohttp,mesurer}
    exposer             Expose les métriques via HTTP.
    agreger             Agrège plusieurs registres Prometheus.
    aiohttp             Intègre les métriques à une application aiohttp.
    mesurer             Génère un contexte managé pour mesurer du code.

options:
  -h, --help            show this help message and exit
  --json                Produit la sortie au format JSON unique.
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script.

Exemple d’appel réel : publier_mesures.py mesurer --nom duree_tache --fichier
script.py --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Aucun support réseau natif sans la bibliothèque tierce `prometheus_client`.
- L’agrégation se limite à la concaténation de fichiers texte.

## Contre‑exemples

Un registre contenant des caractères NUL (`\0`) est considéré binaire et déclenche un refus.

## Ce qu'il lui faut

prometheus_client — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

