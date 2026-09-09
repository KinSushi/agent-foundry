# duree_iso

> La chaîne fournie est‑elle une date/heure/durée ISO 8601 valide ?

## Comment s'en servir

```
python outils/duree_iso.py isoerror exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: duree_iso.py [-h] [--json] [--racine RACINE] [--version]
                    {isoerror,isostrf} ...

Outil de validation et de formatage ISO 8601.

positional arguments:
  {isoerror,isostrf}
    isoerror          valider une chaîne ISO 8601
    isostrf           formater un datetime en ISO 8601

options:
  -h, --help          show this help message and exit
  --json              produire la sortie au format JSON
  --racine RACINE     chemin racine à utiliser (défaut: répertoire du script)
  --version           show program's version number and exit

Exemple : python duree_iso.py isoerror 2023-07-15T13:45:30Z --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Le fallback ne détecte pas les fuseaux horaires « Z » avec précision et ne supporte pas les intervalles complexes.

## Contre‑exemples

Chaîne « 2023-13-01T00:00:00Z » (mois 13) : `isodate` signale « Invalid month », le fallback accepte ! → erreur.

## Ce qu'il lui faut

isodate — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

