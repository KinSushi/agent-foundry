"""Outil d'analyse de cohérence entre réponses de modèles d'IA.

QUESTION      Les réponses de différents modèles d'IA sont-elles cohérentes entre elles ?
              Un prompt est-il optimisé pour minimiser les hallucinations ?
              Une réponse contient-elle des biais de genre ou culturels ?
MESURE        Nombre de contradictions détectées entre réponses (via openai_harmony ou heuristique).
              Score de risque d'hallucination d'un prompt (via openai_harmony ou 0 en mode dégradé).
              Score de biais dans une réponse (via openai_harmony ou liste prédéfinie).
HYPOTHÈSES    - Les fichiers d'entrée sont en texte brut UTF-8.
              - Les modèles sont supportés par openai_harmony (si disponible).
              - Les seuils sont dans [0.0, 1.0].
LIMITES       - Ne détecte pas les contradictions implicites.
              - Les biais culturels dépendent du corpus de référence de openai_harmony.
              - Mode dégradé moins précis que openai_harmony.
CONTRE-EXEMPLE Un prompt optimisé pour réduire les hallucinations peut introduire des biais de genre.
INVOCATION
    {outil} harmoniser --reponses {dossier}/reponses.txt --modeles {dossier}/modeles.txt --json
    {outil} optimiser --prompt {dossier}/prompt.txt --modele gpt-4o --json
    {outil} biais --texte {dossier}/texte.txt --modele gpt-4o --json
DOMAINE       Réponses générées par des LLM, prompts techniques, analyses de biais en français/anglais.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# Liste prédéfinie de termes biaisés pour le mode dégradé
TERMES_BIAISES = {
    "genre": ["il", "elle", "homme", "femme", "médecin", "infirmière", "papa", "maman"],
    "culturel": ["occidental", "oriental", "développé", "sous-développé", "premier monde"]
}

# Mots-clés contradictoires pour le mode dégradé
MOTS_CONTRAIRES = {
    "oui": ["non", "jamais", "impossible"],
    "non": ["oui", "toujours", "possible"],
    "toujours": ["jamais", "parfois"],
    "jamais": ["toujours", "parfois"],
    "vrai": ["faux"],
    "faux": ["vrai"]
}

__all__ = ["harmoniser", "optimiser", "analyser_biais", "main"]

def lire_fichier(chemin: Path) -> str:
    """Lit un fichier texte avec gestion des erreurs."""
    try:
        contenu = chemin.read_text(encoding="utf-8", errors="replace")
        if chr(0) in contenu:
            raise ValueError("Fichier binaire détecté")
        return contenu
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Fichier illisible : {chemin}") from e

def harmoniser(
    reponses: List[str],
    modeles: List[str],
    seuil: float = 0.7,
    *,
    mode_degrade: bool = False
) -> Dict[str, Any]:
    """Détecte les contradictions entre réponses de modèles.

    Args:
        reponses: Liste des réponses (1 par modèle).
        modeles: Liste des noms de modèles (même ordre que reponses).
        seuil: Seuil de score de contradiction (0.0 à 1.0).
        mode_degrade: Active le mode dégradé (heuristique mots-clés).

    Returns:
        Dictionnaire avec "contradictions", "score_global" et "denominateur".

    Raises:
        ValueError: Si les entrées sont invalides.
    """
    if len(reponses) != len(modeles):
        raise ValueError("Nombre de réponses et de modèles différent")
    if not reponses:
        return {"contradictions": [], "score_global": 0.0, "denominateur": 0}

    if mode_degrade:
        contradictions = []
        for i, rep1 in enumerate(reponses):
            for j, rep2 in enumerate(reponses[i+1:], i+1):
                mots1 = set(rep1.lower().split())
                mots2 = set(rep2.lower().split())
                for mot, contraires in MOTS_CONTRAIRES.items():
                    if mot in mots1 and any(c in mots2 for c in contraires):
                        contradictions.append({
                            "text": f"Contradiction entre {modeles[i]} et {modeles[j]}",
                            "models": [modeles[i], modeles[j]],
                            "explanation": f"Présence de '{mot}' vs '{contraires[0]}'"
                        })
        score = min(1.0, len(contradictions) / max(1, len(reponses)))
        return {
            "contradictions": contradictions,
            "score_global": score,
            "denominateur": len(reponses)
        }

    try:
        import openai_harmony
    except ImportError:
        raise RuntimeError("openai_harmony absent. Mode dégradé requis.")

    try:
        resultat = openai_harmony.harmonize_responses(reponses, modeles)
    except Exception as e:
        raise RuntimeError(f"Échec de l'appel à openai_harmony: {e}") from e

    if not isinstance(resultat, dict) or "contradictions" not in resultat or "score" not in resultat:
        raise RuntimeError("Signature de harmonize_responses non conforme")

    return {
        "contradictions": [
            c for c in resultat["contradictions"]
            if c.get("score", 0.0) >= seuil
        ],
        "score_global": resultat["score"],
        "denominateur": len(reponses)
    }

def optimiser(
    prompt: str,
    modele: str,
    max_hallucination: float = 0.3,
    *,
    mode_degrade: bool = False
) -> Dict[str, Any]:
    """Optimise un prompt pour un modèle spécifique.

    Args:
        prompt: Texte du prompt à optimiser.
        modele: Nom du modèle cible.
        max_hallucination: Seuil maximal de risque d'hallucination.
        mode_degrade: Active le mode dégradé.

    Returns:
        Dictionnaire avec "optimized_prompt", "hallucination_risk", "warnings" et "denominateur".

    Raises:
        ValueError: Si le prompt est vide.
        RuntimeError: En mode dégradé sans openai_harmony.
    """
    if not prompt.strip():
        raise ValueError("Prompt vide")

    if mode_degrade:
        raise RuntimeError("openai_harmony requis pour optimiser")

    try:
        import openai_harmony
    except ImportError:
        raise RuntimeError("openai_harmony absent. Mode dégradé : aucune optimisation possible.")

    try:
        resultat = openai_harmony.optimize_prompt(prompt, modele)
    except Exception as e:
        raise RuntimeError(f"Échec de l'appel à openai_harmony: {e}") from e

    if not isinstance(resultat, dict) or "optimized_prompt" not in resultat:
        raise RuntimeError("Signature de optimize_prompt non conforme")

    return {
        "optimized_prompt": resultat["optimized_prompt"],
        "hallucination_risk": resultat.get("hallucination_risk", 0.0),
        "warnings": resultat.get("warnings", []),
        "denominateur": 1 if resultat["optimized_prompt"] else 0
    }

def analyser_biais(
    texte: str,
    modele: str,
    *,
    mode_degrade: bool = False
) -> Dict[str, Any]:
    """Analyse les biais dans une réponse de modèle.

    Args:
        texte: Texte à analyser.
        modele: Nom du modèle source.
        mode_degrade: Active le mode dégradé (liste prédéfinie).

    Returns:
        Dictionnaire avec "bias_report", "score_global" et "denominateur".

    Raises:
        ValueError: Si le texte est vide.
    """
    if not texte.strip():
        return {"bias_report": {}, "score_global": 0.0, "denominateur": 0}

    if mode_degrade:
        mots = texte.lower().split()
        details = []
        for categorie, termes in TERMES_BIAISES.items():
            count = sum(mot in termes for mot in mots)
            if count:
                details.append({
                    "category": categorie,
                    "count": count,
                    "terms": [t for t in termes if t in mots]
                })
        score = min(1.0, sum(d["count"] for d in details) / max(1, len(mots)))
        return {
            "bias_report": {
                "gender": sum(d["count"] for d in details if d["category"] == "genre") / max(1, len(mots)),
                "cultural": sum(d["count"] for d in details if d["category"] == "culturel") / max(1, len(mots)),
                "details": details
            },
            "score_global": score,
            "denominateur": 1
        }

    try:
        import openai_harmony
    except ImportError:
        raise RuntimeError("openai_harmony absent. Mode dégradé requis.")

    try:
        resultat = openai_harmony.analyze_bias(texte, modele)
    except Exception as e:
        raise RuntimeError(f"Échec de l'appel à openai_harmony: {e}") from e

    if not isinstance(resultat, dict) or "bias_report" not in resultat:
        raise RuntimeError("Signature de analyze_bias non conforme")

    return {
        "bias_report": resultat["bias_report"],
        "score_global": resultat.get("score", 0.0),
        "denominateur": 1
    }

def _creer_analyseur_parent() -> argparse.ArgumentParser:
    """Crée l'analyseur parent avec les options communes."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie au format JSON (un seul objet sur stdout)"
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Racine des chemins relatifs (défaut: répertoire de l'outil)"
    )
    return parent

def _resoudre_chemin(chemin: Union[str, Path], racine: Path) -> Path:
    """Résout un chemin relatif à la racine."""
    p = Path(chemin)
    return p if p.is_absolute() else racine / p

def _main_harmoniser(args: argparse.Namespace, racine: Path) -> Tuple[int, Dict[str, Any]]:
    """Exécute la sous-commande harmoniser."""
    try:
        reponses_path = _resoudre_chemin(args.reponses, racine)
        modeles_path = _resoudre_chemin(args.modeles, racine)

        reponses = lire_fichier(reponses_path).splitlines()
        modeles = lire_fichier(modeles_path).splitlines()

        if not reponses or not modeles:
            print("Aucune réponse ou modèle fourni. Refus de conclure (dénominateur nul).", file=sys.stderr)
            return 3, {"denominateur": 0, "examines": [], "examines_tronques": False}

        mode_degrade = not (args.openai_harmony if hasattr(args, "openai_harmony") else False)
        resultat = harmoniser(reponses, modeles, args.seuil, mode_degrade=mode_degrade)

        if mode_degrade:
            print("Mode dégradé activé : détection de contradictions par mots-clés (précision réduite).", file=sys.stderr)

        return 0, {
            **resultat,
            "examines": [str(reponses_path), str(modeles_path)],
            "examines_tronques": False
        }
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2, {"denominateur": 0, "error": str(e)}
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3 if "denominateur" in str(e) else 1, {"denominateur": 0, "error": str(e)}

def _main_optimiser(args: argparse.Namespace, racine: Path) -> Tuple[int, Dict[str, Any]]:
    """Exécute la sous-commande optimiser."""
    try:
        prompt_path = _resoudre_chemin(args.prompt, racine)
        prompt = lire_fichier(prompt_path)

        mode_degrade = not (args.openai_harmony if hasattr(args, "openai_harmony") else False)
        if mode_degrade:
            print("openai_harmony absent. Mode dégradé : aucune optimisation possible.", file=sys.stderr)
            return 3, {"denominateur": 0, "error": "openai_harmony required"}

        resultat = optimiser(prompt, args.modele, args.max_hallucination, mode_degrade=False)

        return 0, {
            **resultat,
            "examines": [str(prompt_path)],
            "examines_tronques": False
        }
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2, {"denominateur": 0, "error": str(e)}
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3 if "denominateur" in str(e) else 1, {"denominateur": 0, "error": str(e)}

def _main_biais(args: argparse.Namespace, racine: Path) -> Tuple[int, Dict[str, Any]]:
    """Exécute la sous-commande biais."""
    try:
        texte_path = _resoudre_chemin(args.texte, racine)
        texte = lire_fichier(texte_path)

        mode_degrade = not (args.openai_harmony if hasattr(args, "openai_harmony") else False)
        resultat = analyser_biais(texte, args.modele, mode_degrade=mode_degrade)

        if mode_degrade:
            print("Mode dégradé activé : détection de biais par liste de mots sensibles (précision faible).", file=sys.stderr)

        return 0, {
            **resultat,
            "examines": [str(texte_path)],
            "examines_tronques": False
        }
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2, {"denominateur": 0, "error": str(e)}
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3 if "denominateur" in str(e) else 1, {"denominateur": 0, "error": str(e)}

def main() -> int:
    """Point d'entrée de l'outil."""
    parent = _creer_analyseur_parent()

    analyseur = argparse.ArgumentParser(
        description="Outil d'analyse de cohérence entre réponses de modèles d'IA.",
        parents=[parent]
    )
    sous_analyseurs = analyseur.add_subparsers(dest="commande", required=True)

    # Sous-commande harmoniser
    harmoniser_parser = sous_analyseurs.add_parser(
        "harmoniser",
        parents=[parent],
        help="Détecte les contradictions entre réponses de modèles",
        description="Détecte les contradictions entre réponses de différents modèles d'IA."
    )
    harmoniser_parser.add_argument(
        "--reponses",
        type=str,
        required=True,
        help="Fichier texte contenant les réponses (1 par ligne)"
    )
    harmoniser_parser.add_argument(
        "--modeles",
        type=str,
        required=True,
        help="Fichier texte contenant les noms des modèles (1 par ligne, même ordre que --reponses)"
    )
    harmoniser_parser.add_argument(
        "--seuil",
        type=float,
        default=0.7,
        help="Seuil de score de contradiction (0.0 à 1.0, défaut 0.7)"
    )
    harmoniser_parser.add_argument(
        "--openai-harmony",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Force l'utilisation de openai_harmony (si disponible)"
    )

    # Sous-commande optimiser
    optimiser_parser = sous_analyseurs.add_parser(
        "optimiser",
        parents=[parent],
        help="Optimise un prompt pour un modèle spécifique",
        description="Évalue et optimise un prompt pour minimiser les hallucinations."
    )
    optimiser_parser.add_argument(
        "--prompt",
        type=str,
        required=True,
        help="Fichier texte contenant le prompt"
    )
    optimiser_parser.add_argument(
        "--modele",
        type=str,
        required=True,
        help="Nom du modèle (ex: 'gpt-4o')"
    )
    optimiser_parser.add_argument(
        "--max-hallucination",
        type=float,
        default=0.3,
        help="Seuil maximal de risque d'hallucination (0.0 à 1.0, défaut 0.3)"
    )

    # Sous-commande biais
    biais_parser = sous_analyseurs.add_parser(
        "biais",
        parents=[parent],
        help="Analyse les biais dans une réponse de modèle",
        description="Analyse les biais de genre et culturels dans une réponse."
    )
    biais_parser.add_argument(
        "--texte",
        type=str,
        required=True,
        help="Fichier texte contenant la réponse à analyser"
    )
    biais_parser.add_argument(
        "--modele",
        type=str,
        required=True,
        help="Nom du modèle (ex: 'gpt-4o')"
    )
    biais_parser.add_argument(
        "--openai-harmony",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Force l'utilisation de openai_harmony (si disponible)"
    )

    args = analyseur.parse_args()

    # Résoudre la racine
    racine = getattr(args, "racine", RACINE)

    # Vérifier openai_harmony
    openai_harmony_disponible = False
    try:
        import importlib.util
        openai_harmony_disponible = importlib.util.find_spec("openai_harmony") is not None
    except ImportError:
        pass

    # Exécuter la sous-commande
    if args.commande == "harmoniser":
        code, resultat = _main_harmoniser(args, racine)
    elif args.commande == "optimiser":
        code, resultat = _main_optimiser(args, racine)
    elif args.commande == "biais":
        code, resultat = _main_biais(args, racine)
    else:
        print(f"Commande inconnue : {args.commande}", file=sys.stderr)
        return 2

    # Sortie
    if getattr(args, "json", False):
        json.dump(resultat, sys.stdout, ensure_ascii=False)
        print()  # Nouvelle ligne après JSON
    else:
        if args.commande == "harmoniser":
            if resultat["denominateur"] == 0:
                print("Aucune réponse à examiner.", file=sys.stderr)
            elif not resultat["contradictions"]:
                print("Aucune contradiction détectée.", file=sys.stderr)
            else:
                print(f"Score global de contradiction : {resultat['score_global']:.2f}", file=sys.stderr)
                for c in resultat["contradictions"]:
                    print(f"- {c['text']} (modèles: {', '.join(c['models'])})", file=sys.stderr)
                    print(f"  Explication : {c['explanation']}", file=sys.stderr)
        elif args.commande == "optimiser":
            if resultat["denominateur"] == 0:
                print("Aucune optimisation possible.", file=sys.stderr)
            else:
                print(f"Prompt optimisé (risque d'hallucination : {resultat['hallucination_risk']:.2f}):", file=sys.stderr)
                print(resultat["optimized_prompt"], file=sys.stdout)
                if resultat["warnings"]:
                    print("Avertissements :", file=sys.stderr)
                    for w in resultat["warnings"]:
                        print(f"- {w}", file=sys.stderr)
        elif args.commande == "biais":
            if resultat["denominateur"] == 0:
                print("Aucun texte à analyser.", file=sys.stderr)
            else:
                print(f"Score global de biais : {resultat['score_global']:.2f}", file=sys.stderr)
                print("Rapport de biais :", file=sys.stderr)
                print(f"- Genre : {resultat['bias_report'].get('gender', 0.0):.2f}", file=sys.stderr)
                print(f"- Culturel : {resultat['bias_report'].get('cultural', 0.0):.2f}", file=sys.stderr)
                if "details" in resultat["bias_report"]:
                    for d in resultat["bias_report"]["details"]:
                        print(f"- {d['category']} : {d['count']} occurrence(s) de {d['terms']}", file=sys.stderr)

    return code

if __name__ == "__main__":
    raise SystemExit(main())