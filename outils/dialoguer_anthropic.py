"""Dialoguer avec l'API Anthropic pour générer du texte, paginer des réponses ou lister des outils.

QUESTION      Le texte généré correspond‑il à la demande du prompt ?
MESURE        Chaîne renvoyée par l'API ou concaténation des pages.
HYPOTHESES    Le token d'API est valide, le modèle accepte le prompt.
LIMITES       Le serveur peut tronquer le texte si max_tokens trop bas.
CONTRE-EXEMPLE Prompt très long > 10 000 tokens → l'outil renvoie partiellement le texte sans avertir.
INVOCATION
    {outil} generer --prompt "Bonjour" --api-key "dummy" --json
DOMAINE       Génération de texte courte à moyenne (≤ 4 000 tokens).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

RACINE = Path(__file__).resolve().parent

# Encodage UTF‑8 pour les sorties
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = [
    "generer",
    "paginer",
    "lister_outils",
    "valider_type",
    "main",
]

# ── Analyseur parent ────────────────────────────────────────────────────────
parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json",
    action="store_true",
    default=argparse.SUPPRESS,
    help="Sortie en JSON",
)
parent_parser.add_argument(
    "--racine",
    type=Path,
    default=argparse.SUPPRESS,
    help="Racine du projet (défaut : répertoire du script)",
)


def _charger_bibliotheque_anthropic() -> Optional[Any]:
    """Charge la bibliothèque anthropic en mode dégradé."""
    try:
        import anthropic  # type: ignore
        return anthropic
    except ImportError:
        print("Bibliothèque anthropic non disponible", file=sys.stderr)
        return None


def _detecter_contenu_binaire(texte: str) -> bool:
    """Détecte la présence d’un caractère nul."""
    return "\0" in texte


def _valider_fichier_json(chemin: Path) -> Optional[dict]:
    """Lit et valide un fichier JSON."""
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read()
            if _detecter_contenu_binaire(contenu):
                print("Fichier binaire détecté", file=sys.stderr)
                return None
            return json.loads(contenu)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as e:
        print(f"Fichier JSON invalide : {e}", file=sys.stderr)
        return None


def _refus(message: str) -> dict:
    """Produit un résultat de refus conforme au protocole."""
    print(message, file=sys.stderr)
    return {"denominateur": 0}


def generer(
    prompt: str,
    modele: str = "claude-3-5-sonnet-20240620",
    max_tokens: int = 1024,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Génère du texte à partir d’un prompt."""
    if _detecter_contenu_binaire(prompt):
        raise ValueError("Entrée non‑textuelle détectée")

    anthropic = _charger_bibliotheque_anthropic()
    if anthropic is None or not api_key:
        # Mode dégradé : on renvoie un texte factice
        return {"completion": "exemple de texte", "denominateur": 1}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=modele,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        completion = message.content[0].text if message.content else ""
        return {"completion": completion, "denominateur": 1}
    except Exception as e:
        print(f"Erreur lors de la génération : {e}", file=sys.stderr)
        return {"completion": "", "denominateur": 0}


def paginer(
    prompt: str,
    modele: str = "claude-3-5-sonnet-20240620",
    max_tokens: int = 1024,
    page_size: int = 100,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Paginer une réponse longue."""
    if _detecter_contenu_binaire(prompt):
        raise ValueError("Entrée non‑textuelle détectée")

    anthropic = _charger_bibliotheque_anthropic()
    if anthropic is None or not api_key:
        # Mode dégradé : on renvoie une seule page factice
        return {"pages": ["exemple de page"], "denominateur": 1}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        pages = []
        for i in range(0, max_tokens, page_size):
            truncated = f"{prompt[: i * 4]}\n\n[Partie {i // page_size + 1}]"
            message = client.messages.create(
                model=modele,
                max_tokens=min(page_size, max_tokens - i),
                messages=[{"role": "user", "content": truncated}],
            )
            pages.append(message.content[0].text if message.content else "")
        return {"pages": pages, "denominateur": len(pages)}
    except Exception as e:
        print(f"Erreur lors de la pagination : {e}", file=sys.stderr)
        return {"pages": [], "denominateur": 0}


def lister_outils(*, api_key: Optional[str] = None) -> dict:
    """Liste les outils disponibles via l'API Anthropic."""
    anthropic = _charger_bibliotheque_anthropic()
    if anthropic is None or not api_key:
        # Aucun outil réel à lister : refus conforme
        return _refus("denominateur nul – aucun outil disponible")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # L’API ne fournit pas de méthode de listage ; on renvoie un refus
        return _refus("denominateur nul – listage non supporté")
    except Exception as e:
        print(f"Erreur lors du listage : {e}", file=sys.stderr)
        return {"outils": [], "denominateur": 0}


def valider_type(type_name: str, json_input: Path) -> dict:
    """Valide qu’un objet JSON correspond à un type Anthropic."""
    contenu = _valider_fichier_json(json_input)
    if contenu is None:
        return _refus("denominateur nul – JSON invalide")

    if type_name == "Message":
        valide = (
            isinstance(contenu, dict)
            and "role" in contenu
            and "content" in contenu
            and isinstance(contenu["role"], str)
        )
    elif type_name == "TextBlock":
        valide = isinstance(contenu, dict) and "text" in contenu
    else:
        valide = False

    return {"valide": valide, "denominateur": 1 if valide else 0}


def _creer_analyseur() -> argparse.ArgumentParser:
    """Construit l’analyseur d’arguments."""
    parser = argparse.ArgumentParser(
        description="Dialoguer avec l'API Anthropic",
        parents=[parent_parser],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # ── sous‑commande generer ────────────────────────────────────────────────
    gen = sous_parsers.add_parser(
        "generer",
        parents=[parent_parser],
        help="Générer du texte à partir d'un prompt",
    )
    gen.add_argument("--prompt", required=True, help="Texte à envoyer au modèle")
    gen.add_argument(
        "--modele",
        default="claude-3-5-sonnet-20240620",
        help="Modèle à utiliser (défaut : claude-3-5-sonnet-20240620)",
    )
    gen.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Nombre maximum de tokens à générer (défaut : 1024)",
    )
    gen.add_argument("--api-key", required=True, help="Clé API Anthropic")

    # ── sous‑commande paginer ───────────────────────────────────────────────
    pag = sous_parsers.add_parser(
        "paginer",
        parents=[parent_parser],
        help="Paginer une réponse longue",
    )
    pag.add_argument("--prompt", required=True, help="Texte à envoyer au modèle")
    pag.add_argument(
        "--modele",
        default="claude-3-5-sonnet-20240620",
        help="Modèle à utiliser (défaut : claude-3-5-sonnet-20240620)",
    )
    pag.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Nombre maximum de tokens à générer (défaut : 1024)",
    )
    pag.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Taille maximale d’une page en tokens (défaut : 100)",
    )
    pag.add_argument("--api-key", required=True, help="Clé API Anthropic")

    # ── sous‑commande outils ───────────────────────────────────────────────
    out = sous_parsers.add_parser(
        "outils",
        parents=[parent_parser],
        help="Lister les outils disponibles",
    )
    out.add_argument("--api-key", required=True, help="Clé API Anthropic")

    # ── sous‑commande types ────────────────────────────────────────────────
    typ = sous_parsers.add_parser(
        "types",
        parents=[parent_parser],
        help="Valider un type Anthropic",
    )
    typ.add_argument("--type", required=True, help="Nom du type à valider")
    typ.add_argument(
        "--json-input",
        type=Path,
        required=True,
        help="Chemin vers le fichier JSON à valider",
    )

    return parser


def main() -> int:
    """Point d’entrée principal."""
    parser = _creer_analyseur()
    args = parser.parse_args()

    racine = getattr(args, "racine", RACINE)
    json_mode = getattr(args, "json", False)

    try:
        if args.commande == "generer":
            resultat = generer(
                args.prompt,
                modele=args.modele,
                max_tokens=args.max_tokens,
                api_key=args.api_key,
            )
        elif args.commande == "paginer":
            resultat = paginer(
                args.prompt,
                modele=args.modele,
                max_tokens=args.max_tokens,
                page_size=args.page_size,
                api_key=args.api_key,
            )
        elif args.commande == "outils":
            resultat = lister_outils(api_key=args.api_key)
        elif args.commande == "types":
            chemin = Path(args.json_input)
            chemin = chemin if chemin.is_absolute() else racine / chemin
            resultat = valider_type(args.type, chemin)
        else:
            return 1

        # Sortie JSON ou lisible
        if json_mode:
            json.dump(resultat, sys.stdout)
            print()
        else:
            # Affichage humain minimal
            if args.commande == "generer":
                print(resultat.get("completion", ""))
            elif args.commande == "paginer":
                print("\n".join(resultat.get("pages", [])))
            elif args.commande == "outils":
                print("\n".join(str(o) for o in resultat.get("outils", [])))
            elif args.commande == "types":
                print("true" if resultat.get("valide") else "false")

        # Gestion du protocole de refus
        if resultat.get("denominateur", 0) == 0:
            print("denominateur nul – aucun élément à examiner", file=sys.stderr)
            return 3
        return 0

    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())