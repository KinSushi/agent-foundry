# resoudre_noms

> Quelle est l'adresse réelle de ce nom, et par quel chemin DNS ?

## Comment s'en servir

```
resoudre_noms.py example.com
```

Sous-commande `noms` : Nom(s) de domaine ou chemin de fichier à analyser (exemple : example.com).

## Toutes les options

```
usage: resoudre_noms.py [-h] [--json] [--racine RACINE] [--online]
                        noms [noms ...]

Résout un nom d'hôte en adresse IP et indique le chemin DNS utilisé.

positional arguments:
  noms             Nom(s) de domaine ou chemin de fichier à analyser (exemple : example.com)

options:
  -h, --help       show this help message and exit
  --json           Sortie JSON au lieu du format lisible
  --racine RACINE  Surcharge la racine du projet et l'ajoute au sys.path
  --online         Autorise les accès réseau (désactivé par défaut)

Exemple : resoudre_noms.py example.com
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

Aucun enregistrement MX, TXT, DNSSEC ou autre type que A/AAAA n'est interrogé. Sans dnspython, le chemin DNS exact (serveurs interrogés) ne peut être connu.

## Contre‑exemples

Un nom qui ne possède que des enregistrements MX (pas d'A/AAAA) sera considéré comme non résolu alors qu'il est valide pour la messagerie.

## Ce qu'il lui faut

dns, idna — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

