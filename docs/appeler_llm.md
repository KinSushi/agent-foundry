# appeler_llm

> OMISE

## Comment s'en servir

```
python outils/appeler_llm.py --hors-ligne --prompt "bonjour" --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: appeler_llm.py [-h] [--json] [--racine RACINE] (--prompt PROMPT |
                      --fichier FICHIER) [--modele MODELE] [--cle-api CLE_API]
                      [--url-base URL_BASE] [--timeout TIMEOUT]
                      [--max-tentatives MAX_TENTATIVES] [--hors-ligne]
                      [--en-ligne]

Interroge un fournisseur de LLM et rapporte sa réponse.

options:
  -h, --help            show this help message and exit
  --json                Rend un seul objet JSON sur stdout au lieu du texte
                        humain.
  --racine RACINE       Surcharge la racine (insérée en tête de sys.path).
  --prompt PROMPT       Le prompt à envoyer au LLM.
  --fichier FICHIER     Fichier contenant le prompt (.txt) ou définissant une
                        variable PROMPT (.py).
  --modele MODELE       Nom du modèle à interroger (défaut : gpt-4o-mini).
  --cle-api CLE_API     Clé d'API (défaut : variable d'environnement
                        OPENAI_API_KEY).
  --url-base URL_BASE   URL de base de l'API (défaut :
                        https://api.openai.com/v1).
  --timeout TIMEOUT     Timeout en secondes (défaut : 30).
  --max-tentatives MAX_TENTATIVES
                        Nombre maximum de tentatives (défaut : 3).
  --hors-ligne          Force le mode hors‑ligne (défaut). Aucun appel réseau
                        ne sera effectué.
  --en-ligne            Autorise les appels réseau.

Exemples :
  python appeler_llm.py --fichier prompt.py --modele gpt-4o-mini
  python appeler_llm.py --json --prompt 'Cite 3 villes' --modele mistral-large-latest --url-base https://api.mistral.ai/v1
  python appeler_llm.py --prompt 'Hello' --cle-api sk-xxx --url-base http://localhost:11434/v1 --modele llama3
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

retries manuels. Sans mistral-common, pas de comptage de tokens.

## Contre‑exemples

lisible sur stderr. Une clé absente provoque un message clair.

## Ce qu'il lui faut

backoff, httpx, mistral_common, openai — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)

