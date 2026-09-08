# tokeniser_texte

> Comment ce texte se découpe-t-il en tokens ?

## Comment s'en servir

```
python tokeniser_texte.py "Bonjour le monde!" --json
```

Sous-commande `texte` : Le texte à analyser.

## Toutes les options

```
usage: tokeniser_texte.py [-h] [--tokenizer-path TOKENIZER_PATH]
                          [--racine RACINE] [--json]
                          texte

Tokenise un texte et indique comment il se découpe en tokens.

positional arguments:
  texte                 Le texte à analyser.

options:
  -h, --help            show this help message and exit
  --tokenizer-path TOKENIZER_PATH
                        Chemin vers un fichier de tokenizer compatible avec la
                        bibliothèque « tokenizers ».
  --racine RACINE       Chemin racine à préfixer dans sys.path (remplace la
                        valeur par défaut).
  --json                Émettre la sortie au format JSON unique sur stdout.

Exemple : python tokeniser_texte.py "Bonjour le monde!" --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

- Aucun modèle BPE/WordPiece n’est entraîné ici ; la tokenisation avancée dépend
de la présence d’un tokenizer pré‑entraîné compatible.
- La fonction ne supporte que les entrées de type str.

## Contre‑exemples

- Un texte vide ou non‑string entraîne un refus de conclusion (denominateur = 0).

## Ce qu'il lui faut

tokenizers — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
