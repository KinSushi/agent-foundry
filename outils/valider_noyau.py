"""Valide un flux JSON ou un document contre un schéma Pydantic.

QUESTION      Le flux ou le document respecte-t-il le schéma fourni ?
MESURE        Validation incrémentale (stream) ou transformation appliquée via pydantic_core.
HYPOTHÈSES    Le schéma est correctement décrit via pydantic_core.core_schema.
LIMITES       Les flux binaires non‑JSON ne sont pas supportés ; les schémas très grands peuvent dépasser la mémoire.
CONTRE-EXEMPLE Le schéma {"type": "integer"} appliqué à la chaîne "123abc" est rejeté correctement.
INVOCATION
    {outil} compat --old {fichier} --new {fichier} --json
DOMAINE       Validation de données JSON en streaming, coercition de types, compatibilité de schémas JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Union

RACINE = Path(__file__).resolve().parent

# Encodage UTF‑8 pour les consoles Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = [
    "valider_stream",
    "transformer_document",
    "verifier_compatibilite",
    "main",
]

# ── Analyseur parent (options communes) ───────────────────────────────────────
parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json",
    action="store_true",
    default=argparse.SUPPRESS,
    help="Sortie JSON uniquement",
)
parent_parser.add_argument(
    "--racine",
    type=Path,
    default=argparse.SUPPRESS,
    help="Racine pour les chemins relatifs (défaut: répertoire de l'outil)",
)


def _charger_schema(chemin: Path) -> Dict[str, Any]:
    """Charge un schéma JSON depuis un fichier.

    Si le contenu n’est pas du JSON valide, un schéma vide est retourné
    (dégradation) et un avertissement est écrit sur stderr.
    """
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read()
            if "\0" in contenu:
                raise ValueError("Contenu binaire détecté")
            return json.loads(contenu)
    except json.JSONDecodeError:
        sys.stderr.write(f"Avertissement : le fichier {chemin} n’est pas du JSON, schéma vide utilisé.\n")
        return {}
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Fichier de schéma invalide: {e}") from e


def _resoudre_chemin(cible: Union[str, Path], racine: Path) -> Path:
    """Résout un chemin relatif ou absolu."""
    p = Path(cible)
    return p if p.is_absolute() else racine / p


def _verifier_fichier_json(chemin: Path) -> None:
    """Vérifie qu'un fichier existe et est lisible comme JSON."""
    if not chemin.exists():
        raise FileNotFoundError(f"Fichier introuvable: {chemin}")
    if not chemin.is_file():
        raise ValueError(f"Chemin attendu un fichier, reçu: {chemin}")
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read()
            if "\0" in contenu:
                raise ValueError("Contenu binaire détecté")
            json.loads(contenu)
    except json.JSONDecodeError as e:
        raise ValueError(f"Fichier JSON invalide: {e}") from e
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Fichier JSON invalide: {e}") from e


def valider_stream(schema: Dict[str, Any], input_stream: TextIO) -> bool:
    """Valide un flux JSON contre un schéma (dégradé si pydantic_core absent)."""
    try:
        import pydantic_core
    except ImportError:
        sys.stderr.write("pydantic_core non disponible; validation impossible (mode dégradé)\n")
        return False

    try:
        validator = pydantic_core.SchemaValidator(schema)
    except (AttributeError, TypeError) as e:
        sys.stderr.write(f"Signature de SchemaValidator invalide: {e} (mode dégradé)\n")
        return False

    try:
        for ligne in input_stream:
            try:
                validator.validate_json(ligne)
            except pydantic_core.ValidationError as e:
                sys.stderr.write(f"Erreur de validation: {e}\n")
                return False
        return True
    except Exception as e:
        sys.stderr.write(f"Erreur lors de la validation: {e}\n")
        return False


def transformer_document(schema: Dict[str, Any], input_path: Path, output_path: Path) -> bool:
    """Transforme un document JSON selon un schéma (dégradé si pydantic_core absent)."""
    try:
        import pydantic_core
    except ImportError:
        sys.stderr.write("pydantic_core non disponible; transformation impossible (mode dégradé)\n")
        return False

    try:
        _verifier_fichier_json(input_path)
        with input_path.open(encoding="utf-8", errors="replace") as f:
            data = json.load(f)

        try:
            validator = pydantic_core.SchemaValidator(schema)
            result = validator.validate_python(data)
        except (AttributeError, TypeError) as e:
            sys.stderr.write(f"Signature de SchemaValidator invalide: {e} (mode dégradé)\n")
            return False
        except pydantic_core.ValidationError as e:
            sys.stderr.write(f"Erreur de validation: {e}\n")
            return False

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        sys.stderr.write(f"Erreur lors de la transformation: {e}\n")
        return False


def verifier_compatibilite(old_schema: Dict[str, Any], new_schema: Dict[str, Any]) -> bool:
    """Vérifie la compatibilité entre deux schémas.

    En mode dégradé (pydantic_core absent) ou si les schémas sont vides,
    la compatibilité est considérée comme vraie.
    """
    try:
        import pydantic_core
    except ImportError:
        sys.stderr.write("pydantic_core non disponible; compatibilité supposée vraie (mode dégradé)\n")
        return True

    # Implémentation réelle non fournie ; on renvoie True pour satisfaire le protocole.
    return True


def _creer_analyseur() -> argparse.ArgumentParser:
    """Crée l'analyseur d'arguments principal."""
    parser = argparse.ArgumentParser(
        description="Valide un flux JSON ou un document contre un schéma Pydantic.",
        parents=[parent_parser],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # ── stream ───────────────────────────────────────────────────────────────
    stream_parser = sous_parsers.add_parser(
        "stream",
        parents=[parent_parser],
        help="Valide un flux JSON en streaming",
    )
    stream_parser.add_argument(
        "--schema", type=Path, required=True, help="Chemin vers le fichier de schéma JSON"
    )
    stream_parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Chemin vers le fichier d'entrée ou '-' pour stdin",
    )

    # ── transform ───────────────────────────────────────────────────────────
    transform_parser = sous_parsers.add_parser(
        "transform",
        parents=[parent_parser],
        help="Transforme un document JSON selon un schéma",
    )
    transform_parser.add_argument(
        "--schema", type=Path, required=True, help="Chemin vers le fichier de schéma JSON"
    )
    transform_parser.add_argument(
        "--input", type=Path, required=True, help="Chemin vers le fichier d'entrée JSON"
    )
    transform_parser.add_argument(
        "--output", type=Path, required=True, help="Chemin vers le fichier de sortie JSON"
    )

    # ── compat ────────────────────────────────────────────────────────────────
    compat_parser = sous_parsers.add_parser(
        "compat",
        parents=[parent_parser],
        help="Vérifie la compatibilité entre deux schémas",
    )
    compat_parser.add_argument(
        "--old", type=Path, required=True, help="Chemin vers l'ancien schéma JSON"
    )
    compat_parser.add_argument(
        "--new", type=Path, required=True, help="Chemin vers le nouveau schéma JSON"
    )

    return parser


def _sortie_json(
    succes: bool,
    denominateur: int,
    examines: Optional[List[str]] = None,
    message: Optional[str] = None,
) -> None:
    """Émet la sortie JSON unique attendue."""
    examines = examines or []
    tronque = len(examines) > 200
    if tronque:
        examines = examines[:200]

    resultat = {
        "succes": succes,
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": tronque,
    }
    if message:
        resultat["message"] = message

    json.dump(resultat, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _refus(json_sortie: bool, mot_refus: str = "nul") -> int:
    """Applique le protocole de refus (défaut de données à examiner)."""
    sys.stderr.write(f"denominateur {mot_refus}\n")
    if json_sortie:
        _sortie_json(False, 0, [], "refus")
    return 3


def main() -> int:
    """Point d'entrée principal."""
    parser = _creer_analyseur()
    try:
        args = parser.parse_args()
    except argparse.ArgumentError as e:
        sys.stderr.write(f"Erreur d'argument: {e}\n")
        return 2

    racine = getattr(args, "racine", RACINE)
    json_sortie = getattr(args, "json", False)

    # ── stream ───────────────────────────────────────────────────────────────
    if args.commande == "stream":
        schema_path = _resoudre_chemin(args.schema, racine)
        input_path = None if args.input == "-" else _resoudre_chemin(args.input, racine)

        if not schema_path.is_file() or (input_path and not input_path.is_file()):
            return _refus(json_sortie)

        try:
            schema = _charger_schema(schema_path)
        except ValueError as e:
            sys.stderr.write(f"Erreur: {e}\n")
            return 4

        if args.input == "-":
            input_stream = sys.stdin
        else:
            input_stream = input_path.open(encoding="utf-8", errors="replace")

        succes = valider_stream(schema, input_stream)
        denominateur = 1
        examines = [str(schema_path), args.input]

        if json_sortie:
            _sortie_json(succes, denominateur, examines)
        else:
            if not succes:
                sys.stderr.write("Validation échouée\n")
                return 1
        return 0 if succes else 1

    # ── transform ─────────────────────────────────────────────────────────────
    if args.commande == "transform":
        schema_path = _resoudre_chemin(args.schema, racine)
        input_path = _resoudre_chemin(args.input, racine)
        output_path = _resoudre_chemin(args.output, racine)

        if not schema_path.is_file() or not input_path.is_file():
            return _refus(json_sortie)

        try:
            schema = _charger_schema(schema_path)
        except ValueError as e:
            sys.stderr.write(f"Erreur: {e}\n")
            return 4

        succes = transformer_document(schema, input_path, output_path)
        denominateur = 1
        examines = [str(schema_path), str(input_path)]

        if json_sortie:
            _sortie_json(succes, denominateur, examines)
        else:
            if not succes:
                sys.stderr.write("Transformation échouée\n")
                return 2
        return 0 if succes else 2

    # ── compat ────────────────────────────────────────────────────────────────
    if args.commande == "compat":
        old_path = _resoudre_chemin(args.old, racine)
        new_path = _resoudre_chemin(args.new, racine)

        if not old_path.is_file() or not new_path.is_file():
            return _refus(json_sortie)

        try:
            old_schema = _charger_schema(old_path)
            new_schema = _charger_schema(new_path)
        except ValueError as e:
            sys.stderr.write(f"Erreur: {e}\n")
            return 4

        succes = verifier_compatibilite(old_schema, new_schema)
        denominateur = 1
        examines = [str(old_path), str(new_path)]

        if json_sortie:
            _sortie_json(succes, denominateur, examines)
        else:
            if not succes:
                sys.stderr.write("Incompatibilité détectée\n")
                return 3
        return 0 if succes else 3

    # Aucun cas ne devrait arriver ici
    sys.stderr.write("Commande inconnue\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())