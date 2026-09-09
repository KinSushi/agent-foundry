# auditeur_permissions

> Quels fichiers ont des permissions dangereuses ?

## Comment s'en servir

```
python outils/auditeur_permissions.py --racine . --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: auditeur_permissions.py [-h] [--racine RACINE] [--json]

Liste les fichiers dont les permissions sont considérées dangereuses.
Exemple d'appel : python auditeur_permissions.py --racine /home/utilisateur

options:
  -h, --help       show this help message and exit
  --racine RACINE  Répertoire racine à analyser (par défaut : répertoire du
                   script).
  --json           Sortie JSON unique sur stdout.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

L'outil ne vérifie pas les ACL étendues, les capacités, ni les restrictions spécifiques à SELinux/AppArmor ; il ne examine pas les systèmes de fichiers réseau pouvant masquer les vrais propriétaires.

## Contre‑exemples

Un fichier possédant le bit SUID mais appartenant à un compte système légitime (comme /usr/bin/passwd) est considéré dangereux par l'outil bien que son usage soit attendu.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

