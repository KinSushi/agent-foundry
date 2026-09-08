# provenance

> OMISE

## Comment s'en servir

```
provenance.py tracer numpy.fft
```

Rend le résultat sur la sortie standard ; `--json` en donne la forme machine.

## Toutes les options

```
usage: provenance.py [-h] [--racine RACINE] {tracer,expliquer,sceau} ...

Trace la provenance complète d'un module Python.

positional arguments:
  {tracer,expliquer,sceau}
    tracer              Trace la provenance complète
    expliquer           Explique pourquoi un module ne fonctionne pas
    sceau               Fige la provenance en JSON

options:
  -h, --help            show this help message and exit
  --racine RACINE       Racine du projet (défaut : répertoire de l'outil)

Exemple : provenance.py tracer numpy.fft
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

Bibliothèque standard seule

---
[← retour à la liste](../README.md)
