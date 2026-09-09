# publier_vitrine

> Publier une vitrine dont aucun chiffre ne peut se périmer.

## Comment s'en servir

```
python python outils/publier_vitrine.py engendrer ./sortie --racine . --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: publier_vitrine.py [-h] [--racine RACINE] [--json]
                          [--image-digest IMAGE_DIGEST] [--titre TITRE]
                          [--sous-titre SOUS_TITRE] [--depot-url DEPOT_URL]
                          [--isolation ISOLATION] [--porte PORTE]
                          [--branche BRANCHE] [--licence LICENCE]
                          [--auteur AUTEUR] [--copyright COPYRIGHT_TEXT]
                          [--banniere BANNIERE]
                          {engendrer} ...

Génère une vitrine complète à partir de mesures.

positional arguments:
  {engendrer}
    engendrer           Produit la vitrine dans le répertoire cible.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Répertoire racine contenant les artefacts (défaut : répertoire du script).
  --json                Émettre un unique objet JSON sur stdout.
  --image-digest IMAGE_DIGEST
                        Empreinte SHA256 de l’image Python à utiliser dans le Dockerfile.
  --titre TITRE         Titre de la bannière (défaut : nom du dossier racine).
  --sous-titre SOUS_TITRE
                        Sous‑titre de la bannière (défaut : phrase neutre basée sur le nombre d’outils).
  --depot-url DEPOT_URL
                        URL du dépôt à placer dans [project.urls]; si absent, la section est omise.
  --isolation ISOLATION
                        Chemin vers un rapport d’isolation (peut être répété).
  --porte PORTE         Chemin vers le rapport de la porte de qualité (défaut : artefacts/porte_102.json).
  --branche BRANCHE     Nom de la branche pour l'URL raw GitHub (défaut: main).
  --licence LICENCE     Chemin vers le fichier de licence à utiliser (défaut : artefacts/AGPL-3.0.txt si valide).
  --auteur AUTEUR       Nom de l'auteur ou de la société pour le copyright (défaut : nom du projet).
  --copyright COPYRIGHT_TEXT
                        Texte complet de la ligne de copyright juridique (exact, sans aucun préfixe).
  --banniere BANNIERE   Chemin vers une bannière PNG fournie (remplace la génération automatique).
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Pas de mesures de type dynamique, uniquement ce qui est sur le disque.

## Contre‑exemples

Un README dont les chiffres sont tapés à la main se périme au premier commit.

## Ce qu'il lui faut

pymupdf — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

