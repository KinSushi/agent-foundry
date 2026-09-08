# hasher_mot_de_passe

> Puis-je stocker ce secret de manière sécurisée ?

## Comment s'en servir

```
python hasher_mot_de_passe.py secret.txt --json
```

Sous-commande `cible` : Chemin du fichier contenant le secret.

## Toutes les options

```
usage: hasher_mot_de_passe.py [-h] [--racine RACINE] [--json] cible

Vérifie si un secret stocké dans un fichier est sécurisé.

positional arguments:
  cible            Chemin du fichier contenant le secret.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine à préfixer dans sys.path (défaut :
                   répertoire du script).
  --json           Produit une sortie unique au format JSON sur stdout.

Exemple : python hasher_mot_de_passe.py secret.txt --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Aucun algorithme de dérivation de mot de passe moderne (argon2, bcrypt)
n'est utilisé, uniquement PBKDF2 fourni par la bibliothèque standard.
- Le format du fichier doit correspondre exactement au schéma attendu.
- La sécurité dépend de la robustesse du sel et du nombre d'itérations.

## Contre‑exemples

Un fichier contenant «password» en clair ou un hachage sans sel/itérations sera jugé non sécurisé.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
