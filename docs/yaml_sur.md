# yaml_sur

> Peut-on manipuler du YAML sans perdre de métadonnées (types, commentaires, flux) ?

## Comment s'en servir

```
python outils/yaml_sur.py lire ./fichier.yaml --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: yaml_sur.py [-h] [--json] [--racine RACINE] {lire,ecrire,stream} ...

Manipulation de fichiers YAML en préservant les types natifs, commentaires et
flux.

positional arguments:
  {lire,ecrire,stream}
    lire                Lire un fichier YAML en préservant les types natifs
    ecrire              Générer du YAML formaté avec commentaires et
                        indentation
    stream              Parser du YAML en streaming depuis un fichier ou stdin

options:
  -h, --help            show this help message and exit
  --json                Sortie en JSON
  --racine RACINE       Racine pour les chemins relatifs (défaut: répertoire
                        de l'outil)
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Ne corrige pas les erreurs de syntaxe YAML. Ne gère pas les schémas ou validations avancées.

## Contre‑exemples

Un fichier YAML avec des ancres circulaires peut bloquer `yaml.safe_load`.

## Ce qu'il lui faut

yaml — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

