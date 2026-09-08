# ouvrir_partout

> Puis‑je lire ce dépôt sans le poser sur le disque ?

## Comment s'en servir

```
python outils/ouvrir_partout.py fichiers 'file://.' --json
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: ouvrir_partout.py [-h] [--racine RACINE] [--json]
                         {protocoles,fichiers,copier} ...

Accès universel aux fichiers via fsspec.

positional arguments:
  {protocoles,fichiers,copier}
    protocoles          Lister les protocoles disponibles et leur état.
    fichiers            Lister les fichiers d’une source.
    copier              Copier les fichiers d’une source.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script.
  --json                Émettre la sortie au format JSON unique.

Exemple : python outils/ouvrir_partout.py fichiers 'file://.' --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Un chemin fsspec n’est pas un pathlib.Path ; les protocoles distants ajoutent une latence réseau NON mesurée ici ; certains protocoles ne fournissent ni taille ni date.

## Contre‑exemples

`available_protocols()` liste 55 noms mais un protocole listé dont le paquet manque échoue à l’ouverture — « listé » ≠ « réel ».

## Ce qu'il lui faut

fsspec — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
