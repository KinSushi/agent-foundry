"""
QUESTION      Comment exposer ce résultat via HTTP ?
MESURE        Fusion des outils 'servir_api' et 'mesurer_metriques' en un seul exécutable
              avec deux sous‑commandes distinctes, chacune avec son propre dénominateur
              et ses propres examinés. Recouvrement des modules employés de 1.0.
HYPOTHESES    Le code source à analyser est valide Python 3.14, le système supporte les
              sockets TCP/IP, et les modules standard sont disponibles sans restriction.
LIMITES       Pas de support ASGI natif, pas de streaming, pas de haute performance,
              pas de HTTPS natif. Mode dégradé avec wsgiref si modules tiers absents.
CONTRE-EXEMPLES Utilisation de paramètres inexistants comme all_threads=True dans
              faulthandler ou dedent=True dans Interpreter.exec est évitée.
INVOCATION
    {outil} mesurer --chemin {fichier} --json
DOMAINE       Développement local, tests, environnements contraints où les dépendances
              tierces sont interdites ou optionnelles.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from wsgiref.simple_server import make_server

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = [
    "analyser_source",
    "mesurer_metriques",
    "creer_app_wsgi",
    "lancer_serveur",
    "main",
]


class ArgumentParserJSON(argparse.ArgumentParser):
    """ArgumentParser qui renvoie une erreur JSON quand --json est présent."""

    def error(self, message: str) -> None:  # pragma: no cover
        if "--json" in sys.argv:
            json.dump(
                {"erreur": message, "denominateur": 0},
                sys.stdout,
                ensure_ascii=False,
            )
            sys.stdout.write("\n")
            sys.exit(2)
        super().error(message)


def analyser_source(chemin: Path) -> Dict[str, Any]:
    """Analyse un fichier Python et retourne les résultats."""
    if not chemin.is_file():
        raise ValueError(f"Fichier inexistant ou illisible: {chemin}")

    try:
        with chemin.open("r", encoding="utf-8") as f:
            source = f.read()
        compile(source, str(chemin), "exec")
    except (SyntaxError, UnicodeDecodeError) as e:
        raise ValueError(f"Fichier invalide: {e}")

    return {
        "denominateur": 1,
        "examines": [chemin.name],
        "valide": True,
    }


def mesurer_metriques(chemin: Path) -> Dict[str, Any]:
    """Mesure des métriques sur un fichier Python."""
    if not chemin.is_file():
        raise ValueError(f"Fichier inexistant ou illisible: {chemin}")

    try:
        with chemin.open("r", encoding="utf-8") as f:
            lignes = f.readlines()
    except UnicodeDecodeError as e:
        raise ValueError(f"Fichier illisible: {e}")

    metriques = {
        "lignes": len(lignes),
        "fonctions": sum(1 for ligne in lignes if ligne.strip().startswith("def ")),
        "classes": sum(1 for ligne in lignes if ligne.strip().startswith("class ")),
    }

    return {
        "denominateur": 1,
        "examines": [chemin.name],
        "metriques": metriques,
    }


def creer_app_wsgi(
    racine: Path,
    chemin: Path,
) -> Callable[[Dict[str, Any], Callable], List[bytes]]:
    """Crée une application WSGI pour servir les résultats."""
    def application(environ: Dict[str, Any], start_response: Callable) -> List[bytes]:
        path = environ.get("PATH_INFO", "")

        if path == "/api/analyser":
            try:
                resultat = analyser_source(chemin)
                status = "200 OK"
                response = json.dumps(resultat).encode("utf-8")
            except Exception as e:
                status = "400 Bad Request"
                response = json.dumps({"erreur": str(e)}).encode("utf-8")
        elif path == "/api/mesurer":
            try:
                resultat = mesurer_metriques(chemin)
                status = "200 OK"
                response = json.dumps(resultat).encode("utf-8")
            except Exception as e:
                status = "400 Bad Request"
                response = json.dumps({"erreur": str(e)}).encode("utf-8")
        else:
            status = "404 Not Found"
            response = b'{"erreur": "Endpoint non trouve"}'

        headers = [("Content-Type", "application/json")]
        start_response(status, headers)
        return [response]

    return application


def lancer_serveur(
    app: Callable,
    host: str = "localhost",
    port: int = 8000,
) -> None:
    """Lance le serveur HTTP avec l'application WSGI."""
    with make_server(host, port, app) as httpd:
        print(
            f"Serveur démarré sur http://{host}:{port}",
            file=sys.stderr,
        )
        print("Endpoints disponibles:", file=sys.stderr)
        print("  - /api/analyser : Analyse du fichier", file=sys.stderr)
        print("  - /api/mesurer  : Mesure des métriques", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt du serveur...", file=sys.stderr)
            httpd.shutdown()


def analyser_cli() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    # arguments communs aux sous‑commandes
    parent_commons = argparse.ArgumentParser(add_help=False)
    parent_commons.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie JSON sur stdout.",
    )
    parent_commons.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Répertoire racine pour les imports relatifs.",
    )

    parser = ArgumentParserJSON(
        description="Outil pour exposer des résultats d'analyse et de métriques via HTTP.",
        epilog="Exemple: python servir_api.py servir --chemin mon_fichier.py --host 0.0.0.0 --port 8000",
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    parser_servir = sous_parsers.add_parser(
        "servir",
        help="Exposer les résultats via HTTP.",
        parents=[parent_commons],
    )
    parser_servir.add_argument(
        "--chemin",
        type=Path,
        required=True,
        help="Chemin vers le fichier Python à analyser/mesurer.",
    )
    parser_servir.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Adresse d'écoute (par défaut: localhost).",
    )
    parser_servir.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port d'écoute (par défaut: 8000).",
    )

    parser_mesurer = sous_parsers.add_parser(
        "mesurer",
        help="Mesurer des métriques sur un fichier Python.",
        parents=[parent_commons],
    )
    parser_mesurer.add_argument(
        "--chemin",
        type=Path,
        required=True,
        help="Chemin vers le fichier Python à mesurer.",
    )

    return parser.parse_args()


def _refus_denominator(stderr_msg: str) -> None:
    """Écrit le message de refus sur stderr et sort avec le code 3."""
    print(stderr_msg, file=sys.stderr)
    sys.exit(3)


def main() -> int:
    """Point d'entrée principal de l'outil."""
    args = analyser_cli()

    # Gestion du paramètre --racine
    racine = getattr(args, "racine", RACINE)
    if racine not in sys.path:
        sys.path.insert(0, str(racine))

    json_mode = getattr(args, "json", False)

    if args.commande == "servir":
        if not args.chemin.is_file():
            if json_mode:
                json.dump(
                    {"erreur": f"Fichier inexistant ou illisible: {args.chemin}", "denominateur": 0},
                    sys.stdout,
                    ensure_ascii=False,
                )
                sys.stdout.write("\n")
                _refus_denominator("Denominateur nul : rien à examiner, refus de conclure.")
            else:
                print(f"Erreur: Fichier inexistant ou illisible: {args.chemin}", file=sys.stderr)
                return 1

        if json_mode:
            # Retourne le résultat d'analyse sous forme JSON
            resultat = analyser_source(args.chemin)
            json.dump(resultat, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0

        # Mode serveur (pas de JSON)
        app = creer_app_wsgi(racine, args.chemin)
        lancer_serveur(app, host=args.host, port=args.port)
        return 0

    elif args.commande == "mesurer":
        try:
            resultat = mesurer_metriques(args.chemin)
            if json_mode:
                json.dump(resultat, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
                return 0
            else:
                print(f"Métriques mesurées pour {args.chemin.name}:", file=sys.stdout)
                for k, v in resultat["metriques"].items():
                    print(f"  - {k}: {v}", file=sys.stdout)
                return 0
        except ValueError as e:
            # Refus légitime : rien à examiner
            if json_mode:
                json.dump({"erreur": str(e), "denominateur": 0}, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
                _refus_denominator("Denominateur nul : rien à examiner, refus de conclure.")
            else:
                print(f"Erreur: {e}", file=sys.stderr)
                return 1
        except Exception as e:  # pragma: no cover
            if json_mode:
                json.dump({"erreur": str(e), "denominateur": 0}, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
                return 1
            else:
                print(f"Erreur inattendue: {e}", file=sys.stderr)
                return 1

    else:  # pragma: no cover
        print("Commande inconnue.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())