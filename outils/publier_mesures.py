"""QUESTION
Quel(s) type(s) de métriques Prometheus peut‑on exposer, agréger ou mesurer dans un processus Python ?
MESURE
Observation des métriques via HTTP ou fichiers texte au format Prometheus, avec génération d’URL ou de code instrumenté.
HYPOTHESES
- Le processus cible est exécuté localement.
- Les fichiers fournis sont lisibles en UTF‑8 et contiennent du texte.
LIMITES
- Aucun support réseau natif sans la bibliothèque tierce `prometheus_client`.
- L’agrégation se limite à la concaténation de fichiers texte.
CONTRE-EXEMPLE
Un registre contenant des caractères NUL (`\\0`) est considéré binaire et déclenche un refus.
INVOCATION
    {outil} mesurer --nom duree_tache --fichier {fichier} --json
DOMAINE
Processus Python locaux où l’on souhaite instrumenter ou exposer des métriques Prometheus.
"""

from __future__ import annotations

import argparse
import json
import sys
import importlib.util
from pathlib import Path
from typing import List, Sequence

# ------------------------------------------------------------
# Constantes et utilitaires
# ------------------------------------------------------------
RACINE = Path(__file__).resolve().parent
MAX_EXAMINES = 200
REFUS_WORDS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

class ErreurOutil(Exception):
    """Exception levée par le cœur de l’outil."""

    def __init__(
        self,
        code: int,
        message: str,
        denominator: int = 0,
        examines: Sequence[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.denominator = denominator
        self.examines = list(examines or [])

def _require_prometheus() -> None:
    """Vérifie la présence de `prometheus_client`."""
    try:
        import prometheus_client  # noqa: F401
    except ImportError as exc:
        raise ErreurOutil(
            3,
            "Mode dégradé : la bibliothèque tierce `prometheus_client` est absente.",
            denominator=0,
            examines=[],
        ) from exc

# ------------------------------------------------------------
# Cœur fonctionnel (sans impression)
# ------------------------------------------------------------
def exposer(port: int, endpoint: str = "/metrics") -> str:
    """Démarre un serveur HTTP exposant les métriques Prometheus."""
    _require_prometheus()
    try:
        from prometheus_client import start_http_server  # type: ignore
    except ImportError as exc:  # déjà exclu par _require_prometheus, gardé pour le socle
        raise ErreurOutil(3, "Mode dégradé : prometheus_client absent.", denominator=0, examines=[]) from exc

    try:
        start_http_server(port)
    except OSError as exc:
        raise ErreurOutil(1, f"Port {port} déjà utilisé : {exc}", denominator=0) from exc
    return f"http://localhost:{port}{endpoint}"

def agreger(registries: List[Path], output: Path) -> Path:
    """Concatène les fichiers de registre Prometheus en un seul fichier."""
    _require_prometheus()
    examines: List[str] = []
    content_parts: List[str] = []
    for reg_path in registries:
        if not reg_path.is_file():
            raise ErreurOutil(
                2,
                f"Fichier {reg_path} introuvable.",
                denominator=0,
                examines=examines,
            )
        try:
            texte = reg_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ErreurOutil(
                2,
                f"Impossible de lire {reg_path} : {exc}",
                denominator=0,
                examines=examines,
            ) from exc
        if "\x00" in texte:
            raise ErreurOutil(
                2,
                f"Fichier {reg_path} est binaire.",
                denominator=0,
                examines=examines,
            )
        examines.append(str(reg_path))
        content_parts.append(texte)
    try:
        output.write_text("\n".join(content_parts), encoding="utf-8")
    except OSError as exc:
        raise ErreurOutil(2, f"Impossible d’écrire {output} : {exc}", denominator=0) from exc
    return output

def aiohttp_integration(app_path: Path, port: int) -> str:
    """Intègre un endpoint `/metrics` à une application aiohttp existante."""
    _require_prometheus()
    if importlib.util.find_spec("aiohttp") is None:
        raise ErreurOutil(
            3,
            "Mode dégradé : l'intégration `aiohttp` nécessite le paquet `aiohttp`.",
            denominator=0,
            examines=[],
        )
    spec = importlib.util.spec_from_file_location("module_aio", str(app_path))
    if spec is None or spec.loader is None:
        raise ErreurOutil(3, f"Impossible de charger le module {app_path}.", denominator=0)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore
    except Exception as exc:
        raise ErreurOutil(3, f"Erreur lors de l’import du module {app_path} : {exc}", denominator=0) from exc
    if not hasattr(module, "app"):
        raise ErreurOutil(3, f"Module {app_path} ne contient pas d'application aiohttp.", denominator=0)
    return f"http://localhost:{port}/metrics"

def mesurer(nom: str, type_: str, fichier: Path) -> str:
    """Produit du code Python instrumenté pour mesurer une métrique."""
    if not fichier.is_file():
        raise ErreurOutil(4, f"Fichier {fichier} introuvable.", denominator=0)
    try:
        texte = fichier.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ErreurOutil(4, f"Impossible de lire {fichier} : {exc}", denominator=0) from exc
    if "\x00" in texte:
        raise ErreurOutil(4, f"Fichier {fichier} n'est pas un script Python (binaire).", denominator=0)
    prefix = f"# Mesure : {nom} (type={type_})\n"
    if type_ == "duree":
        instrument = f"with mesurer('{nom}'):\n"
        indented = "\n".join("    " + line for line in texte.splitlines())
        result = prefix + instrument + indented
    else:
        result = prefix + texte
    return result

# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------
def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit la sortie au format JSON unique.",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )

    parser = argparse.ArgumentParser(
        description="Outil de publication de métriques Prometheus.",
        parents=[parent],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    p_exposer = subparsers.add_parser("exposer", help="Expose les métriques via HTTP.", parents=[parent])
    p_exposer.add_argument("--port", type=int, required=True, help="Port d’écoute.")
    p_exposer.add_argument("--endpoint", default="/metrics", help="Chemin de l’endpoint (défaut : /metrics).")

    p_agreger = subparsers.add_parser("agreger", help="Agrège plusieurs registres Prometheus.", parents=[parent])
    p_agreger.add_argument("--registries", nargs="+", type=Path, required=True, help="Chemins vers les fichiers de registre.")
    p_agreger.add_argument("--output", type=Path, required=True, help="Chemin du fichier de sortie agrégé.")

    p_aiohttp = subparsers.add_parser("aiohttp", help="Intègre les métriques à une application aiohttp.", parents=[parent])
    p_aiohttp.add_argument("--app", type=Path, required=True, help="Chemin vers le module contenant `app`.")
    p_aiohttp.add_argument("--port", type=int, required=True, help="Port d’écoute.")

    p_mesurer = subparsers.add_parser("mesurer", help="Génère un contexte managé pour mesurer du code.", parents=[parent])
    p_mesurer.add_argument("--nom", required=True, help="Nom de la métrique.")
    p_mesurer.add_argument("--type", choices=["duree", "compteur", "jauge"], default="duree", help="Type de métrique (défaut : duree).")
    p_mesurer.add_argument("--fichier", type=Path, required=True, help="Chemin vers le script Python à instrumenter.")

    parser.epilog = (
        "Exemple d’appel réel :\n"
        "  publier_mesures.py mesurer --nom duree_tache --fichier script.py --json"
    )
    return parser.parse_args(argv)

def _prepare_path(cible: Path, racine: Path) -> Path:
    return cible if cible.is_absolute() else racine / cible

def _build_json_output(denominator: int, examines: List[str], **kwargs) -> str:
    """Construit systématiquement la sortie JSON avec dénominateur."""
    payload = {
        "denominateur": denominator,
        "examines_tronques": examines[:MAX_EXAMINES],
        **kwargs
    }
    return json.dumps(payload, ensure_ascii=False)

def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        racine = getattr(args, "racine", RACINE)
        json_mode = getattr(args, "json", False)

        if args.commande == "exposer":
            url = exposer(port=args.port, endpoint=args.endpoint)
            output = _build_json_output(1, [], url=url) if json_mode else url

        elif args.commande == "agreger":
            regs = [_prepare_path(p, racine) for p in args.registries]
            out_path = _prepare_path(args.output, racine)
            out_file = agreger(registries=regs, output=out_path)
            output = _build_json_output(len(regs), [str(p) for p in regs], fichier_sortie=str(out_file)) if json_mode else str(out_file)

        elif args.commande == "aiohttp":
            app_path = _prepare_path(args.app, racine)
            url = aiohttp_integration(app_path=app_path, port=args.port)
            output = _build_json_output(1, [str(app_path)], url=url) if json_mode else url

        elif args.commande == "mesurer":
            file_path = _prepare_path(args.fichier, racine)
            code = mesurer(nom=args.nom, type_=args.type, fichier=file_path)
            output = _build_json_output(1, [str(file_path)], fichier=str(file_path)) if json_mode else code

        else:
            raise AssertionError("Commande inconnue.")

        print(output, file=sys.stdout)
        return 0

    except ErreurOutil as err:
        refuse = err.denominator == 0
        if refuse:
            sys.stderr.write(f"denominateur {REFUS_WORDS[0]}\n")
            output = _build_json_output(0, [])
            if json_mode:
                sys.stdout.write(output)
            return 3
        else:
            sys.stderr.write(f"{err.message}\n")
            output = _build_json_output(err.denominator, err.examines, erreur=err.message)
            if json_mode:
                sys.stdout.write(output)
            return err.code if err.code != 0 else 1
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"Erreur inattendue : {exc}\n")
        if json_mode:
            sys.stdout.write(_build_json_output(0, [], erreur=str(exc)))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())