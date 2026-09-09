# environnement_node

> Peut-on exécuter un script npm dans un environnement Node isolé sans polluer le Python ?

## Comment s'en servir

```
python outils/environnement_node.py create ./proj --node-version 20.9.0 --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: environnement_node.py [-h] [--racine RACINE] [--json]
                             {create,install-node,npm-install,npm-run,check-isolation} ...

Gère un environnement Node.js isolé pour exécuter des scripts npm.

positional arguments:
  {create,install-node,npm-install,npm-run,check-isolation}
    create              Crée un environnement Node isolé.
    install-node        Installe ou met à jour Node.js dans l'environnement
                        existant.
    npm-install         Installe un paquet npm dans l'environnement.
    npm-run             Exécute un script npm dans l'environnement.
    check-isolation     Vérifie que l'environnement Node n'a pas modifié les
                        variables d'environnement Python.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Répertoire racine pour les opérations (défaut:
                        répertoire du script)
  --json                Sortie au format JSON

Exemple d'appel :
  environnement_node.py create --racine ./mon_projet --node-version 20.9.0
  environnement_node.py npm-run --racine ./mon_projet --script build
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

L’outil ne détecte pas les effets indirects (ex. : fichiers temporaires créés par le script).

## Contre‑exemples

Un script npm qui modifie `process.env.PYTHONPATH` persiste après la fin du processus Node, ce qui fait échouer `check-isolation`.

## Ce qu'il lui faut

nodeenv — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

