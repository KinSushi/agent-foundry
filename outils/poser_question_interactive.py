"""QUESTION: Comment demander une décision humaine ?
MESURE: Interaction texte via console.
HYPOTHESES: L'utilisateur fournit une réponse claire.
LIMITES: Pas d'interface graphique, aucune validation avancée.
CONTRE-EXEMPLES: Entrées vides ou non‑texte.
INVOCATION
    {outil} --json
DOMAINE: Agents IA nécessitant une décision humaine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Any

# 2. Gestion de l'encodage de la console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = ["poser_question_interactive"]


def _obtenir_prompt_fonction() -> Callable[[str], str]:
    """
    Retourne une fonction de saisie adaptée.
    Essaie d'utiliser prompt_toolkit, puis InquirerPy,
    sinon revient à input().
    Aucun message n'est affiché ici ; la décision d'afficher
    un avertissement est prise dans la CLI.
    """
    try:
        from prompt_toolkit import prompt  # type: ignore
        return lambda q: prompt(q)
    except ImportError:
        pass

    try:
        from InquirerPy import inquirer  # type: ignore
        return lambda q: inquirer.text(message=q).execute()
    except ImportError:
        pass

    return lambda q: input(q)


def poser_question_interactive(
    prompt_fonction: Callable[[str], str] | None = None,
) -> str:
    """
    Pose la question « Comment demander une décision humaine ? » à l'utilisateur
    et renvoie la réponse saisie.
    """
    question = "Comment demander une décision humaine ? "
    if prompt_fonction is None:
        prompt_fonction = _obtenir_prompt_fonction()
    reponse = prompt_fonction(question)
    return reponse.strip()


def _contrat() -> Dict[str, str]:
    """Extrait les sections du docstring pour le champ « contrat » du JSON."""
    sections = [
        "QUESTION",
        "MESURE",
        "HYPOTHESES",
        "LIMITES",
        "CONTRE-EXEMPLES",
        "INVOCATION",
        "DOMAINE",
    ]
    texte = __doc__ or ""
    contrat: Dict[str, str] = {}
    for sec in sections:
        prefix = f"{sec}:"
        start = texte.find(prefix)
        if start != -1:
            end = texte.find("\n", start)
            valeur = (
                texte[start + len(prefix) : end].strip()
                if end != -1
                else texte[start + len(prefix) :].strip()
            )
            contrat[sec.lower()] = valeur
    return contrat


def _json_sortie(reponse: str, succes: bool = True) -> str:
    """Construit l'objet JSON de sortie."""
    examines: List[Dict[str, Any]] = []
    if succes:
        examines.append(
            {"nom": "question", "valeur": "Comment demander une décision humaine ?"}
        )
    examines_tronques = False
    if len(examines) > 200:
        examines = examines[:200]
        examines_tronques = True

    payload = {
        "contrat": _contrat(),
        "denominateur": len(examines),
        "examines": examines,
        "examines_tronques": examines_tronques,
        "resultat": reponse,
    }
    if not succes:
        payload["erreur"] = "Interaction interrompue par l'utilisateur."
    return json.dumps(payload, ensure_ascii=False)


def main() -> int:
    # 6. Gestion du répertoire racine
    racine_defaut = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        prog="poser_question_interactive.py",
        description="Pose la question « Comment demander une décision humaine ? » à un humain.",
        epilog="Exemple : python poser_question_interactive.py --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=racine_defaut,
        help="Chemin racine à préfixer à sys.path (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la réponse sous forme d’un unique objet JSON sur stdout.",
    )

    args = parser.parse_args()

    # insertion du chemin racine
    racine = args.racine.resolve()
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    # Avertissement éventuel sur l'absence de modules avancés
    prompt_fonction = _obtenir_prompt_fonction()
    if prompt_fonction.__code__.co_name == "<lambda>" and prompt_fonction.__code__.co_filename == "<string>":
        print(
            "Aucun module d'interface avancée disponible (prompt_toolkit, InquirerPy). "
            "Utilisation de input() standard.",
            file=sys.stderr,
        )

    try:
        reponse = poser_question_interactive(prompt_fonction)
    except (EOFError, KeyboardInterrupt):
        # Protocole de refus légitime (F4)
        print(
            "Denominateur nul : rien à examiner, refus de conclure.",
            file=sys.stderr,
        )
        if args.json:
            print(_json_sortie("", succes=False))
        return 3
    except Exception as e:
        # Traitement générique, mais on applique le même protocole de refus
        print(
            "Denominateur nul : rien à examiner, refus de conclure.",
            file=sys.stderr,
        )
        if args.json:
            print(_json_sortie("", succes=False))
        return 3

    if args.json:
        print(_json_sortie(reponse))
    else:
        print(reponse)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())