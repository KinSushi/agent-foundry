# objets_amazon

> Capacité à interagir avec les services AWS sans écrire de code bas‑niveau.

## Comment s'en servir

```
python outils/objets_amazon.py lister_s3 --bucket test --json exemple.py
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: objets_amazon.py [-h] [--json] [--racine RACINE] {lister_s3} ...

Outil d'interaction simplifiée avec les services AWS.

positional arguments:
  {lister_s3}
    lister_s3      Lister les objets d’un bucket S3 (pagination).

options:
  -h, --help       show this help message and exit
  --json           Retourner la sortie sous forme d'un unique objet JSON.
  --racine RACINE  Chemin racine à utiliser à la place du répertoire du
                   script.
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Ne gère pas les erreurs réseau imprévisibles (ex : timeout).
- Ne valide pas les permissions IAM en amont.
CONTRE‑EXEMPLES Un bucket S3 inexistant → erreur 404, mais l’outil ne vérifie pas son existence avant de générer l’URL.

## Ce qu'il lui faut

boto3 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

