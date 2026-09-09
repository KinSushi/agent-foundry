# traducteur_automatique

> Comment traduire ce texte sans API externe ?

## Comment s'en servir

```
traducteur_automatique.py "Bonjour" --lang fr --domain monapp
```

Sous-commande `texte` : Texte à traduire.

## Toutes les options

```
usage: traducteur_automatique.py [-h] [--racine RACINE] [--json]
                                 [--domain DOMAIN] [--localedir LOCALEDIR]
                                 [--lang LANGUES]
                                 texte

Traduit un texte en utilisant les catalogues gettext locaux, sans appel à une
API externe.

positional arguments:
  texte                 Texte à traduire.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Chemin racine du projet (remplace la valeur par
                        défaut).
  --json                Produit la sortie au format JSON unique sur stdout.
  --domain DOMAIN       Nom du domaine gettext (défaut : messages).
  --localedir LOCALEDIR
                        Répertoire contenant les sous‑répertoires de langues
                        (ex : ./locale).
  --lang LANGUES        Code(s) langue(s) à utiliser (ex : fr, en). Peut être
                        répété.

Exemple : traducteur_automatique.py "Bonjour" --lang fr --domain monapp
--localedir ./locale
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Sans catalogue .mo, aucune traduction n’est possible ; le script ne recourt à aucune API externe.

## Contre‑exemples

Un texte fourni alors qu’aucun fichier .mo n’est présent ne sera pas traduit.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

