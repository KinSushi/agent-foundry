"""QUESTION
Comment ce texte se découpe-t-il en tokens ?

MESURE
Le texte fourni est découpé en une séquence de tokens selon la méthode demandée.
Le nombre de tokens réellement produits est indiqué comme « denominateur ».
Les tokens examinés sont listés dans « examines » (max 200, sinon tronqués).

HYPOTHESES
- Si la bibliothèque tierce « tokenizers » est disponible et qu’un fichier de
  tokenizer valide est fourni, il sera utilisé.
- Sinon, une tokenisation simple basée sur les expressions régulières est appliquée.
- Le texte est décodé en UTF‑8.

LIMITES
- Aucun modèle BPE/WordPiece n’est entraîné ici ; la tokenisation avancée dépend
  de la présence d’un tokenizer pré‑entraîné compatible.
- La fonction ne supporte que les entrées de type str.

CONTRE-EXEMPLES
- Un texte vide ou non‑string entraîne un refus de conclusion (denominateur = 0).

DOMAINE
Cet outil s’applique à la tokenisation de textes courts en français ou en anglais,
dans le cadre d’analyses linguistiques ou de pré‑traitement de données.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Encodage UTF‑8 pour la console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent
__all__ = ["tokeniser_texte", "analyse_texte"]


def _charger_tokenizer_tertiary(path: str) -> Any:
    """Essaye de charger un tokenizer depuis le chemin indiqué en utilisant la
    bibliothèque « tokenizers ». Retourne None si la bibliothèque n’est pas
    disponible ou si le chargement échoue."""
    try:
        from tokenizers import Tokenizer  # type: ignore
    except Exception as e:
        print(
            f"Info : la bibliothèque tierce « tokenizers » n’est pas disponible – {e}",
            file=sys.stderr,
        )
        return None

    try:
        tokenizer = Tokenizer.from_file(path)
        return tokenizer
    except Exception as e:
        print(
            f"Erreur : impossible de charger le tokenizer depuis « {path} » – {e}",
            file=sys.stderr,
        )
        return None


def tokeniser_texte(texte: str, tokenizer_path: Optional[str] = None) -> List[str]:
    """Retourne la liste des tokens du texte.

    Si *tokenizer_path* est fourni et que la bibliothèque « tokenizers » est
    disponible, le tokenizer est utilisé. Sinon, une tokenisation simple basée
    sur les expressions régulières est appliquée.
    """
    if not isinstance(texte, str):
        raise TypeError("Le texte à tokeniser doit être une chaîne de caractères.")

    if tokenizer_path:
        tokenizer = _charger_tokenizer_tertiary(tokenizer_path)
        if tokenizer is not None:
            try:
                encoding = tokenizer.encode(texte)
                return encoding.tokens  # type: ignore[attr-defined]
            except Exception as e:
                print(
                    f"Info : le tokenizer tiers a échoué – {e}. Repli sur tokenisation simple.",
                    file=sys.stderr,
                )

    # Fallback : tokenisation simple (mots + ponctuation)
    return re.findall(r"\w+|[^\s\w]", texte, flags=re.UNICODE)


def analyse_texte(
    texte: str,
    tokenizer_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse le texte et renvoie le résultat structuré.

    Le dictionnaire retourné contient :
        - « denominateur » : nombre total de tokens.
        - « examines » : liste (max 200) des tokens.
        - « examines_tronques » : booléen indiquant si la liste a été tronquée.
    """
    tokens = tokeniser_texte(texte, tokenizer_path)

    denominateur = len(tokens)
    examines = tokens[:200]
    examines_tronques = len(tokens) > 200

    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
    }


def _contrat() -> Dict[str, str]:
    """Construit le contrat de mesure à insérer dans la sortie JSON."""
    doc = __doc__
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    for line in doc.splitlines():
        line = line.strip()
        if line in {
            "QUESTION",
            "MESURE",
            "HYPOTHESES",
            "LIMITES",
            "CONTRE-EXEMPLES",
            "DOMAINE",
        }:
            current = line
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    for k in sections:
        sections[k] = sections[k].strip()
    return sections


def _sortie_json(resultat: Dict[str, Any]) -> str:
    """Produit le JSON complet incluant le contrat."""
    payload = {
        "contrat": _contrat(),
        "denominateur": resultat["denominateur"],
        "examines": resultat["examines"],
        "examines_tronques": resultat["examines_tronques"],
    }
    return json.dumps(payload, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tokenise un texte et indique comment il se découpe en tokens.",
        epilog="Exemple : python tokeniser_texte.py \"Bonjour le monde!\" --json",
    )
    parser.add_argument(
        "texte",
        help="Le texte à analyser.",
    )
    parser.add_argument(
        "--tokenizer-path",
        help="Chemin vers un fichier de tokenizer compatible avec la bibliothèque « tokenizers ».",
    )
    parser.add_argument(
        "--racine",
        help="Chemin racine à préfixer dans sys.path (remplace la valeur par défaut).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout.",
    )

    args = parser.parse_args()

    # Gestion du paramètre --racine
    if args.racine:
        racine_path = Path(args.racine).resolve()
        if not racine_path.is_dir():
            print(
                f"Erreur : le chemin fourni à --racine n’est pas un répertoire valide : {racine_path}",
                file=sys.stderr,
            )
            return 2
        sys.path.insert(0, str(racine_path))

    # Validation du texte
    if not isinstance(args.texte, str):
        print(
            "Erreur : le texte fourni n’est pas une chaîne de caractères.",
            file=sys.stderr,
        )
        return 2

    try:
        resultat = analyse_texte(args.texte, args.tokenizer_path)
    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 2

    denom = resultat["denominateur"]
    if denom == 0:
        print("Refus de conclure : aucun token n’a été produit.", file=sys.stderr)
        return 3

    if args.json:
        print(_sortie_json(resultat))
        return 0
    else:
        print(f"Denominateur (nombre de tokens) : {denom}")
        print("Tokens examinés :")
        for token in resultat["examines"]:
            print(f"- {token}")
        if resultat["examines_tronques"]:
            print("… (liste tronquée)", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())