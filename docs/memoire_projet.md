# memoire_projet

> ce que je crois savoir de ce projet est-il encore vrai ?

## Comment s'en servir

```
python outils/memoire_projet.py --racine . noter 'Décision :
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: memoire_projet.py [-h] [--racine RACINE] [--json] [--base BASE]
                         {noter,chercher,perimees,oublier} ...

Mémoire de projet scellée à son contexte (FTS5 + zlib + sha256).

positional arguments:
  {noter,chercher,perimees,oublier}
    noter               Ajouter une entrée
    chercher            Rechercher un terme
    perimees            Vérifier les entrées périmées
    oublier             Supprimer une entrée

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du projet (défaut : répertoire de l'outil)
  --json                Sortie JSON sur stdout
  --base BASE           Chemin de la base SQLite (défaut : memoire.db)

Exemple : python outils/memoire_projet.py --racine . noter 'Décision :
utiliser FTS5' --fichiers src/main.py
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

ne détecte pas qu'une entrée était FAUSSE dès l'écriture ; ne voit pas un changement dans un fichier non déclaré ; la recherche de termes utilise re.search (faux positifs possibles)

## Contre‑exemples

une entrée qui ne déclare aucun fichier est classée comme NON VÉRIFIABLE (et non VALIDE)

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

