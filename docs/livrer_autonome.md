# livrer_autonome

> cet outil peut-il voyager en un seul fichier ?

## Comment s'en servir

```
python -m outils.livrer_autonome emballer mon_script.py --racine .
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: livrer_autonome [-h] [--racine RACINE] [--json]
                       {emballer,carte,verifier,eprouver} ...

Emballage autonome d'un script Python en zipapp.

positional arguments:
  {emballer,carte,verifier,eprouver}
    emballer            Emballer un script Python en zipapp.
    carte               Affiche la liste des paquets embarquables / non
                        embarquables.
    verifier            Vérifie l'exécution d'une archive zipapp.
    eprouver            Importe chaque module depuis l'archive via zipimport
                        et vérifie qu'aucun fichier n'est écrit sur disque.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine à utiliser à la place du répertoire du
                        script.
  --json                Produit la sortie au format JSON unique.

Exemple : python -m outils.livrer_autonome emballer mon_script.py --racine .
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

ne voit pas les imports dynamiques ; ne teste pas la machine cible

## Contre‑exemples

une archive peut se construire et ne pas s'exécuter — d'où la vérification d'exécution obligatoire

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

