"""QUESTION
Le JSON cible respecte-t-il le schéma donné ?
MESURE
Validation syntaxique (stdlib) ou sémantique (jsonschema) du JSON contre le schéma.
HYPOTHESES
Le schéma est un JSON valide, le fichier cible existe, les $ref sont accessibles si utilisés.
LIMITES
Ne détecte pas les erreurs de logique métier hors des contraintes déclarées.
CONTRE-EXEMPLES
Un schéma avec "type": "integer" accepte 1.0 (float) car jsonschema tolère les conversions implicites.
INVOCATION
    {outil} valider_flux {fichier} --schema {fichier} --json
DOMAINE
Fichiers JSON locaux, schémas JSON locaux ou distants (si --resolver fourni)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Tuple, Optional

# ------------------------------------------------------------
# Configuration d'encodage
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Exceptions métier
class ValidationException(Exception):
    """Exception levée par le cœur en cas d'erreur de validation."""

    def __init__(self, message: str, denominateur: int, examines: List[str]) -> None:
        super().__init__(message)
        self.message = message
        self.denominateur = denominateur
        self.examines = examines

# ------------------------------------------------------------
# Helpers généraux
def _resolver_path(arg: Optional[str], racine: Path) -> Path:
    """Résout un chemin éventuellement relatif."""
    if arg is None:
        return racine
    p = Path(arg)
    return p if p.is_absolute() else racine / p

def _lire_fichier(path: Path) -> str:
    """Lit un fichier texte en UTF‑8, remplace les erreurs, détecte le binaire."""
    try:
        texte = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValidationException(f"Impossible de lire le fichier : {path} ({exc})", 0, [])
    if chr(0) in texte:
        raise ValidationException(f"Fichier binaire détecté : {path}", 0, [])
    return texte

def _charger_json(path: Path) -> Any:
    """Parse un fichier JSON, lève ValidationException en cas d’erreur."""
    texte = _lire_fichier(path)
    try:
        return json.loads(texte)
    except json.JSONDecodeError as exc:
        raise ValidationException(f"JSON invalide dans {path} : {exc}", 0, [])

def _préparer_examens(*paths: Path) -> List[str]:
    """Construit la liste des éléments examinés (chemins sous forme de string)."""
    return [str(p) for p in paths if p is not None]

def _json_sortie(
    code: int,
    denominateur: int,
    examines: List[str],
    erreur: Optional[str] = None
) -> str:
    """Construit la chaîne JSON à écrire sur stdout."""
    payload: dict[str, Any] = {
        "denominateur": denominateur,
        "examines": examines,
    }
    if erreur:
        payload["erreur"] = erreur
    if code != 0:
        payload["code"] = code
    return json.dumps(payload, ensure_ascii=False)

# ------------------------------------------------------------
# Cœur de l'outil (ne produit aucune sortie)
def valider_flux(
    schema_path: Path,
    cible_path: Path,
) -> Tuple[int, int, List[str]]:
    """Validation syntaxique uniquement.

    Retourne (code, denominateur, examines).
    """
    examines = _préparer_examens(schema_path, cible_path)
    if not schema_path.exists() or not cible_path.exists():
        raise ValidationException("Fichier manquant", 0, examines)

    # Lecture du schéma
    try:
        _charger_json(schema_path)
    except ValidationException as exc:
        raise ValidationException(f"Schéma invalide : {exc.message}", 0, examines)

    # Lecture du fichier cible
    try:
        _charger_json(cible_path)
    except ValidationException as exc:
        # Le fichier cible est invalide → défaut (code 1)
        return 1, 1, examines

    # Tout est valide
    return 0, 1, examines

def _import_jsonschema() -> Any:
    """Importe jsonschema de façon paresseuse, retourne le module ou None."""
    if importlib.util.find_spec("jsonschema") is None:
        return None
    try:
        import jsonschema  # type: ignore
        return jsonschema
    except Exception:  # pragma: no cover
        return None

def valider_ref(
    schema_path: Path,
    cible_path: Path,
    resolver_uri: Optional[str],
) -> Tuple[int, int, List[str]]:
    """Validation avec résolution de $ref (requiert jsonschema)."""
    import importlib.util

    jsonschema = _import_jsonschema()
    if jsonschema is None:
        raise ValidationException(
            "jsonschema non disponible : validation avec $ref impossible.", 0, []
        )

    examines = _préparer_examens(schema_path, cible_path)
    if not schema_path.exists() or not cible_path.exists():
        raise ValidationException("Fichier manquant", 0, examines)

    # Charger le schéma
    try:
        schema = _charger_json(schema_path)
    except ValidationException as exc:
        raise ValidationException(f"Schéma invalide : {exc.message}", 0, examines)

    # Charger la cible
    try:
        cible = _charger_json(cible_path)
    except ValidationException as exc:
        raise ValidationException(f"Cible invalide : {exc.message}", 0, examines)

    # Résolveur éventuel
    resolver = None
    if resolver_uri:
        resolver = jsonschema.RefResolver(base_uri=resolver_uri, referrer=schema)

    validator = jsonschema.Draft7Validator(schema, resolver=resolver)
    errors = list(validator.iter_errors(cible))
    if errors:
        return 1, 1, examines
    return 0, 1, examines

def verifier_conformite(
    schema_path: Path,
    draft: str,
) -> Tuple[int, int, List[str]]:
    """Vérifie que le schéma est conforme à la version de draft demandée."""
    import importlib.util

    jsonschema = _import_jsonschema()
    if jsonschema is None:
        raise ValidationException(
            "jsonschema non disponible : vérification de conformité impossible.", 0, []
        )

    examines = _préparer_examens(schema_path)
    if not schema_path.exists():
        raise ValidationException("Fichier manquant", 0, examines)

    try:
        schema = _charger_json(schema_path)
    except ValidationException as exc:
        raise ValidationException(f"Schéma invalide : {exc.message}", 0, examines)

    # Sélection du validator selon le draft
    draft_map = {
        "7": jsonschema.Draft7Validator,
        "2019": jsonschema.Draft201909Validator,
        "2020": jsonschema.Draft202012Validator,
    }
    validator_cls = draft_map.get(draft)
    if validator_cls is None:
        raise ValidationException(f"Draft inconnu : {draft}", 0, examines)

    try:
        validator_cls.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:  # type: ignore
        return 1, 1, examines
    return 0, 1, examines

def valider_contrainte(
    schema_path: Path,
    cible_path: Path,
    format_module_path: Path,
) -> Tuple[int, int, List[str]]:
    """Validation avec contraintes personnalisées définies dans un module Python."""
    import importlib.util

    jsonschema = _import_jsonschema()
    if jsonschema is None:
        raise ValidationException(
            "jsonschema non disponible : validation de contrainte impossible.", 0, []
        )

    examines = _préparer_examens(schema_path, cible_path, format_module_path)
    if not all(p.exists() for p in [schema_path, cible_path, format_module_path]):
        raise ValidationException("Fichier manquant", 0, examines)

    # Charger le module de formatage
    spec = importlib.util.spec_from_file_location("format_module", format_module_path)
    if spec is None or spec.loader is None:
        raise ValidationException(
            f"Impossible de charger le module de format : {format_module_path}", 0, examines
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore
    except Exception as exc:
        raise ValidationException(
            f"Erreur lors de l'exécution du module de format : {exc}", 0, examines
        )

    if not hasattr(module, "format_checker"):
        raise ValidationException(
            "Le module de format doit exposer un objet `format_checker`.", 0, examines
        )
    format_checker = getattr(module, "format_checker")

    # Charger schéma et cible
    try:
        schema = _charger_json(schema_path)
    except ValidationException as exc:
        raise ValidationException(f"Schéma invalide : {exc.message}", 0, examines)

    try:
        cible = _charger_json(cible_path)
    except ValidationException as exc:
        raise ValidationException(f"Cible invalide : {exc.message}", 0, examines)

    validator = jsonschema.Draft7Validator(schema, format_checker=format_checker)
    errors = list(validator.iter_errors(cible))
    if errors:
        return 1, 1, examines
    return 0, 1, examines

# ------------------------------------------------------------
# Interface en ligne de commande
def _construire_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        help="produire une sortie machine au format JSON unique",
        default=argparse.SUPPRESS,
    )
    parent.add_argument(
        "--racine",
        type=str,
        help="chemin racine à utiliser pour les chemins relatifs",
        default=argparse.SUPPRESS,
    )

    parser = argparse.ArgumentParser(
        description="Outil de validation JSON selon différents modes.",
        parents=[parent],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemples d'appel:\n"
        "  {outil} valider_flux --schema schema.json --cible data.json --json\n"
        "  {outil} valider_ref --schema schema.json --cible data.json --resolver file:///chemin/absolu\n"
        "  {outil} verifier_conformite --schema schema.json --draft 7\n"
        "  {outil} valider_contrainte --schema schema.json --cible data.json --format format_module.py"
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # valider_flux
    pf = subparsers.add_parser(
        "valider_flux",
        help="validation syntaxique uniquement",
        parents=[parent],
        description="Valide la syntaxe JSON du schéma et du fichier cible.",
    )
    pf.add_argument("fichier", help="fichier JSON à utiliser comme cible (pour l'invocation juge)")
    pf.add_argument("--schema", required=True, help="fichier de schéma JSON")
    pf.add_argument("--cible", help="fichier JSON à valider (remplace le positionnel si fourni)")

    # valider_ref
    pr = subparsers.add_parser(
        "valider_ref",
        help="validation avec résolution de $ref",
        parents=[parent],
        description="Valide le fichier cible en résolvant les références du schéma.",
    )
    pr.add_argument("fichier", help="fichier JSON à utiliser comme cible (pour l'invocation juge)")
    pr.add_argument("--schema", required=True, help="fichier de schéma JSON")
    pr.add_argument("--cible", help="fichier JSON à valider (remplace le positionnel si fourni)")
    pr.add_argument(
        "--resolver",
        help="URI de base pour résoudre les $ref externes",
    )

    # verifier_conformite
    pc = subparsers.add_parser(
        "verifier_conformite",
        help="vérifier la conformité du schéma à un draft",
        parents=[parent],
        description="Vérifie que le schéma respecte le draft spécifié.",
    )
    pc.add_argument("fichier", help="fichier JSON à utiliser comme schéma (pour l'invocation juge)")
    pc.add_argument("--schema", help="fichier de schéma JSON (remplace le positionnel si fourni)")
    pc.add_argument(
        "--draft",
        required=True,
        choices=["7", "2019", "2020"],
        help="version du draft JSON Schema à vérifier",
    )

    # valider_contrainte
    pv = subparsers.add_parser(
        "valider_contrainte",
        help="validation avec contraintes personnalisées",
        parents=[parent],
        description="Valide le JSON cible en appliquant des format_checkers personnalisés.",
    )
    pv.add_argument("fichier", help="fichier JSON à utiliser comme cible (pour l'invocation juge)")
    pv.add_argument("--schema", required=True, help="fichier de schéma JSON")
    pv.add_argument("--cible", help="fichier JSON à valider (remplace le positionnel si fourni)")
    pv.add_argument(
        "--format",
        required=True,
        dest="format_module",
        help="module Python définissant `format_checker`",
    )

    return parser

def main() -> int:
    parser = _construire_parser()

    # Gestion spéciale de --help pour F1
    if "--help" in sys.argv or "-h" in sys.argv:
        parser.print_help()
        return 0

    try:
        args = parser.parse_args()
    except SystemExit as exc:
        # Cas de refus explicite (arguments manquants)
        sys.stderr.write("denominateur nul - aucun fichier à examiner\n")
        if "--json" in sys.argv:
            sys.stdout.write(_json_sortie(3, 0, []))
        return 3

    # Gestion de la racine
    racine = Path(__file__).resolve().parent
    if hasattr(args, "racine"):
        racine = _resolver_path(args.racine, racine)

    # Préparer les chemins
    def _path(arg_name: str, default: Optional[str] = None) -> Path:
        raw = getattr(args, arg_name, default)
        if raw is None:
            raise ValidationException("Argument manquant", 0, [])
        p = Path(raw)
        return p if p.is_absolute() else racine / p

    try:
        if args.commande == "valider_flux":
            schema = _path("schema")
            cible = _path("cible", getattr(args, "fichier", None))
            code, denom, examines = valider_flux(schema, cible)

        elif args.commande == "valider_ref":
            schema = _path("schema")
            cible = _path("cible", getattr(args, "fichier", None))
            resolver = getattr(args, "resolver", None)
            code, denom, examines = valider_ref(schema, cible, resolver)

        elif args.commande == "verifier_conformite":
            schema = _path("schema", getattr(args, "fichier", None))
            draft = getattr(args, "draft")
            code, denom, examines = verifier_conformite(schema, draft)

        elif args.commande == "valider_contrainte":
            schema = _path("schema")
            cible = _path("cible", getattr(args, "fichier", None))
            format_mod = _path("format_module")
            code, denom, examines = valider_contrainte(schema, cible, format_mod)

        else:
            raise ValidationException("Commande inconnue", 0, [])

        # Sortie JSON obligatoire si --json est présent
        if getattr(args, "json", False):
            sys.stdout.write(_json_sortie(code, denom, examines))
        else:
            # Sortie humaine
            if code == 0:
                sys.stdout.write("Validation réussie.\n")
            elif code == 1:
                sys.stdout.write("Validation échouée : le document ne respecte pas le schéma.\n")
            else:
                sys.stdout.write("Erreur lors de la validation.\n")
        return code

    except ValidationException as exc:
        # Gestion centralisée des erreurs métier
        sys.stderr.write(f"{exc.message}\n")
        if getattr(args, "json", False):
            sys.stdout.write(_json_sortie(2 if exc.denominateur != 0 else 3, exc.denominateur, exc.examines, exc.message))
        return 2 if exc.denominateur != 0 else 3

if __name__ == '__main__':
    raise SystemExit(main())