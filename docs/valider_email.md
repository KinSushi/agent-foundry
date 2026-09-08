# valider_email

> OMISE

## Comment s'en servir

```
python valider_email.py user@example.com
```

Sous-commande `cible` : L'adresse email ou le chemin du fichier a valider.

## Toutes les options

```
usage: valider_email.py [-h] [--json] [--racine RACINE] cible

Valide une adresse email ou un fichier d'adresses.

positional arguments:
  cible            L'adresse email ou le chemin du fichier a valider.

options:
  -h, --help       show this help message and exit
  --json           Rend un seul objet JSON sur stdout.
  --racine RACINE  Surcharge la racine et l'insere en tete de sys.path.

Exemple: python valider_email.py user@example.com
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

OMISE

## Ce qu'il lui faut

dns, email_validator — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
