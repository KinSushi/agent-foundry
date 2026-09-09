"""QUESTION
    La sortie du LLM respecte‑t‑elle le format ou le schéma attendu ?
MESURE
    Détection des violations de schéma, erreurs de syntaxe, conversion en objet Python valide.
HYPOTHÈSES
    Le texte fourni provient d’un LLM, est du texte UTF‑8 et non binaire.
LIMITES
    Pas de validation de types profonds, pas de correction sémantique, formats non‑JSON supportés uniquement si la bibliothèque tierce est disponible.
CONTRE‑EXEMPLES
    Un JSON valide mais avec une clé manquante est accepté en mode dégradé (validation partielle).
INVOCATION
    {outil} detecter-erreurs --format json --fichier {fichier} --json
DOMAINE
    Sorties textuelles de LLM destinées à être parsées par des outils automatisés.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "verifier_schema",
    "detecter_erreurs",
    "convertir_objet",
    "imposer_format",
    "main",
]

# ──────────────────────────────────────────────────────────────────────────────
# Globals
# ──────────────────────────────────────────────────────────────────────────────
_engine: str = "stdlib"  # "lmformatenforcer" quand le module est disponible


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────
class ValidationError(Exception):
    """Erreur de validation métier (schéma, syntaxe, conversion)."""


class InputError(Exception):
    """Erreur liée à la lecture ou au type d’entrée."""


class NoDataError(Exception):
    """Aucun texte à examiner."""


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _reconfigure_streams() -> None:
    """Force l’encodage UTF‑8 sur stdout et stderr si possible."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _load_optional(name: str) -> Optional[Any]:
    """Importe un module optionnel uniquement s’il est installé."""
    if importlib.util.find_spec(name) is None:
        return None
    return __import__(name)


def _read_text(texte: Optional[str], fichier: Optional[str], racine: Path) -> str:
    """Renvoie le texte à examiner ou lève InputError."""
    if texte is not None:
        return texte
    if fichier is not None:
        p = Path(fichier)
        p = p if p.is_absolute() else racine / p
        try:
            data = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise InputError(f"Impossible de lire le fichier : {p}") from exc
        if "\x00" in data:
            raise InputError("Le fichier semble être binaire.")
        return data
    raise NoDataError


def _json_load(text: str) -> Any:
    """Charge du JSON."""
    return json.loads(text)


def _json_dump(obj: Any) -> str:
    """Sérialise en JSON minifié, clés triées."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _truncate_examines(items: List[str]) -> List[str]:
    """Limite la liste à 200 éléments."""
    return items[:200]


# ──────────────────────────────────────────────────────────────────────────────
# Core functions (ne font jamais d’affichage)
# ──────────────────────────────────────────────────────────────────────────────
def verifier_schema(schema: Dict[str, Any], texte: str) -> Dict[str, Any]:
    """Vérifie que *texte* respecte *schema* (dégradé : seules les clés top‑level)."""
    global _engine
    try:
        data = _json_load(texte)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"JSON invalide : {exc.msg}") from exc

    # --------------------------------------------------------------------- #
    # Mode réel – tentative d’utiliser lmformatenforcer si disponible
    # --------------------------------------------------------------------- #
    lmfe = _load_optional("lmformatenforcer")
    if lmfe is None:
        sys.stderr.write("mode degrade : lmformatenforcer absent\n")
    else:
        try:
            from lmformatenforcer.jsonschemaparser import JsonSchemaParser
            parser: Any = JsonSchemaParser()
            for idx, ch in enumerate(texte):
                try:
                    parser = parser.add_character(ch)  # type: ignore[attr-defined]
                except Exception as exc:
                    allowed = parser.get_allowed_characters()  # type: ignore[attr-defined]
                    raise ValidationError(
                        f"Déviation à l’index {idx} : caractère «{ch}» non autorisé. "
                        f"Autorisé : {allowed}"
                    ) from exc
            if not parser.can_end():  # type: ignore[attr-defined]
                allowed = parser.get_allowed_characters()  # type: ignore[attr-defined]
                raise ValidationError(
                    f"Fin invalide après l’index {len(texte)-1}. Autorisé : {allowed}"
                )
            _engine = "lmformatenforcer"
        except Exception as exc:
            # Si la validation caractère‑par‑caractère échoue, on retombe sur le
            # mode dégradé tout en conservant le message d’erreur.
            raise ValidationError(str(exc)) from exc

    # --------------------------------------------------------------------- #
    # Mode dégradé – validation des clés top‑level uniquement
    # --------------------------------------------------------------------- #
    violations: List[str] = []
    if isinstance(schema, dict):
        required = [
            k for k, v in schema.items() if isinstance(v, dict) and v.get("required", False)
        ]
        if not required:
            required = list(schema.keys())
        for key in required:
            if key not in data:
                violations.append(f"Clé '{key}' manquante")
    else:
        raise ValidationError("Le schéma fourni n’est pas un objet JSON.")

    return {"valide": not violations, "violations": violations, "denominateur": 1}


def detecter_erreurs(format_: str, texte: str) -> Dict[str, Any]:
    """Détecte la première erreur de syntaxe selon *format_*."""
    fmt = format_.lower()
    if fmt == "json":
        try:
            obj = _json_load(texte)
            return {"valide": True, "erreur": None, "parties_valides": [obj], "denominateur": 1}
        except json.JSONDecodeError as exc:
            return {"valide": False, "erreur": f"JSON invalide : {exc.msg}", "parties_valides": None, "denominateur": 1}
    if fmt == "python":
        try:
            obj = ast.literal_eval(texte)
            return {"valide": True, "erreur": None, "parties_valides": [obj], "denominateur": 1}
        except (SyntaxError, ValueError) as exc:
            return {"valide": False, "erreur": f"Python invalide : {exc}", "parties_valides": None, "denominateur": 1}
    if fmt == "yaml":
        yaml = _load_optional("yaml")
        if yaml is None:
            raise ValidationError("Support YAML indisponible (module absent).")
        try:
            obj = yaml.safe_load(texte)
            return {"valide": True, "erreur": None, "parties_valides": [obj], "denominateur": 1}
        except yaml.YAMLError as exc:
            return {"valide": False, "erreur": f"YAML invalide : {exc}", "parties_valides": None, "denominateur": 1}
    raise ValidationError(f"Format inconnu : {format_}")


def convertir_objet(schema: Dict[str, Any], texte: str) -> Dict[str, Any]:
    """Convertit *texte* en objet Python conforme au *schema* (dégradé : aucune validation)."""
    try:
        obj = _json_load(texte)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"JSON invalide : {exc.msg}") from exc
    return {"objet": obj, "denominateur": 1}


def imposer_format(format_: str, texte: str) -> Dict[str, Any]:
    """Réécrit *texte* dans le format strict demandé."""
    fmt = format_.lower()
    if fmt == "json":
        try:
            obj = _json_load(texte)
            texte_min = _json_dump(obj)
            return {"texte": texte_min, "denominateur": 1}
        except json.JSONDecodeError as exc:
            raise ValidationError(f"JSON invalide : {exc.msg}") from exc
    if fmt == "yaml":
        yaml = _load_optional("yaml")
        if yaml is None:
            raise ValidationError("Support YAML indisponible (module absent).")
        try:
            obj = yaml.safe_load(texte)
            texte_yaml = yaml.safe_dump(obj, sort_keys=True)
            return {"texte": texte_yaml, "denominateur": 1}
        except yaml.YAMLError as exc:
            raise ValidationError(f"YAML invalide : {exc}") from exc
    if fmt == "python":
        try:
            obj = ast.literal_eval(texte)
            texte_py = repr(obj)
            return {"texte": texte_py, "denominateur": 1}
        except (SyntaxError, ValueError) as exc:
            raise ValidationError(f"Python invalide : {exc}") from exc
    raise ValidationError(f"Format inconnu : {format_}")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def _build_parent_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique.",
        default=argparse.SUPPRESS,
    )
    parent.add_argument(
        "--racine",
        type=str,
        help="Chemin racine à utiliser pour les chemins relatifs.",
        default=argparse.SUPPRESS,
    )
    return parent


def main() -> int:
    _reconfigure_streams()
    parent = _build_parent_parser()

    parser = argparse.ArgumentParser(
        description="Contraindre la sortie d’un LLM à un format structuré.",
        epilog="Exemple : {outil} verifier-schema --schema schema.json --texte '{\"a\":1}' --json",
        formatter_class=argparse.RawTextHelpFormatter,
        parents=[parent],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # ---- verifier-schema -------------------------------------------------
    p_vs = subparsers.add_parser(
        "verifier-schema",
        help="Vérifie qu’un texte JSON respecte un schéma.",
        parents=[parent],
    )
    p_vs.add_argument("--schema", required=True, help="Fichier JSON contenant le schéma.")
    group_vs = p_vs.add_mutually_exclusive_group(required=True)
    group_vs.add_argument("--texte", help="Texte à valider.")
    group_vs.add_argument("--fichier", help="Fichier contenant le texte à valider.")

    # ---- detecter-erreurs ------------------------------------------------
    p_de = subparsers.add_parser(
        "detecter-erreurs",
        help="Détecte la première erreur de syntaxe d’un texte.",
        parents=[parent],
    )
    p_de.add_argument("--format", required=True, choices=["json", "yaml", "python"], help="Format attendu.")
    group_de = p_de.add_mutually_exclusive_group(required=True)
    group_de.add_argument("--texte", help="Texte à analyser.")
    group_de.add_argument("--fichier", help="Fichier contenant le texte à analyser.")

    # ---- convertir-objet -------------------------------------------------
    p_co = subparsers.add_parser(
        "convertir-objet",
        help="Convertit un texte JSON en objet Python conforme au schéma.",
        parents=[parent],
    )
    p_co.add_argument("--schema", required=True, help="Fichier JSON contenant le schéma.")
    group_co = p_co.add_mutually_exclusive_group(required=True)
    group_co.add_argument("--texte", help="Texte à convertir.")
    group_co.add_argument("--fichier", help="Fichier contenant le texte à convertir.")

    # ---- imposer-format --------------------------------------------------
    p_if = subparsers.add_parser(
        "imposer-format",
        help="Réécrit le texte dans le format strict demandé.",
        parents=[parent],
    )
    p_if.add_argument("--format", required=True, choices=["json", "yaml", "python"], help="Format cible.")
    group_if = p_if.add_mutually_exclusive_group(required=True)
    group_if.add_argument("--texte", help="Texte à reformater.")
    group_if.add_argument("--fichier", help="Fichier contenant le texte à reformater.")

    args = parser.parse_args()

    # Détermination de la racine
    racine = Path(__file__).resolve().parent
    if hasattr(args, "racine"):
        racine = Path(args.racine).resolve()

    mode_json = getattr(args, "json", False)
    examines: List[str] = []
    global _engine
    _engine = "stdlib"  # remise à zéro pour chaque exécution

    try:
        # ---- verifier-schema ------------------------------------------------
        if args.commande == "verifier-schema":
            schema_path = Path(args.schema)
            schema_path = schema_path if schema_path.is_absolute() else racine / schema_path
            schema_text = schema_path.read_text(encoding="utf-8", errors="replace")
            if "\x00" in schema_text:
                raise InputError("Le schéma semble être binaire.")
            schema_obj = _json_load(schema_text)
            texte = _read_text(getattr(args, "texte", None), getattr(args, "fichier", None), racine)
            examines.append(str(schema_path))
            examines.append(str(getattr(args, "fichier", "")) if getattr(args, "fichier", None) else "texte fourni")
            result = verifier_schema(schema_obj, texte)

        # ---- detecter-erreurs -----------------------------------------------
        elif args.commande == "detecter-erreurs":
            texte = _read_text(getattr(args, "texte", None), getattr(args, "fichier", None), racine)
            examines.append(str(getattr(args, "fichier", "")) if getattr(args, "fichier", None) else "texte fourni")
            result = detecter_erreurs(args.format, texte)

        # ---- convertir-objet ------------------------------------------------
        elif args.commande == "convertir-objet":
            schema_path = Path(args.schema)
            schema_path = schema_path if schema_path.is_absolute() else racine / schema_path
            schema_text = schema_path.read_text(encoding="utf-8", errors="replace")
            if "\x00" in schema_text:
                raise InputError("Le schéma semble être binaire.")
            schema_obj = _json_load(schema_text)
            texte = _read_text(getattr(args, "texte", None), getattr(args, "fichier", None), racine)
            examines.append(str(schema_path))
            examines.append(str(getattr(args, "fichier", "")) if getattr(args, "fichier", None) else "texte fourni")
            result = convertir_objet(schema_obj, texte)

        # ---- imposer-format -------------------------------------------------
        elif args.commande == "imposer-format":
            texte = _read_text(getattr(args, "texte", None), getattr(args, "fichier", None), racine)
            examines.append(str(getattr(args, "fichier", "")) if getattr(args, "fichier", None) else "texte fourni")
            result = imposer_format(args.format, texte)

        else:
            raise AssertionError("Sous‑commande inconnue.")  # ne devrait jamais arriver

        # Construction de la sortie JSON conforme aux exigences
        sortie = {
            "denominateur": result.get("denominateur", 0),
            "examines": _truncate_examines(examines),
            "moteur": _engine,
        }
        for k, v in result.items():
            if k != "denominateur":
                sortie[k] = v

        if mode_json:
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0 if sortie.get("valide", True) else 1
        else:
            # Affichage humain
            if isinstance(sortie.get("valide"), bool):
                sys.stdout.write("Valide\n" if sortie["valide"] else "Invalide\n")
            if "violations" in sortie:
                for v in sortie["violations"]:
                    sys.stdout.write(f"- {v}\n")
            if "erreur" in sortie and sortie["erreur"]:
                sys.stdout.write(f"Erreur : {sortie['erreur']}\n")
            if "texte" in sortie:
                sys.stdout.write(sortie["texte"] + "\n")
            if "objet" in sortie:
                sys.stdout.write(repr(sortie["objet"]) + "\n")
            return 0 if sortie.get("valide", True) else 1

    except NoDataError:
        # Refus : aucun texte à examiner
        if mode_json:
            json.dump({"denominateur": 0, "examines": [], "moteur": _engine}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        sys.stderr.write("denominateur nul : aucun texte fourni.\n")
        return 3
    except InputError as exc:
        sys.stderr.write(f"Erreur d’entrée : {exc}\n")
        return 2
    except ValidationError as exc:
        payload = {
            "valide": False,
            "erreur": str(exc),
            "denominateur": 1,
            "examines": _truncate_examines(examines),
            "moteur": _engine,
        }
        if mode_json:
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 1
        else:
            sys.stderr.write(f"Erreur de validation : {exc}\n")
            return 1
    except Exception as exc:  # pragma: no cover – protection ultime
        sys.stderr.write(f"Erreur inattendue : {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())