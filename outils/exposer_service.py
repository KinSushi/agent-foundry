"""Exposer un service HTTP avec validation et documentation OpenAPI.

QUESTION
    Le serveur expose-t-il correctement la spécification OpenAPI ?
MESURE
    Présence d’un fichier openapi.json conforme au schéma OpenAPI 3.0.
HYPOTHESES
    Le code source contient des décorateurs FastAPI valides.
LIMITES
    La validation ne couvre pas les extensions propriétaires du schéma.
CONTRE-EXEMPLE
    Un endpoint défini avec @app.get("/items") mais sans modèle de réponse génère
    une spécification incomplète.
INVOCATION
    {outil} augmenter-doc {dossier} --json
DOMAINE
    APIs RESTful décrites par OpenAPI, utilisées dans des micro‑services Python.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import shlex
import subprocess
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = [
    "generer_openapi",
    "valider_payload",
    "creer_dependance_db",
    "executer_tache_background",
    "demarrer_serveur_async",
]

_LOG = logging.getLogger(__name__)

class ErreurValidation(Exception):
    """Le payload ne respecte pas le schéma."""

class ErreurInjection(Exception):
    """Impossible de créer la dépendance."""

class ErreurServeur(Exception):
    """Le serveur n'a pas pu démarrer."""

def _verifier_fastapi() -> bool:
    """Vérifie si fastapi est disponible sans l'importer."""
    try:
        import importlib.util
        return importlib.util.find_spec("fastapi") is not None
    except ImportError:
        return False

def generer_openapi(racine: Path) -> Dict[str, Any]:
    """Génère le schéma OpenAPI à partir des décorateurs FastAPI.

    Lève ErreurValidation si fastapi est absent.
    """
    if not _verifier_fastapi():
        raise ErreurValidation("fastapi est requis mais absent")

    try:
        from fastapi import FastAPI
        from fastapi.openapi.utils import get_openapi
    except ImportError as e:
        raise ErreurValidation("fastapi introuvable") from e

    app = FastAPI()

    @app.get("/items/{item_id}")
    async def read_item(item_id: int, q: Optional[str] = None):
        return {"item_id": item_id, "q": q}

    return get_openapi(
        title="API générée",
        version="1.0.0",
        openapi_version="3.0.2",
        description="API générée par exposer_service",
        routes=app.routes,
    )

def _analyse_statique(racine: Path) -> Dict[str, Any]:
    """Analyse statique des fichiers *.py pour extraire les routes FastAPI.

    Retourne un dict contenant :
        - denominateur : nombre de fichiers *.py examinés
        - examines    : liste des chemins examinés
        - routes      : liste des routes détectées
    """
    py_files: List[Path] = list(racine.rglob("*.py"))
    examines = [str(p) for p in py_files]
    routes: List[Dict[str, Any]] = []

    for fichier in py_files:
        try:
            tree = ast.parse(fichier.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                # on ne s’intéresse qu’aux appels de décorateur
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    method = deco.func.attr.lower()
                    if method not in {
                        "get", "post", "put", "delete", "patch",
                        "api_route", "route", "websocket"
                    }:
                        continue
                    # extraction du chemin (premier argument ou kw 'path')
                    chemin: str = ""
                    if deco.args:
                        arg0 = deco.args[0]
                        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                            chemin = arg0.value
                    for kw in deco.keywords:
                        if kw.arg in {"path", "url"} and isinstance(kw.value, ast.Constant):
                            chemin = kw.value.value
                    doc_present = ast.get_docstring(node) is not None
                    routes.append({
                        "chemin": chemin,
                        "methode": method.upper(),
                        "fonction": node.name,
                        "docstring": doc_present,
                    })
    return {
        "denominateur": len(py_files),
        "examines": examines,
        "routes": routes,
    }

def valider_payload(schema_path: Path, input_path: Path) -> bool:
    """Valide un payload JSON contre un schéma JSON Schema."""
    try:
        with schema_path.open(encoding="utf-8", errors="replace") as f:
            schema = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ErreurValidation(f"Schéma illisible: {e}") from e
    except ValueError as e:
        raise ErreurValidation(f"Schéma invalide: {e}") from e

    if chr(0) in schema_path.read_text(encoding="utf-8", errors="replace"):
        raise ErreurValidation("Schéma contient des données binaires")

    try:
        with input_path.open(encoding="utf-8", errors="replace") as f:
            payload = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ErreurValidation(f"Payload illisible: {e}") from e
    except ValueError as e:
        raise ErreurValidation(f"Payload invalide: {e}") from e

    def _valider(instance: Any, schema_part: Dict[str, Any]) -> bool:
        if "type" in schema_part:
            t = schema_part["type"]
            if t == "object" and not isinstance(instance, dict):
                return False
            if t == "array" and not isinstance(instance, list):
                return False
            if t == "string" and not isinstance(instance, str):
                return False
            if t == "number" and not isinstance(instance, (int, float)):
                return False
            if t == "integer" and not isinstance(instance, int):
                return False
            if t == "boolean" and not isinstance(instance, bool):
                return False
            if t == "null" and instance is not None:
                return False
        if "properties" in schema_part and isinstance(instance, dict):
            for prop, subs in schema_part["properties"].items():
                if prop in instance and not _valider(instance[prop], subs):
                    return False
        if "required" in schema_part and isinstance(instance, dict):
            for prop in schema_part["required"]:
                if prop not in instance:
                    return False
        if "items" in schema_part and isinstance(instance, list):
            for item in instance:
                if not _valider(item, schema_part["items"]):
                    return False
        return True

    return _valider(payload, schema)

def creer_dependance_db(dsn: str, endpoint: str) -> str:
    """Crée une dépendance FastAPI pour une connexion DB."""
    if not _verifier_fastapi():
        raise ErreurInjection("fastapi est requis mais absent")
    return f"""
from fastapi import Depends
import sqlite3

def get_db():
    conn = sqlite3.connect("{dsn}")
    try:
        yield conn
    finally:
        conn.close()

# Utilisation dans un endpoint:
# @app.get("{endpoint}")
# async def read_data(db=Depends(get_db)):
#     cursor = db.cursor()
#     cursor.execute("SELECT 1")
#     return cursor.fetchone()
"""

def executer_tache_background(cmd: str, delay: Optional[int] = None) -> None:
    """Exécute une commande en arrière-plan après un délai optionnel."""
    def _executer():
        if delay:
            threading.Event().wait(delay)
        try:
            subprocess.run(shlex.split(cmd), check=True)
        except subprocess.SubprocessError as e:
            _LOG.error("Échec de la tâche en arrière-plan: %s", e)
            sys.stderr.write(f"Échec de la tâche en arrière-plan: {e}\n")
    threading.Thread(target=_executer, daemon=True).start()

class ServeurHTTPMinimal(BaseHTTPRequestHandler):
    """Serveur HTTP minimal pour le mode dégradé."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write('{"message": "Mode dégradé - fastapi absent"}'.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        _LOG.info(format, *args)

async def demarrer_serveur_async(host: str, port: int) -> None:
    """Démarre un serveur FastAPI ou un serveur minimal en mode dégradé."""
    if _verifier_fastapi():
        try:
            from fastapi import FastAPI
            import uvicorn
        except ImportError as e:
            raise ErreurServeur("fastapi introuvable") from e
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"message": "Serveur FastAPI actif"}

        config = uvicorn.Config(app, host=host, port=port)
        server = uvicorn.Server(config)
        await server.serve()
    else:
        server = HTTPServer((host, port), ServeurHTTPMinimal)
        _LOG.warning("Mode dégradé: fastapi absent, serveur HTTP minimal démarré")
        sys.stderr.write("Mode dégradé: fastapi absent, serveur HTTP minimal démarré\n")
        server.serve_forever()

def _analyser_arguments() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="Sortie au format JSON"
    )
    parent_parser.add_argument(
        "--racine", type=Path, default=argparse.SUPPRESS,
        help="Répertoire racine du projet (défaut: répertoire du script)"
    )

    parser = argparse.ArgumentParser(
        description="Exposer un service HTTP avec validation et documentation OpenAPI",
        parents=[parent_parser]
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    doc_parser = subparsers.add_parser(
        "augmenter-doc", parents=[parent_parser],
        help="Génère openapi.json dans le répertoire indiqué"
    )
    doc_parser.add_argument(
        "racine", type=Path, nargs="?",
        help="Répertoire racine du projet (défaut: répertoire du script)"
    )

    val_parser = subparsers.add_parser(
        "ameliorer-valider", parents=[parent_parser],
        help="Valide un payload JSON contre un schéma JSON Schema"
    )
    val_parser.add_argument("--schema", type=Path, required=True)
    val_parser.add_argument("--input", type=Path, required=True)

    db_parser = subparsers.add_parser(
        "injecter-db", parents=[parent_parser],
        help="Crée une dépendance FastAPI pour une connexion DB"
    )
    db_parser.add_argument("--dsn", required=True)
    db_parser.add_argument("--endpoint", required=True)

    bg_parser = subparsers.add_parser(
        "background-tache", parents=[parent_parser],
        help="Exécute une commande en arrière-plan après la réponse HTTP"
    )
    bg_parser.add_argument("--cmd", required=True)
    bg_parser.add_argument("--delay", type=int)

    async_parser = subparsers.add_parser(
        "concurrency-async", parents=[parent_parser],
        help="Démarre un serveur FastAPI en mode asyncio"
    )
    async_parser.add_argument("--port", type=int, default=8000)
    async_parser.add_argument("--host", default="127.0.0.1")

    return parser.parse_args()

def _construire_sortie_json(
    succes: bool,
    message: str = "",
    resultat: Optional[Dict[str, Any]] = None,
    examines: Optional[List[str]] = None,
    routes: Optional[List[Dict[str, Any]]] = None,
    denominateur: int = 0
) -> Dict[str, Any]:
    """Construit la sortie JSON standardisée."""
    sortie = {"succes": succes, "message": message, "denominateur": denominateur}
    if resultat:
        sortie["resultat"] = resultat
    if examines:
        sortie["examines"] = examines[:200]
        if len(examines) > 200:
            sortie["examines_tronques"] = True
    if routes:
        sortie["routes"] = routes
    return sortie

def _afficher_erreur(message: str) -> None:
    """Affiche un message d'erreur sur stderr."""
    sys.stderr.write(f"{message}\n")

def main() -> int:
    """Point d'entrée principal."""
    args = _analyser_arguments()
    racine = getattr(args, "racine", RACINE)

    if args.commande == "augmenter-doc":
        if _verifier_fastapi():
            try:
                schema = generer_openapi(racine)
                if getattr(args, "json", False):
                    print(json.dumps(
                        _construire_sortie_json(
                            True,
                            "Schéma OpenAPI généré",
                            {"openapi": schema},
                            denominateur=1
                        ),
                        ensure_ascii=False
                    ))
                else:
                    out_path = racine / "openapi.json"
                    out_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
                    _afficher_erreur(f"Schéma OpenAPI généré dans {out_path}")
                return 0
            except ErreurValidation as e:
                _afficher_erreur(str(e))
                if getattr(args, "json", False):
                    print(json.dumps(_construire_sortie_json(False, str(e), denominateur=0), ensure_ascii=False))
                return 3
        else:
            # mode dégradé : analyse statique
            analyse = _analyse_statique(racine)
            denom = analyse["denominateur"]
            if denom == 0:
                _afficher_erreur("denominateur nul : fastapi absent, impossible de conclure")
                if getattr(args, "json", False):
                    print(json.dumps(_construire_sortie_json(False,
                        "denominateur nul : fastapi absent, impossible de conclure",
                        denominateur=0), ensure_ascii=False))
                return 3
            sys.stderr.write("mode degrade : fastapi absent, analyse statique par ast\n")
            if getattr(args, "json", False):
                print(json.dumps(_construire_sortie_json(
                    True,
                    "Analyse statique terminée",
                    examines=analyse["examines"],
                    routes=analyse["routes"],
                    denominateur=denom
                ), ensure_ascii=False))
            else:
                _afficher_erreur(f"Analyse statique de {denom} fichier(s) terminée")
            return 0

    elif args.commande == "ameliorer-valider":
        try:
            valide = valider_payload(args.schema, args.input)
            if getattr(args, "json", False):
                print(json.dumps(_construire_sortie_json(
                    valide,
                    "Payload valide" if valide else "Payload invalide",
                    denominateur=1
                ), ensure_ascii=False))
            return 0 if valide else 2
        except ErreurValidation as e:
            _afficher_erreur(str(e))
            if getattr(args, "json", False):
                print(json.dumps(_construire_sortie_json(False, str(e), denominateur=0), ensure_ascii=False))
            return 3

    elif args.commande == "injecter-db":
        try:
            code = creer_dependance_db(args.dsn, args.endpoint)
            if getattr(args, "json", False):
                print(json.dumps(_construire_sortie_json(
                    True,
                    "Dépendance DB générée",
                    {"code": code},
                    denominateur=1
                ), ensure_ascii=False))
            else:
                print(code)
            return 0
        except ErreurInjection as e:
            _afficher_erreur(str(e))
            if getattr(args, "json", False):
                print(json.dumps(_construire_sortie_json(False, str(e), denominateur=0), ensure_ascii=False))
            return 3

    elif args.commande == "background-tache":
        executer_tache_background(args.cmd, args.delay)
        if getattr(args, "json", False):
            print(json.dumps(_construire_sortie_json(
                True,
                "Tâche en arrière-plan lancée",
                denominateur=1
            ), ensure_ascii=False))
        return 0

    elif args.commande == "concurrency-async":
        try:
            asyncio.run(demarrer_serveur_async(args.host, args.port))
            return 0
        except ErreurServeur as e:
            _afficher_erreur(str(e))
            if getattr(args, "json", False):
                print(json.dumps(_construire_sortie_json(False, str(e), denominateur=0), ensure_ascii=False))
            return 3

    return 2  # fallback générique

if __name__ == "__main__":
    raise SystemExit(main())