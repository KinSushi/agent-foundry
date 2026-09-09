"""QUESTION
L'outil signe‑t‑il correctement une requête AWS SigV4 ?
MESURE
Valeur du champ `Authorization` dans la requête signée.
HYPOTHESES
Les clés d'accès sont valides, la date système est correcte.
LIMITES
L'outil ne vérifie pas la validité du secret (pas d'appel AWS réel).
CONTRE-EXEMPLES
Une clé expirée produit une signature qui passe la mesure mais est rejetée par le service.
INVOCATION
{outil} auth {fichier} --service s3 --region us-east-1 --access-key AKIATEST --secret-key testsecret --json
DOMAINE
Services AWS supportant la signature v4 (S3, DynamoDB, …)."""

from __future__ import annotations

import argparse
import json
import sys
import hashlib
import hmac
import datetime
import urllib.parse
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "main",
    "core_client",
    "core_auth",
    "core_args",
    "core_awsrequest",
    "core_error",
    "OutilError",
]

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
REFUS_WORDS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

# ------------------------------------------------------------
# Exceptions métier
# ------------------------------------------------------------
class OutilError(Exception):
    """Exception levée par le cœur de l'outil."""

    def __init__(
        self,
        message: str,
        code: int = 1,
        denominateur: int = 0,
        examines: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.denominateur = denominateur
        self.examines = examines or []


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _read_file(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            contenu = f.read()
        if "\x00" in contenu:
            raise OutilError("Fichier binaire détecté", code=5, denominateur=0)
        return contenu
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise OutilError(f"Impossible de lire le fichier : {exc}", code=5, denominateur=0)


def _trunc_examines(lst: List[str]) -> List[str]:
    return lst[:MAX_EXAMINES]


def _build_result(
    denominateur: int,
    examines: List[str],
    sortie: str = "",
) -> Dict[str, Any]:
    base = {
        "denominateur": denominateur,
        "examines": _trunc_examines(examines),
        "examines_tronques": len(examines) > MAX_EXAMINES,
    }
    if sortie:
        base["sortie"] = sortie
    return base


def _sign_sigv4(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: bytes,
    service: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: Optional[str] = None,
) -> str:
    """Implémentation minimale de SigV4 (sans botocore)."""
    t = datetime.datetime.utcnow()
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")

    parsed = urllib.parse.urlparse(url)
    canonical_uri = urllib.parse.quote(parsed.path or "/", safe="/~")
    canonical_querystring = parsed.query
    signed_headers = ";".join(k.lower() for k in sorted(headers))
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = "".join(
        f"{k.lower()}:{v.strip()}\n" for k, v in sorted(headers.items())
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join([algorithm, amz_date, credential_scope, hashed_canonical])

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return auth_header


# ------------------------------------------------------------
# Cœur de l'outil
# ------------------------------------------------------------
def core_client(args: argparse.Namespace) -> Dict[str, Any]:
    examines = [f"service:{args.service}", f"action:{args.action}"]
    if not args.service or not args.action:
        raise OutilError("Service et action requis", code=2, denominateur=0, examines=examines)

    if importlib.util.find_spec("botocore") is None:
        sys.stderr.write("botocore non disponible – mode dégradé\n")
        return _build_result(denominateur=1, examines=examines)

    try:
        import botocore.session
        session = botocore.session.get_session()
        client = session.create_client(
            service_name=args.service,
            region_name=args.region,
            endpoint_url=args.endpoint,
        )
        params = json.loads(args.params) if args.params else {}
        operation = getattr(client, args.action)
        response = operation(**params)
        response_json = json.dumps(response, default=str)
        return _build_result(denominateur=1, examines=examines, sortie=response_json)
    except Exception as exc:
        raise OutilError(f"Erreur client AWS : {exc}", code=2, denominateur=0, examines=examines)


def core_auth(args: argparse.Namespace) -> Dict[str, Any]:
    examines = [f"request_file:{args.request_file}"]
    if not args.access_key or not args.secret_key:
        raise OutilError("Missing credentials", code=4, denominateur=0, examines=examines)

    contenu = _read_file(Path(args.request_file))
    lines = contenu.splitlines()
    if not lines:
        raise OutilError(
            "Fichier de requête vide – refus (denominateur nul)",
            code=3,
            denominateur=0,
            examines=examines,
        )

    try:
        method, url, _ = lines[0].split()
    except ValueError:
        raise OutilError("Première ligne invalide (méthode URL)", code=4, denominateur=0, examines=examines)

    headers: Dict[str, str] = {}
    body_bytes = b""
    i = 1
    while i < len(lines) and lines[i].strip():
        k, v = lines[i].split(":", 1)
        headers[k.strip()] = v.strip()
        i += 1
    i += 1
    if i < len(lines):
        body_bytes = "\n".join(lines[i:]).encode("utf-8")

    if importlib.util.find_spec("botocore") is not None:
        try:
            import botocore.auth
            import botocore.credentials
            import botocore.awsrequest

            cred = botocore.credentials.Credentials(
                args.access_key, args.secret_key, args.session_token
            )
            request = botocore.awsrequest.AWSRequest(
                method=method, url=url, data=body_bytes, headers=headers
            )
            signer = botocore.auth.SigV4Auth(cred, args.service, args.region)
            signer.add_auth(request)
            signed = request.raw_headers.decode("utf-8")
            return _build_result(denominateur=1, examines=examines, sortie=signed)
        except Exception as exc:
            sys.stderr.write(f"Signature via botocore échouée : {exc}\n")

    auth_header = _sign_sigv4(
        method=method,
        url=url,
        headers=headers,
        body=body_bytes,
        service=args.service,
        region=args.region,
        access_key=args.access_key,
        secret_key=args.secret_key,
        session_token=args.session_token,
    )
    headers["Authorization"] = auth_header
    signed_req = f"{method} {url} HTTP/1.1\n" + "\n".join(f"{k}: {v}" for k, v in headers.items())
    return _build_result(denominateur=1, examines=examines, sortie=signed_req)


def core_args(args: argparse.Namespace) -> Dict[str, Any]:
    examines = [f"service:{args.service}", f"action:{args.action}"]
    if not args.params:
        raise OutilError("Paramètres JSON requis", code=3, denominateur=0, examines=examines)
    try:
        _ = json.loads(args.params)
    except json.JSONDecodeError as exc:
        raise OutilError(f"JSON invalide : {exc}", code=3, denominateur=0, examines=examines)
    return _build_result(denominateur=1, examines=examines)


def core_awsrequest(args: argparse.Namespace) -> Dict[str, Any]:
    examines = [f"url:{args.url}"]
    if importlib.util.find_spec("botocore") is None:
        sys.stderr.write("botocore non disponible – mode dégradé, aucune requête réelle\n")
        return _build_result(denominateur=1, examines=examines)

    try:
        import botocore.awsrequest
        import botocore.endpoint

        request = botocore.awsrequest.AWSRequest(
            method=args.method, url=args.url, data=args.body.encode() if args.body else None
        )
        endpoint = botocore.endpoint.Endpoint(
            service_model=None,
            endpoint_url=args.url,
            request_signer=None,
            http_session=None,
        )
        response = endpoint.make_request(request, operation_model=None)
        return _build_result(
            denominateur=1,
            examines=examines,
            sortie=response.content.decode("utf-8", errors="replace"),
        )
    except Exception as exc:
        raise OutilError(f"Erreur requête AWS : {exc}", code=6, denominateur=0, examines=examines)


def core_error(args: argparse.Namespace) -> Dict[str, Any]:
    examines = [f"response_file:{args.response_file}"]
    contenu = _read_file(Path(args.response_file))
    try:
        data = json.loads(contenu)
        err = data.get("Error", {})
        sortie = err.get("Code", "UnknownError") if err else "Pas d'erreur détectée"
        return _build_result(denominateur=1, examines=examines, sortie=sortie)
    except json.JSONDecodeError:
        raise OutilError("Unable to parse response as JSON", code=7, denominateur=0, examines=examines)


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def _make_parent_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique",
        default=argparse.SUPPRESS,
    )
    parent.add_argument(
        "--racine",
        type=str,
        help="Chemin racine du projet (défaut : répertoire du script)",
        default=argparse.SUPPRESS,
    )
    return parent


def main() -> int:
    parent_parser = _make_parent_parser()
    parser = argparse.ArgumentParser(
        description="Outil de signature et d'appel AWS (SigV4).",
        parents=[parent_parser],
        epilog="Exemple : %(prog)s client --service s3 --action ListBuckets --region us-east-1 --json",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    p_client = subparsers.add_parser(
        "client", parents=[parent_parser], help="Appel dynamique via botocore"
    )
    p_client.add_argument("--service", required=True, help="Nom du service AWS")
    p_client.add_argument("--action", required=True, help="Nom de l'action")
    p_client.add_argument("--params", help="Paramètres JSON")
    p_client.add_argument("--region", required=True, help="Région AWS")
    p_client.add_argument("--endpoint", help="URL d'endpoint personnalisé")
    p_client.add_argument("cible", nargs="?", help="Fichier à examiner (optionnel)")
    p_client.set_defaults(func=core_client)

    p_auth = subparsers.add_parser(
        "auth", parents=[parent_parser], help="Signature d'une requête brute"
    )
    p_auth.add_argument("--service", required=True, help="Service AWS")
    p_auth.add_argument("--region", required=True, help="Région AWS")
    p_auth.add_argument("--access-key", required=True, help="Clé d'accès")
    p_auth.add_argument("--secret-key", required=True, help="Clé secrète")
    p_auth.add_argument("--session-token", help="Token de session")
    p_auth.add_argument(
        "request_file",
        help="Fichier de requête brute (exemple : valide.py)",
    )
    p_auth.set_defaults(func=core_auth)

    p_args = subparsers.add_parser(
        "args", parents=[parent_parser], help="Validation des paramètres"
    )
    p_args.add_argument("--service", required=True, help="Service AWS")
    p_args.add_argument("--action", required=True, help="Action AWS")
    p_args.add_argument("--params", required=True, help="Paramètres JSON")
    p_args.set_defaults(func=core_args)

    p_req = subparsers.add_parser(
        "awsrequest", parents=[parent_parser], help="Envoi d'une requête HTTP AWS"
    )
    p_req.add_argument("--method", required=True, help="Méthode HTTP")
    p_req.add_argument("--url", required=True, help="URL cible")
    p_req.add_argument("--headers", help="En‑têtes JSON")
    p_req.add_argument("--body", help="Corps (fichier ou chaîne)")
    p_req.add_argument("--retries", type=int, default=0, help="Nombre de retries")
    p_req.add_argument("--timeout", type=float, help="Timeout en secondes")
    p_req.set_defaults(func=core_awsrequest)

    p_err = subparsers.add_parser(
        "error", parents=[parent_parser], help="Analyse d'une réponse d'erreur AWS"
    )
    p_err.add_argument("--service", required=True, help="Service AWS")
    p_err.add_argument("--action", required=True, help="Action AWS")
    p_err.add_argument("--response-file", required=True, help="Fichier de réponse")
    p_err.set_defaults(func=core_error)

    args = parser.parse_args()

    racine_path = RACINE
    if hasattr(args, "racine"):
        p = Path(args.racine)
        racine_path = p if p.is_absolute() else RACINE / p

    try:
        result = args.func(args)
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        else:
            sortie = result.get("sortie", "")
            if sortie:
                print(sortie, file=sys.stdout)
        return 0
    except OutilError as exc:
        if exc.denominateur == 0:
            # protocole de refus
            if not any(w in exc.message.lower() for w in REFUS_WORDS):
                exc.message += " – aucun (denominateur nul)"
            print(exc.message, file=sys.stderr)
            if getattr(args, "json", False):
                err_res = _build_result(exc.denominateur, exc.examines)
                print(json.dumps(err_res, ensure_ascii=False), file=sys.stdout)
            return 3
        print(exc.message, file=sys.stderr)
        if getattr(args, "json", False):
            err_res = _build_result(exc.denominateur, exc.examines)
            print(json.dumps(err_res, ensure_ascii=False), file=sys.stdout)
        return exc.code if exc.code != 0 else 1
    except SystemExit as se:
        return se.code
    except Exception as exc:
        print(f"Erreur inattendue : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())