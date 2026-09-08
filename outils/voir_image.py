"""QUESTION      Que montre cette image, et où sont les objets/contours ?
MESURE        L'image est lue, les contours sont détectés via Canny puis findContours.
HYPOTHESES    L'image est lisible, en couleur ou gris, et contient des objets contrastés.
LIMITES       Pas de détection si OpenCV absent, ou image non lisible, ou très floue.
CONTRE-EXEMPLES Une image monochrome sans contraste ne produit aucun contour.
INVOCATION
    {outil} voir --image {fichier} --json
DOMAINE       Analyse d'images simples pour IA question‑réponse.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

__all__ = [
    "charger_image",
    "detecter_contours",
    "analyse_image",
    "comparer_images",
    "main",
]

# ──────────────────────────────────────────────────────────────
# Détection paresseuse d'OpenCV
# ──────────────────────────────────────────────────────────────
CV2_DISPONIBLE = importlib.util.find_spec("cv2") is not None


def _cv2_disponible() -> bool:
    """Indique si OpenCV a pu être trouvé sans l'importer."""
    return CV2_DISPONIBLE


# ──────────────────────────────────────────────────────────────
# Gestion de la racine du projet
# ──────────────────────────────────────────────────────────────
def _ajouter_racine(racine: Path) -> None:
    """Insère la racine fournie en tête de sys.path."""
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))


# ──────────────────────────────────────────────────────────────
# API publique (sans argparse, sans I/O)
# ──────────────────────────────────────────────────────────────
def charger_image(chemin: Path) -> Any:
    """Lit une image depuis *chemin* avec OpenCV.

    Lève RuntimeError si OpenCV est indisponible.
    """
    if not _cv2_disponible():
        raise RuntimeError("OpenCV non disponible, impossible de charger l'image.")
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Import d'OpenCV échoué.") from exc
    img = cv2.imread(str(chemin))  # type: ignore[arg-type]
    if img is None:
        raise FileNotFoundError(f"Impossible de lire l'image : {chemin}")
    return img


def detecter_contours(img: Any) -> List[Any]:
    """Renvoie la liste des contours détectés dans *img*.

    Lève RuntimeError si OpenCV est indisponible.
    """
    if not _cv2_disponible():
        raise RuntimeError("OpenCV non disponible, impossible de détecter les contours.")
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Import d'OpenCV échoué.") from exc
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)  # type: ignore[arg-type]
    edges = cv2.Canny(gris, 100, 200)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours


def analyse_image(chemin: Path) -> Tuple[int, List[str], Dict[str, Any], int]:
    """
    Analyse une seule image.

    Retourne (denominateur, examines, resultat, code_sortie).
    """
    if not _cv2_disponible():
        return 0, [], {"message": "OpenCV indisponible, analyse impossible."}, 2

    try:
        img = charger_image(chemin)
    except Exception as e:
        sys.stderr.write(f"{str(e)}\n")
        return 0, [], {"erreur": str(e)}, 2

    contours = detecter_contours(img)
    nb = len(contours)

    try:
        import cv2  # type: ignore
    except ImportError:  # pragma: no cover
        cv2 = None  # safety, though _cv2_disponible() guarantees presence

    boites = [cv2.boundingRect(c) for c in contours]  # type: ignore[arg-type]

    resultat = {
        "nombre_contours": nb,
        "boites": boites,
    }

    code = 1 if nb > 0 else 0
    return 1, [str(chemin)], resultat, code


def comparer_images(chemin1: Path, chemin2: Path) -> Tuple[int, List[str], Dict[str, Any], int]:
    """
    Compare deux images et signale les différences.

    Retourne (denominateur, examines, resultat, code_sortie).
    """
    if not _cv2_disponible():
        return 0, [], {"message": "OpenCV indisponible, comparaison impossible."}, 2

    try:
        img1 = charger_image(chemin1)
        img2 = charger_image(chemin2)
    except Exception as e:
        sys.stderr.write(f"{str(e)}\n")
        return 0, [], {"erreur": str(e)}, 2

    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Import d'OpenCV échoué.") from exc

    diff = cv2.absdiff(img1, img2)  # type: ignore[arg-type]
    gris = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gris, 30, 255, cv2.THRESH_BINARY)
    contours = detecter_contours(thresh)

    nb = len(contours)
    boites = [cv2.boundingRect(c) for c in contours]  # type: ignore[arg-type]

    resultat = {
        "nombre_differences": nb,
        "boites_differences": boites,
    }

    code = 1 if nb > 0 else 0
    return 2, [str(chemin1), str(chemin2)], resultat, code


# ──────────────────────────────────────────────────────────────
# Construction du contrat (six sections)
# ──────────────────────────────────────────────────────────────
_CONTRAT = {
    "QUESTION": "Que montre cette image, et où sont les objets/contours ?",
    "MESURE": "L'image est lue, les contours sont détectés via Canny puis findContours.",
    "HYPOTHESES": "L'image est lisible, en couleur ou gris, et contient des objets contrastés.",
    "LIMITES": "Pas de détection si OpenCV absent, ou image non lisible, ou très floue.",
    "CONTRE-EXEMPLES": "Une image monochrome sans contraste ne produit aucun contour.",
    "DOMAINE": "Analyse d'images simples pour IA question‑réponse.",
}

# ──────────────────────────────────────────────────────────────
# Interface en ligne de commande
# ──────────────────────────────────────────────────────────────
def _format_humain(res: Dict[str, Any]) -> str:
    """Formate le dictionnaire résultat pour affichage humain."""
    lignes = [f"{k} : {v}" for k, v in res.items()]
    return "\n".join(lignes)


def _sortie(
    denominateur: int,
    examines: List[str],
    resultat: Dict[str, Any],
    code_sortie: int,
    json_mode: bool,
) -> int:
    """Prépare la sortie selon le mode demandé et retourne le code de sortie."""
    if denominateur == 0:
        # Protocole de refus légitime
        sys.stderr.write("Denominateur nul : rien a examiner, refus de conclure.\n")
        payload = {"denominateur": 0}
        if json_mode:
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 3

    if json_mode:
        payload = {
            "contrat": _CONTRAT,
            "denominateur": denominateur,
            "examines": examines[:200],
            "examines_tronques": len(examines) > 200,
            "resultat": resultat,
        }
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_format_humain(resultat) + "\n")

    return code_sortie


def main() -> int:
    # Encodage UTF‑8 même sur console cp1252
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # Arguments communs transmis aux sous‑commandes
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--racine",
        type=Path,
        help="Chemin racine du projet (préfixe du sys.path).",
        default=argparse.SUPPRESS,
    )
    parent_parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON unique sur stdout.",
        default=argparse.SUPPRESS,
    )

    parser = argparse.ArgumentParser(
        prog="voir_image.py",
        description="Outil d'analyse d'images pour répondre à la question : Que montre cette image, et où sont les objets/contours ?",
        epilog="Exemple : python voir_image.py voir --image chemin/vers/image.png",
        parents=[parent_parser],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # Sous‑commande « voir »
    p_voir = subparsers.add_parser(
        "voir",
        help="Analyse une image et décrit ses contours.",
        parents=[parent_parser],
    )
    p_voir.add_argument("--image", type=Path, required=True, help="Chemin vers l'image à analyser.")

    # Sous‑commande « comparer »
    p_cmp = subparsers.add_parser(
        "comparer",
        help="Compare deux images et indique les différences.",
        parents=[parent_parser],
    )
    p_cmp.add_argument("--image1", type=Path, required=True, help="Première image.")
    p_cmp.add_argument("--image2", type=Path, required=True, help="Seconde image.")

    args = parser.parse_args()

    # Gestion de la racine
    racine = Path(getattr(args, "racine", Path(__file__).resolve().parent))
    _ajouter_racine(racine)

    if args.commande == "voir":
        denom, examines, res, code = analyse_image(args.image)
        return _sortie(denom, examines, res, code, getattr(args, "json", False))

    if args.commande == "comparer":
        denom, examines, res, code = comparer_images(args.image1, args.image2)
        return _sortie(denom, examines, res, code, getattr(args, "json", False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())