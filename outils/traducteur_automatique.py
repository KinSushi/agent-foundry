"""QUESTION
Comment traduire ce texte sans API externe ?
MESURE
Le script examine le texte fourni et tente de le traduire via les catalogues .mo disponibles avec le module gettext.
HYPOTHESES
Un catalogue .mo correspondant à la langue demandée existe dans le répertoire indiqué.
LIMITES
Sans catalogue .mo, aucune traduction n’est possible ; le script ne recourt à aucune API externe.
CONTRE-EXEMPLES
Un texte fourni alors qu’aucun fichier .mo n’est présent ne sera pas traduit.
DOMAINE
Utilisation en ligne de commande pour vérifier la disponibilité d’une traduction locale sans appel réseau.
"""

from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 6. Racine portable
RACINE: Path = Path(__file__).resolve().parent

__all__: List[str] = ["traduire_texte", "analyse_translation"]


def _charger_translation(
    domaine: str,
    localedir: Optional[Path],
    langues: Optional[List[str]],
) -> Any:
    """Retourne une instance gettext.Translations ou NullTranslations."""
    import gettext

    try:
        return gettext.translation(
            domain=domaine,
            localedir=str(localedir) if localedir else None,
            languages=langues,
            fallback=False,
        )
    except OSError:
        # Aucun catalogue trouvé : on utilise la traduction nulle (identité)
        return gettext.NullTranslations()


def traduire_texte(
    texte: str,
    domaine: str = "messages",
    localedir: Optional[Path] = None,
    langues: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Analyse et traduit *texte*.

    Retourne un dictionnaire contenant :
        - « succès » (bool) : True si une traduction différente a été obtenue.
        - « traduction » (str) : texte traduit ou texte original.
        - « denominateur » (int) : nombre d’éléments réellement examinés (1 ici).
        - « examines » (list[str]) : liste des éléments examinés.
        - « examines_tronques » (bool) : toujours False (pas de troncature).
    """
    trans = _charger_translation(domaine, localedir, langues)
    traduction = trans.gettext(texte)
    succès = traduction != texte
    return {
        "succès": succès,
        "traduction": traduction,
        "denominateur": 1,
        "examines": [texte],
        "examines_tronques": False,
    }


def analyse_translation(
    texte: str,
    domaine: str,
    localedir: Optional[Path],
    langues: Optional[List[str]],
) -> Dict[str, Any]:
    """Enveloppe : traduit puis prépare le contrat JSON."""
    result = traduire_texte(texte, domaine, localedir, langues)

    # contrat tiré du docstring du module
    contrat = {
        "QUESTION": "Comment traduire ce texte sans API externe ?",
        "MESURE": "Le script examine le texte fourni et tente de le traduire via les catalogues .mo disponibles avec le module gettext.",
        "HYPOTHESES": "Un catalogue .mo correspondant à la langue demandée existe dans le répertoire indiqué.",
        "LIMITES": "Sans catalogue .mo, aucune traduction n’est possible ; le script ne recourt à aucune API externe.",
        "CONTRE-EXEMPLES": "Un texte fourni alors qu’aucun fichier .mo n’est présent ne sera pas traduit.",
        "DOMAINE": "Utilisation en ligne de commande pour vérifier la disponibilité d’une traduction locale sans appel réseau.",
    }

    json_payload = {
        "denominateur": result["denominateur"],
        "examines": result["examines"][:200],
        "examines_tronques": result["examines_tronques"],
        "contrat": contrat,
    }
    return {"payload": json_payload, "traduction": result["traduction"], "succès": result["succès"]}


def _afficher_humain(traduction: str, succès: bool) -> None:
    if succès:
        print(traduction)
    else:
        print(
            "Impossible de traduire le texte sans ressource externe.",
            file=sys.stderr,
        )
        print(traduction)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="traducteur_automatique.py",
        description="Traduit un texte en utilisant les catalogues gettext locaux, sans appel à une API externe.",
        epilog="Exemple : traducteur_automatique.py \"Bonjour\" --lang fr --domain monapp --localedir ./locale",
    )
    parser.add_argument(
        "texte",
        help="Texte à traduire.",
    )
    parser.add_argument(
        "--racine",
        help="Chemin racine du projet (remplace la valeur par défaut).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique sur stdout.",
    )
    parser.add_argument(
        "--domain",
        default="messages",
        help="Nom du domaine gettext (défaut : messages).",
    )
    parser.add_argument(
        "--localedir",
        help="Répertoire contenant les sous‑répertoires de langues (ex : ./locale).",
    )
    parser.add_argument(
        "--lang",
        action="append",
        dest="langues",
        help="Code(s) langue(s) à utiliser (ex : fr, en). Peut être répété.",
    )

    args = parser.parse_args()

    # 6. Gestion du chemin racine
    global RACINE
    if args.racine:
        RACINE = Path(args.racine).resolve()
    if str(RACINE) not in sys.path:
        sys.path.insert(0, str(RACINE))

    # Validation du texte
    if not isinstance(args.texte, str):
        print("Erreur : le texte fourni n’est pas une chaîne.", file=sys.stderr)
        return 2

    # Préparation du répertoire locale
    localedir_path: Optional[Path] = Path(args.localedir).resolve() if args.localedir else None
    if localedir_path and not localedir_path.is_dir():
        print(f"Erreur : le répertoire locale indiqué n’existe pas : {localedir_path}", file=sys.stderr)
        return 2

    # Analyse / traduction
    analyse = analyse_translation(
        texte=args.texte,
        domaine=args.domain,
        localedir=localedir_path,
        langues=args.langues,
    )

    # 10. Gestion du dénominateur nul
    if analyse["payload"]["denominateur"] == 0:
        print("Refus de conclure : aucun élément n’a pu être examiné.", file=sys.stderr)
        return 3

    if args.json:
        json.dump(analyse["payload"], sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0 if analyse["succès"] else 1
    else:
        _afficher_humain(analyse["traduction"], analyse["succès"])
        return 0 if analyse["succès"] else 1


if __name__ == "__main__":
    raise SystemExit(main())