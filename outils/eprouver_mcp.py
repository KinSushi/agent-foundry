"""QUESTION
    Le processus identifié par PID X est‑il réellement bloqué ?
MESURE
    L’état du processus (statut, signaux en attente) observé via les appels système.
HYPOTHÈSES
    Le PID existe, appartient à l’utilisateur courant et le système expose les informations de statut.
LIMITES
    Les conteneurs sans accès à /proc ou les processus appartenant à un autre UID ne sont pas observables.
CONTRE‑EXEMPLE
    Un processus en état « D » (uninterruptible sleep) ne répond pas à SIGCONT ; l’outil le signale comme bloqué mais ne le débloque pas.
INVOCATION
    {outil} unblock --json
    {outil} inventaire {dossier} --json
DOMAINE
    Gestion de processus locaux sous Linux et Windows (via API équivalente)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Configuration d'encodage
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# Constantes
RACINE = Path(__file__).resolve().parent
MAX_EXAMINES = 200
REFUSAL_WORDS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

# --------------------------------------------------------------------------- #
# Exceptions métier
class EprouverError(RuntimeError):
    """Exception levée par le cœur en cas d’erreur métier."""

    def __init__(self, message: str, denominateur: int = 0, examines: Optional[List[Any]] = None):
        super().__init__(message)
        self.message = message
        self.denominateur = denominateur
        self.examines = examines or []

def _get_signal() -> int:
    """Retourne un signal valide selon la plateforme."""
    return getattr(signal, 'SIGCONT', signal.SIGINT)

# --------------------------------------------------------------------------- #
# Fonctions du cœur (logique pure, aucune impression)

def _prepare_output(
    result: Dict[str, Any],
    denominateur: int,
    examines: List[Any],
) -> Dict[str, Any]:
    """Construit le dictionnaire JSON de sortie avec dénominateur garanti."""
    base = {
        "denominateur": denominateur,
        "examines": examines[:MAX_EXAMINES] if examines else [],
    }
    if len(examines) > MAX_EXAMINES:
        base["examines_tronques"] = True
    return {**base, **result}

def core_unblock(pid: int) -> Tuple[Dict[str, Any], int, List[int]]:
    """Envoie SIGCONT au processus indiqué."""
    examines = [pid]
    try:
        os.kill(pid, _get_signal())
    except ProcessLookupError:
        raise EprouverError(f"PID {pid} inconnu.", denominateur=0, examines=examines)
    except PermissionError:
        raise EprouverError(f"Permission refusée pour PID {pid}.", denominateur=0, examines=examines)
    except AttributeError:  # Cas où SIGCONT n'existe pas
        raise EprouverError("Signal SIGCONT non disponible sur cette plateforme.", denominateur=0, examines=examines)
    return {"debloque": True}, 1, examines

def core_inventaire(dossier: Path) -> Tuple[Dict[str, Any], int, List[str]]:
    """Cherche des manifestes MCP dans un dossier et rend le compte trouvé."""
    examines = []
    if not dossier.is_dir():
        raise EprouverError(f"Chemin {dossier} n'est pas un dossier.", denominateur=0, examines=examines)

    manifestes = list(dossier.glob("*.mcp.json"))
    examines = [str(m) for m in manifestes]

    if not manifestes:
        raise EprouverError("Aucun manifeste MCP trouvé.", denominateur=0, examines=examines)

    return {"manifestes": [str(m) for m in manifestes]}, len(manifestes), examines

def core_inspect_server(host: str, port: int) -> Tuple[Dict[str, Any], int, List[Tuple[str, int]]]:
    """Inspecte un serveur via le module mcp.server si disponible."""
    examines = [(host, port)]
    if importlib.util.find_spec("mcp.server") is None:
        raise EprouverError(
            "Fonctionnalité inspect‑server désactivée (module mcp.server absent).",
            denominateur=0,
            examines=examines,
        )
    try:
        from mcp.server import inspect as mcp_inspect  # type: ignore
    except ImportError as exc:
        raise EprouverError(
            f"Import du module mcp.server échoué : {exc}",
            denominateur=0,
            examines=examines,
        )
    try:
        data = mcp_inspect(host, port)
    except Exception as exc:  # pragma: no cover
        raise EprouverError(f"Erreur d’inspection du serveur : {exc}", denominateur=0, examines=examines)
    return {"serveur": data}, 1, examines

def core_negotiate_format(url: str, formats: List[str]) -> Tuple[Dict[str, Any], int, List[str]]:
    """Négocie le format préféré via le module mcp.client si disponible."""
    examines = formats
    if not formats:
        raise EprouverError("Aucun format fourni.", denominateur=0, examines=examines)
    if importlib.util.find_spec("mcp.client") is None:
        raise EprouverError(
            "Fonctionnalité negotiate‑format désactivée (module mcp.client absent).",
            denominateur=0,
            examines=examines,
        )
    try:
        from mcp.client import negotiate as mcp_negotiate  # type: ignore
    except ImportError as exc:
        raise EprouverError(
            f"Import du module mcp.client échoué : {exc}",
            denominateur=0,
            examines=examines,
        )
    try:
        chosen = mcp_negotiate(url, formats)
    except Exception as exc:  # pragma: no cover
        raise EprouverError(f"Erreur de négociation du format : {exc}", denominateur=0, examines=examines)
    return {"format": chosen}, 1, examines

def core_share_proxy(pickle_path: Path) -> Tuple[Dict[str, Any], int, List[str]]:
    """Charge un objet picklé et crée un proxy via mcp.shared si disponible."""
    examines = [str(pickle_path)]
    try:
        with pickle_path.open("rb") as f:
            import pickle
            obj = pickle.load(f)
    except Exception:
        try:
            with pickle_path.open("r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            raise EprouverError("Fichier non sérialisable ou illisible.", denominateur=0, examines=examines)

    if importlib.util.find_spec("mcp.shared") is None:
        raise EprouverError(
            "Fonctionnalité share‑proxy désactivée (module mcp.shared absent).",
            denominateur=0,
            examines=examines,
        )
    try:
        from mcp.shared import proxy as mcp_proxy  # type: ignore
    except ImportError as exc:
        raise EprouverError(
            f"Import du module mcp.shared échoué : {exc}",
            denominateur=0,
            examines=examines,
        )
    try:
        prox = mcp_proxy(obj)
    except Exception as exc:  # pragma: no cover
        raise EprouverError(f"Erreur de création du proxy : {exc}", denominateur=0, examines=examines)

    return {"proxy": True}, 1, examines

def core_validate_type(value_json: str, schema_json: str) -> Tuple[Dict[str, Any], int, List[str]]:
    """Valide une valeur contre un schéma via mcp.types si disponible."""
    examines = [value_json, schema_json]
    try:
        value = json.loads(value_json)
        schema = json.loads(schema_json)
    except json.JSONDecodeError as exc:
        raise EprouverError(f"JSON invalide : {exc}", denominateur=0, examines=examines)

    if importlib.util.find_spec("mcp.types") is None:
        raise EprouverError(
            "Fonctionnalité validate‑type désactivée (module mcp.types absent).",
            denominateur=0,
            examines=examines,
        )
    try:
        from mcp.types import validate as mcp_validate  # type: ignore
    except ImportError as exc:
        raise EprouverError(
            f"Import du module mcp.types échoué : {exc}",
            denominateur=0,
            examines=examines,
        )
    try:
        valid = mcp_validate(value, schema)
    except Exception as exc:  # pragma: no cover
        raise EprouverError(f"Erreur de validation : {exc}", denominateur=0, examines=examines)

    return {"valid": bool(valid)}, 1, examines

# --------------------------------------------------------------------------- #
# Gestion de la CLI

def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )

def main() -> int:
    parent_parser = argparse.ArgumentParser(add_help=False)
    _add_common_arguments(parent_parser)

    parser = argparse.ArgumentParser(
        description="Outil d’évaluation de processus et services MCP.",
        prog="eprouver_mcp.py",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-v", "--version", action="version", version="eprouver_mcp 1.0")
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # ---- unblock ---------------------------------------------------------
    p_unblock = subparsers.add_parser(
        "unblock",
        parents=[parent_parser],
        help="Envoie SIGCONT à un processus bloqué.",
        description="Envoie SIGCONT à un processus identifié par son PID.",
        epilog="Exemple :\n  eprouver_mcp.py unblock --pid 1234 --json",
    )
    p_unblock.add_argument(
        "--pid",
        type=int,
        default=os.getpid(),
        help="PID du processus à débloquer (défaut : PID du processus actuel).",
    )

    # ---- inventaire ------------------------------------------------------
    p_inventaire = subparsers.add_parser(
        "inventaire",
        parents=[parent_parser],
        help="Cherche des manifestes MCP dans un dossier.",
        description="Cherche des fichiers *.mcp.json dans un dossier et rend le compte trouvé.",
        epilog="Exemple :\n  eprouver_mcp.py inventaire {dossier} --json",
    )
    p_inventaire.add_argument(
        "dossier",
        type=Path,
        help="Dossier à examiner pour des manifestes MCP.",
    )

    # ---- inspect-server ---------------------------------------------------
    p_inspect = subparsers.add_parser(
        "inspect-server",
        parents=[parent_parser],
        help="Inspecte un serveur MCP.",
        description="Récupère les métriques d’un serveur via le module mcp.server.",
    )
    p_inspect.add_argument("--host", required=True, help="Nom d’hôte du serveur.")
    p_inspect.add_argument("--port", required=True, type=int, help="Port du serveur.")

    # ---- negotiate-format -------------------------------------------------
    p_negotiate = subparsers.add_parser(
        "negotiate-format",
        parents=[parent_parser],
        help="Négocie le format préféré avec un service MCP.",
    )
    p_negotiate.add_argument("--url", required=True, help="URL du service.")
    p_negotiate.add_argument(
        "--formats",
        required=True,
        help="Liste de formats séparés par des virgules, ex. json,xml.",
    )

    # ---- share-proxy ------------------------------------------------------
    p_share = subparsers.add_parser(
        "share-proxy",
        parents=[parent_parser],
        help="Crée un proxy à partir d’un fichier picklé.",
    )
    p_share.add_argument("--object", required=True, help="Chemin du fichier contenant l’objet sérialisé.")

    # ---- validate-type ----------------------------------------------------
    p_validate = subparsers.add_parser(
        "validate-type",
        parents=[parent_parser],
        help="Valide une valeur JSON contre un schéma JSON.",
    )
    p_validate.add_argument("--value", required=True, help="Valeur JSON à valider.")
    p_validate.add_argument("--schema", required=True, help="Schéma JSON.")

    args = parser.parse_args()

    # Gestion du paramètre racine
    racine = getattr(args, "racine", None)
    base_path = racine.resolve() if racine is not None else RACINE

    # Dispatch
    try:
        if args.commande == "unblock":
            result, denom, examines = core_unblock(args.pid)
        elif args.commande == "inventaire":
            dossier = (base_path / args.dossier) if not args.dossier.is_absolute() else args.dossier
            result, denom, examines = core_inventaire(dossier)
        elif args.commande == "inspect-server":
            result, denom, examines = core_inspect_server(args.host, args.port)
        elif args.commande == "negotiate-format":
            fmt_list = [f.strip() for f in args.formats.split(",") if f.strip()]
            result, denom, examines = core_negotiate_format(args.url, fmt_list)
        elif args.commande == "share-proxy":
            path = (base_path / args.object) if not Path(args.object).is_absolute() else Path(args.object)
            result, denom, examines = core_share_proxy(path)
        elif args.commande == "validate-type":
            result, denom, examines = core_validate_type(args.value, args.schema)
        else:  # pragma: no cover
            raise EprouverError("Commande inconnue.", denominateur=0, examines=[])
    except EprouverError as err:
        if args.json:
            out = _prepare_output({"error": err.message}, err.denominateur, err.examines)
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        if err.denominateur == 0:
            word = next((w for w in REFUSAL_WORDS if w in err.message.lower()), "refuse")
            sys.stderr.write(f"{err.message}\n")
            sys.stderr.write(f"denominateur {word}\n")
            return 3
        return 1

    if args.json:
        out = _prepare_output(result, denom, examines)
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0

__all__ = [
    "main",
    "core_unblock",
    "core_inventaire",
    "core_inspect_server",
    "core_negotiate_format",
    "core_share_proxy",
    "core_validate_type",
    "EprouverError",
]

if __name__ == "__main__":
    raise SystemExit(main())