# atelier_document

> que dit ce document, et où exactement le dit-il ?

## Comment s'en servir

```
python outils/atelier_document.py indexer --racine . --db ./index.db --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
warning: The `fitz` API is deprecated and will be removed in future. Use `import pymupdf` instead.
usage: atelier_document.py [-h] {indexer,chercher,perimees} ...

Indexe et recherche du texte positionné dans les PDF.

positional arguments:
  {indexer,chercher,perimees}
    indexer             Construire ou mettre à jour l'index.
    chercher            Rechercher un terme dans l'index.
    perimees            Vérifier la validité des PDF déjà indexés.

options:
  -h, --help            show this help message and exit

Exemple : python atelier_document.py indexer --racine ./docs --db ./index.db
--json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

un PDF scanné rend 0 caractère — aucun OCR disponible ici ; la détection de tables est limitée, la bibliothèque le dit elle‑même ; pymupdf porte un binaire, donc NON embarquable

## Contre‑exemples

un extrait sans position ne se vérifie pas — c'est la faute que cet outil corrige, mesurée à 37 citations fausses sur 39

## Ce qu'il lui faut

fitz, fsspec, zstandard — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

