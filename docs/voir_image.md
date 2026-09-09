# voir_image

> Que montre cette image, et où sont les objets/contours ?

## Comment s'en servir

```
python outils/voir_image.py voir --image exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: voir_image.py [-h] [--racine RACINE] [--json] {voir,comparer} ...

Outil d'analyse d'images pour répondre à la question : Que montre cette image,
et où sont les objets/contours ?

positional arguments:
  {voir,comparer}
    voir           Analyse une image et décrit ses contours.
    comparer       Compare deux images et indique les différences.

options:
  -h, --help       show this help message and exit
  --racine RACINE  Chemin racine du projet (préfixe du sys.path).
  --json           Produit une sortie JSON unique sur stdout.

Exemple : python voir_image.py voir --image chemin/vers/image.png
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Pas de détection si OpenCV absent, ou image non lisible, ou très floue.

## Contre‑exemples

Une image monochrome sans contraste ne produit aucun contour.

## Ce qu'il lui faut

cv2 — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

