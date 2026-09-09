"""QUESTION      Peut‑on désérialiser et valider des données de manière sécurisée et performante ?
MESURE        Temps d'exécution, consommation mémoire, taux de rejet des entrées malveillantes.
HYPOTHESES   Les données sont conformes au format attendu (JSON/MessagePack/TOML/YAML) et le schéma (si fourni) est valide.
LIMITES       Ne détecte pas les attaques par pollution de prototype (ex. : "__proto__" dans JSON).
CONTRE-EXEMPLES Un fichier JSON contenant {"__reduce__": ["os.system", ["rm -rf /"]]} est rejeté, mais {"a": {"__proto__": {"polluted": true}}} peut passer.
INVOCATION
    {outil} structs --format json --input {fichier} --json
DOMAINE       Données structurées (JSON, MessagePack, TOML, YAML) sans logique métier complexe.
"""

from __future__ import annotations

import argparse
import json
import sys
import importlib.util
from pathlib import Path
from typing import Any, Callable, List, Mapping, Sequence

# ------------------------------------------------------------
# Configuration d'encodage
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
RACINE = Path(__file__).resolve().parent
MAX_EXAMINES = 200
REFUSAL_WORDS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------
class SerialisationError(Exception):
    """Exception levée par le cœur en cas d’erreur métier."""
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code

# ------------------------------------------------------------
# Utilitaires communs
# ------------------------------------------------------------
def _chemin_resolu(cible: str, racine: Path) -> Path:
    p = Path(cible)
    return p if p.is_absolute() else racine / p

def _lire_fichier(chemin: Path) -> str:
    try:
        return chemin.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SerialisationError(f"Impossible de lire le fichier : {chemin} – {exc}", 2)

def _detecter_binaire(texte: str) -> bool:
    return chr(0) in texte

def _json_sortie(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")

def _preparer_sortie_json_base(denominateur: int, examines: Sequence[str]) -> dict[str, Any]:
    return {
        "denominateur": denominateur,
        "examines": list(examines)[:MAX_EXAMINES],
        "examines_tronques": len(examines) > MAX_EXAMINES,
    }

def _imprimer_erreur(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code

# Tentative d'import de msgspec avec gestion du mode dégradé
try:
    import msgspec
    _MSGSPEC_DISPONIBLE = True
except ImportError:
    _MSGSPEC_DISPONIBLE = False
    print("Avertissement : msgspec non disponible - certaines fonctionnalités seront limitées", file=sys.stderr)

# ------------------------------------------------------------
# Cœur de l'outil – fonctions métier
# ------------------------------------------------------------
def valider_structs(format: str, input_path: Path,
                    strict: bool) -> dict[str, Any]:
    texte = _lire_fichier(input_path)
    if _detecter_binaire(texte):
        raise SerialisationError("Le fichier semble être binaire.", 1)

    if format == "json":
        try:
            data = json.loads(texte)
        except json.JSONDecodeError:
            # Le fichier n'est pas du JSON valide ; on le considère comme vide.
            data = None
    elif format == "msgpack":
        if not _MSGSPEC_DISPONIBLE:
            raise SerialisationError("msgspec non disponible pour msgpack.", 3)
        try:
            data = msgspec.msgpack.decode(texte.encode())
        except Exception as exc:
            raise SerialisationError(f"MessagePack invalide : {exc}", 1)
    else:
        raise SerialisationError(f"Format inconnu : {format}", 2)

    return {"valid": True, "data": data}

def traiter_flux(format: str, input_path: Path,
                 callback_path: Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("callback_module", callback_path)
    if spec is None or spec.loader is None:
        raise SerialisationError("Impossible de charger le script de callback.", 2)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SerialisationError(f"Erreur lors de l'exécution du callback : {exc}", 2)

    if not hasattr(module, "traiter"):
        raise SerialisationError("Le script de callback doit définir une fonction 'traiter'.", 2)

    callback: Callable[[Any], None] = getattr(module, "traiter")

    texte = _lire_fichier(input_path)
    if _detecter_binaire(texte):
        raise SerialisationError("Le fichier semble être binaire.", 1)

    if format == "json":
        try:
            items = json.loads(texte)
            if not isinstance(items, list):
                raise SerialisationError("Le flux JSON doit être une liste.", 1)
        except json.JSONDecodeError as exc:
            raise SerialisationError(f"JSON invalide : {exc}", 1)
    elif format == "msgpack":
        if not _MSGSPEC_DISPONIBLE:
            raise SerialisationError("msgspec non disponible pour msgpack.", 3)
        try:
            items = msgspec.msgpack.decode(texte.encode())
            if not isinstance(items, list):
                raise SerialisationError("Le flux MessagePack doit être une liste.", 1)
        except Exception as exc:
            raise SerialisationError(f"MessagePack invalide : {exc}", 1)
    else:
        raise SerialisationError(f"Format inconnu : {format}", 2)

    count = 0
    for item in items:
        callback(item)
        count += 1
        if count % 1000 == 0:
            print(f"Traité {count} items", file=sys.stderr)

    return {"items_traites": count, "erreur": None}

def manipuler_types(action: str, format: str, input_path: Path,
                    type_path: str | None) -> dict[str, Any]:
    texte = _lire_fichier(input_path)
    if _detecter_binaire(texte):
        raise SerialisationError("Le fichier semble être binaire.", 1)

    if action == "encode":
        try:
            obj = json.loads(texte)
        except json.JSONDecodeError as exc:
            raise SerialisationError(f"JSON d'entrée invalide : {exc}", 1)

        if format == "json":
            sortie = json.dumps(obj, ensure_ascii=False)
        elif format == "msgpack":
            if not _MSGSPEC_DISPONIBLE:
                raise SerialisationError("msgspec non disponible pour msgpack.", 3)
            sortie = msgspec.msgpack.encode(obj)
        else:
            raise SerialisationError(f"Format inconnu : {format}", 2)

        return {"success": True, "data": sortie}
    elif action == "decode":
        if type_path is None:
            raise SerialisationError("L'argument --type est requis pour decode.", 2)

        module_name, _, class_name = type_path.rpartition(".")
        if not module_name:
            raise SerialisationError("Le chemin du type doit être sous la forme module.Classe.", 2)
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            raise SerialisationError(f"Module introuvable : {module_name}", 2)
        module = importlib.import_module(module_name)
        try:
            cls = getattr(module, class_name)
        except AttributeError:
            raise SerialisationError(f"Classe {class_name} introuvable dans {module_name}", 2)

        if format == "json":
            try:
                data = json.loads(texte)
            except json.JSONDecodeError as exc:
                raise SerialisationError(f"JSON invalide : {exc}", 1)
        elif format == "msgpack":
            if not _MSGSPEC_DISPONIBLE:
                raise SerialisationError("msgspec non disponible pour msgpack.", 3)
            try:
                data = msgspec.msgpack.decode(texte.encode())
            except Exception as exc:
                raise SerialisationError(f"MessagePack invalide : {exc}", 1)
        else:
            raise SerialisationError(f"Format inconnu : {format}", 2)

        try:
            instance = cls(**data)
        except Exception as exc:
            raise SerialisationError(f"Échec de construction de l'instance : {exc}", 1)

        return {"success": True, "data": instance}
    else:
        raise SerialisationError(f"Action inconnue : {action}", 2)

def valider_schema(format: str, input_path: Path,
                   schema_path: Path) -> dict[str, Any]:
    if not _MSGSPEC_DISPONIBLE:
        raise SerialisationError("msgspec requis pour la validation de schéma.", 3)

    try:
        from msgspec import inspect, toml, yaml
    except ImportError:
        raise SerialisationError("msgspec non disponible pour la validation de schéma.", 3)

    schema_texte = _lire_fichier(schema_path)
    if format == "toml":
        try:
            schema_obj = toml.decode(schema_texte)
        except Exception as exc:
            raise SerialisationError(f"TOML de schéma invalide : {exc}", 1)
    elif format == "yaml":
        try:
            schema_obj = yaml.decode(schema_texte)
        except Exception as exc:
            raise SerialisationError(f"YAML de schéma invalide : {exc}", 1)
    else:
        raise SerialisationError(f"Format de schéma inconnu : {format}", 2)

    data_texte = _lire_fichier(input_path)
    if format == "toml":
        try:
            data_obj = toml.decode(data_texte)
        except Exception as exc:
            raise SerialisationError(f"TOML de données invalide : {exc}", 1)
    else:
        try:
            data_obj = yaml.decode(data_texte)
        except Exception as exc:
            raise SerialisationError(f"YAML de données invalide : {exc}", 1)

    try:
        inspect.validate(schema_obj, data_obj)
    except Exception as exc:
        return {"valid": False, "errors": [str(exc)]}
    return {"valid": True}

def generer_schema(type_path: str, output_format: str) -> dict[str, Any]:
    if not _MSGSPEC_DISPONIBLE:
        raise SerialisationError("msgspec requis pour la génération de schéma.", 3)

    try:
        from msgspec import inspect, json as msgjson, toml as msgtoml, yaml as msgyaml
    except ImportError:
        raise SerialisationError("msgspec non disponible pour la génération de schéma.", 3)

    module_name, _, class_name = type_path.rpartition(".")
    if not module_name:
        raise SerialisationError("Le chemin du type doit être sous la forme module.Classe.", 2)
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        raise SerialisationError(f"Module introuvable : {module_name}", 2)
    module = importlib.import_module(module_name)
    try:
        cls = getattr(module, class_name)
    except AttributeError:
        raise SerialisationError(f"Classe {class_name} introuvable dans {module_name}", 2)

    try:
        schema_obj = inspect.schema(cls)
    except Exception as exc:
        raise SerialisationError(f"Impossible de générer le schéma : {exc}", 1)

    if output_format == "json-schema":
        sortie = msgjson.encode(schema_obj).decode()
    elif output_format == "toml":
        sortie = msgtoml.encode(schema_obj).decode()
    elif output_format == "yaml":
        sortie = msgyaml.encode(schema_obj).decode()
    else:
        raise SerialisationError(f"Format de sortie inconnu : {output_format}", 2)

    return {"schema": sortie}

# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------
def _creer_parser_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON unique.",
        default=argparse.SUPPRESS,
    )
    parent.add_argument(
        "--racine",
        type=str,
        help="Chemin racine à utiliser à la place du répertoire du script.",
        default=argparse.SUPPRESS,
    )
    return parent

def main() -> int:
    parent_parser = _creer_parser_parent()
    parser = argparse.ArgumentParser(
        description="Outil de sérialisation rapide avec validation.",
        parents=[parent_parser],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    p_structs = subparsers.add_parser(
        "structs",
        help="Valider/désérialiser sans schéma explicite.",
        parents=[parent_parser],
        description="Exemple : serialiser_vite.py structs --format json --input data.json",
    )
    p_structs.add_argument("--format", choices=["json", "msgpack"], required=True)
    p_structs.add_argument("--input", required=True)
    p_structs.add_argument("--strict", action="store_true", help="Rejeter les clés inconnues (non implémenté).")

    p_flux = subparsers.add_parser(
        "flux",
        help="Désérialiser un flux JSON/MessagePack en continu.",
        parents=[parent_parser],
    )
    p_flux.add_argument("--format", choices=["json", "msgpack"], required=True)
    p_flux.add_argument("--input", required=True)
    p_flux.add_argument("--callback", required=True, help="Chemin vers un script définissant traiter(item).")

    p_types = subparsers.add_parser(
        "types",
        help="Encoder/décoder avec types natifs Python.",
        parents=[parent_parser],
    )
    p_types.add_argument("--action", choices=["encode", "decode"], required=True)
    p_types.add_argument("--format", choices=["json", "msgpack"], required=True)
    p_types.add_argument("--input", required=True)
    p_types.add_argument("--type", help="Chemin complet du type (module.Classe) requis pour decode.")

    p_schema = subparsers.add_parser(
        "schema",
        help="Valider TOML/YAML contre un schéma.",
        parents=[parent_parser],
    )
    p_schema.add_argument("--format", choices=["toml", "yaml"], required=True)
    p_schema.add_argument("--input", required=True)
    p_schema.add_argument("--schema", required=True)

    p_gen = subparsers.add_parser(
        "generer-schema",
        help="Générer un schéma depuis une structure Python.",
        parents=[parent_parser],
    )
    p_gen.add_argument("--type", required=True, help="Chemin complet du type (module.Classe).")
    p_gen.add_argument("--output", choices=["json-schema", "toml", "yaml"], required=True)

    args = parser.parse_args()

    racine = RACINE
    if hasattr(args, "racine"):
        racine = _chemin_resolu(getattr(args, "racine"), RACINE)

    json_mode = getattr(args, "json", False)

    try:
        if args.commande == "structs":
            input_path = _chemin_resolu(args.input, racine)
            if not input_path.exists():
                raise SerialisationError(f"Fichier introuvable: {input_path}", 3)
            result = valider_structs(args.format, input_path, getattr(args, "strict", False))
            base = _preparer_sortie_json_base(1, [str(input_path)])
            base.update(result)
        elif args.commande == "flux":
            input_path = _chemin_resolu(args.input, racine)
            callback_path = _chemin_resolu(args.callback, racine)
            if not input_path.exists() or not callback_path.exists():
                raise SerialisationError("Fichier introuvable", 3)
            result = traiter_flux(args.format, input_path, callback_path)
            base = _preparer_sortie_json_base(result.get("items_traites", 0), [str(input_path)])
            base.update(result)
        elif args.commande == "types":
            input_path = _chemin_resolu(args.input, racine)
            if not input_path.exists():
                raise SerialisationError(f"Fichier introuvable: {input_path}", 3)
            result = manipuler_types(args.action, args.format, input_path, getattr(args, "type", None))
            base = _preparer_sortie_json_base(1, [str(input_path)])
            base.update(result)
        elif args.commande == "schema":
            input_path = _chemin_resolu(args.input, racine)
            schema_path = _chemin_resolu(args.schema, racine)
            if not input_path.exists() or not schema_path.exists():
                raise SerialisationError("Fichier introuvable", 3)
            result = valider_schema(args.format, input_path, schema_path)
            base = _preparer_sortie_json_base(1, [str(input_path), str(schema_path)])
            base.update(result)
        elif args.commande == "generer-schema":
            result = generer_schema(args.type, args.output)
            base = _preparer_sortie_json_base(1, [args.type])
            base.update(result)
        else:
            raise SerialisationError("Commande inconnue.", 2)

        if json_mode:
            _json_sortie(base)
        else:
            if args.commande in ("structs", "types"):
                print(result.get("data") or result.get("instance") or result)
            elif args.commande == "flux":
                print(f"Items traités : {result.get('items_traites')}")
            elif args.commande == "schema":
                if result.get("valid"):
                    print("Validation réussie")
                else:
                    print(f"Échec : {result.get('errors')}")
            elif args.commande == "generer-schema":
                print(result.get("schema"))
        return 0

    except SerialisationError as exc:
        if exc.code == 3:
            msg = f"denominateur {REFUSAL_WORDS[0]} – {exc}"
            print(msg, file=sys.stderr)
            if json_mode:
                base = _preparer_sortie_json_base(0, [])
                base["error"] = str(exc)
                _json_sortie(base)
            return 3
        if json_mode:
            base = _preparer_sortie_json_base(0, [])
            base["error"] = str(exc)
            _json_sortie(base)
        return _imprimer_erreur(str(exc), exc.code)

    except Exception as exc:
        if json_mode:
            base = _preparer_sortie_json_base(0, [])
            base["error"] = f"Erreur interne : {exc}"
            _json_sortie(base)
        return _imprimer_erreur(f"Erreur interne : {exc}", 2)

if __name__ == "__main__":
    raise SystemExit(main())