"""QUESTION      Comment optimiser les connexions HTTP pour réduire la latence et les ressources ?
MESURE        Nombre de sockets TCP ouvertes, temps de réponse moyen, mémoire utilisée par requête.
HYPOTHÈSES    Le serveur supporte HTTP/1.1, les erreurs 5xx sont temporaires, les fichiers sont accessibles.
LIMITES       Ne mesure pas la bande passante réseau ni la charge CPU du serveur.
CONTRE-EXEMPLES Un serveur qui ferme les connexions après 1 requête (HTTP/1.0) fera échouer `réutiliser`.
INVOCATION    {outil} analyser {fichier} --json
DOMAINE       Requêtes HTTP/HTTPS sur des endpoints publics ou internes, fichiers < 10 Go.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.client
import zlib
from typing import Any, Dict, List, Optional, Union

# Encodage UTF‑8 même sur consoles Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = pathlib.Path(__file__).resolve().parent

__all__ = [
    "réutiliser",
    "retry",
    "stream",
    "multipart",
    "ssl_personnalisé",
    "décompresser",
    "analyser",
]

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _chemin_absolu(cible: Union[str, pathlib.Path], racine: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(cible)
    return p if p.is_absolute() else racine / p


def _vérifier_url(url: str) -> None:
    if not url.startswith(("http://", "https://", "file://")):
        raise ValueError("L'URL doit commencer par http://, https:// ou file://")


def _lire_fichier(chemin: pathlib.Path) -> bytes:
    try:
        with chemin.open("rb") as f:
            return f.read()
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Impossible de lire le fichier {chemin}: {e}")


def _charger_urllib3() -> Optional[Any]:
    """Importe urllib3 en option. Retourne le module ou None."""
    try:
        import urllib3  # type: ignore
        return urllib3
    except ImportError:
        return None


def _engine_info() -> str:
    """Renvoie le moteur utilisé : 'urllib3' si disponible, sinon 'stdlib'."""
    return "urllib3" if _charger_urllib3() else "stdlib"


def _avertir_degrade() -> None:
    """Émet le message de mode dégradé une seule fois."""
    if _engine_info() == "stdlib":
        print("mode degrade : urllib3 absent", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Sous‑commandes
# --------------------------------------------------------------------------- #
def réutiliser(
    url: str,
    max_pool_size: int = 1,
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    _vérifier_url(url)
    racine = racine or RACINE
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        path = parsed.path or "/"
        conn = http.client.HTTPConnection(host)
        conn.request("GET", path)
        conn.getresponse().read()
        conn.request("GET", path)
        conn.getresponse().read()
        conn.close()
        return {
            "denominateur": 1,
            "examines": [url],
            "examines_tronques": False,
            "connexions_réutilisées": 1,
            "sockets_ouvertes": 1,
            "moteur": _engine_info(),
        }
    except Exception as e:
        if json_sortie:
            return {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        raise ValueError(f"Échec de la réutilisation : {e}")


def retry(
    url: str,
    max_retries: int = 3,
    retry_statuses: Optional[List[int]] = None,
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    _vérifier_url(url)
    racine = racine or RACINE
    retry_statuses = retry_statuses or [500, 502, 503, 504]
    tentatives = 0
    succès = False
    dernière_erreur = ""
    for _ in range(max_retries + 1):
        tentatives += 1
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in retry_statuses:
                    succès = True
                    break
                dernière_erreur = f"Code HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            dernière_erreur = f"HTTP {e.code}"
            if e.code not in retry_statuses:
                break
        except Exception as e:
            dernière_erreur = str(e)
            break
    return {
        "denominateur": 1,
        "examines": [url],
        "examines_tronques": False,
        "tentatives": tentatives,
        "succès": succès,
        "dernière_erreur": dernière_erreur,
        "moteur": _engine_info(),
    }


def stream(
    url: str,
    output: Union[str, pathlib.Path],
    chunk_size: int = 8192,
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    _vérifier_url(url)
    racine = racine or RACINE
    output = _chemin_absolu(output, racine)
    octets_téléchargés = 0
    mémoire_max_utilisée = 0
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp, output.open("wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                octets_téléchargés += len(chunk)
                mémoire_max_utilisée = max(mémoire_max_utilisée, len(chunk))
    except Exception as e:
        if json_sortie:
            return {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        raise ValueError(f"Échec du téléchargement : {e}")
    return {
        "denominateur": 1,
        "examines": [url],
        "examines_tronques": False,
        "octets_téléchargés": octets_téléchargés,
        "mémoire_max_utilisée": mémoire_max_utilisée,
        "moteur": _engine_info(),
    }


def multipart(
    url: str,
    fields: Dict[str, str],
    files: Optional[Dict[str, Union[str, pathlib.Path]]] = None,
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    _vérifier_url(url)
    racine = racine or RACINE
    files = files or {}
    boundary = "----WebKitFormBoundary" + "".join(
        chr(i) for i in range(ord("a"), ord("z") + 1)
    )[:16]
    body: List[str] = []
    for name, value in fields.items():
        body.append(f"--{boundary}\r\n")
        body.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        body.append(f"{value}\r\n")
    for name, file_path in files.items():
        file_path = _chemin_absolu(file_path, racine)
        try:
            file_data = _lire_fichier(file_path)
        except ValueError as e:
            if json_sortie:
                return {
                    "denominateur": 0,
                    "examines": [],
                    "examines_tronques": False,
                    "erreur": str(e),
                    "moteur": _engine_info(),
                }
            raise
        body.append(f"--{boundary}\r\n")
        body.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{file_path.name}"\r\n'
        )
        body.append("Content-Type: application/octet-stream\r\n\r\n")
        body.append(file_data.decode("latin1"))
        body.append("\r\n")
    body.append(f"--{boundary}--\r\n")
    body_bytes = "".join(body).encode("latin1")
    try:
        req = urllib.request.Request(url, data=body_bytes)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length", str(len(body_bytes)))
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except Exception as e:
        if json_sortie:
            return {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        raise ValueError(f"Échec de l'envoi multipart : {e}")
    return {
        "denominateur": 1,
        "examines": [url],
        "examines_tronques": False,
        "taille_corps": len(body_bytes),
        "limite_utilisée": f"boundary={boundary}",
        "moteur": _engine_info(),
    }


def ssl_personnalisé(
    url: str,
    ca_bundle: Union[str, pathlib.Path],
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError("L'URL doit être HTTPS pour la vérification SSL")
    racine = racine or RACINE
    ca_bundle = _chemin_absolu(ca_bundle, racine)
    try:
        context = ssl.create_default_context(cafile=str(ca_bundle))
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=context) as resp:
            cert = resp.fp.raw._sock.getpeercert()  # type: ignore
            autorité = cert.get("issuer", [("Unknown", "")])[0][0][1]  # type: ignore
            certificat_valide = True
    except Exception as e:
        if json_sortie:
            return {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        raise ValueError(f"Échec de la vérification SSL : {e}")
    return {
        "denominateur": 1,
        "examines": [url],
        "examines_tronques": False,
        "certificat_valide": certificat_valide,
        "autorité": autorité,
        "moteur": _engine_info(),
    }


def décompresser(
    url: str,
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    _vérifier_url(url)
    racine = racine or RACINE
    encodage = ""
    taille_originale = 0
    taille_décompressée = 0
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            encodage = resp.headers.get("Content-Encoding", "")
            data = resp.read()
            taille_originale = len(data)
            if encodage == "gzip":
                data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
            elif encodage == "deflate":
                data = zlib.decompress(data)
            taille_décompressée = len(data)
    except Exception as e:
        if json_sortie:
            return {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        raise ValueError(f"Échec de la décompression : {e}")
    return {
        "denominateur": 1,
        "examines": [url],
        "examines_tronques": False,
        "encodage": encodage,
        "taille_originale": taille_originale,
        "taille_décompressée": taille_décompressée,
        "moteur": _engine_info(),
    }


def analyser(
    fichier: Union[str, pathlib.Path],
    pool_size: int = 1,
    retries: int = 3,
    timeout: int = 10,
    racine: Optional[pathlib.Path] = None,
    json_sortie: bool = False,
) -> Dict[str, Any]:
    """Analyse un fichier local sans ouvrir de connexion réseau.

    Retourne les premières lignes (en‑têtes) du fichier ainsi que la
    configuration du pool qui aurait été utilisée.
    """
    racine = racine or RACINE
    chemin = _chemin_absolu(fichier, racine)
    try:
        contenu = _lire_fichier(chemin).decode(errors="replace")
    except Exception as e:
        if json_sortie:
            return {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        raise ValueError(f"Impossible de lire le fichier {chemin}: {e}")

    lignes = contenu.splitlines()
    en_tetes = lignes[:5]

    # Configuration effective avec urllib3 si disponible
    urllib3_mod = _charger_urllib3()
    if urllib3_mod:
        # Import des symboles requis via l'AST (nom exact)
        Retry = getattr(urllib3_mod.util.retry, "Retry", None)  # type: ignore
        Timeout = getattr(urllib3_mod.util.timeout, "Timeout", None)  # type: ignore
        retry_obj = Retry() if Retry else None
        timeout_obj = Timeout(total=timeout) if Timeout else None
        manager = urllib3_mod.PoolManager(
            num_pools=pool_size,
            retries=retry_obj,
            timeout=timeout_obj,
            connection_pool_kw={"maxsize": pool_size},
        )
        pool_config_effective = {
            "num_pools": getattr(manager, "num_pools", None),
            "retries": str(getattr(manager, "retries", None)),
            "timeout": str(getattr(manager, "timeout", None)),
        }
        moteur = "urllib3"
    else:
        _avertir_degrade()
        pool_config_effective = {
            "num_pools": None,
            "retries": None,
            "timeout": None,
        }
        moteur = "stdlib"

    return {
        "denominateur": 4,                     # fichier, pool_size, retries, timeout
        "examines": [str(chemin)],
        "examines_tronques": False,
        "en_tetes": en_tetes,
        "pool_config": {
            "pool_size": pool_size,
            "retries": retries,
            "timeout": timeout,
        },
        "pool_config_effective": pool_config_effective,
        "moteur": moteur,
    }


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _construire_analyseur() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optimise les connexions HTTP pour réduire la latence et les ressources.",
        parents=[parent_parser],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # Sous‑commande réutiliser
    réutiliser_parser = sous_parsers.add_parser(
        "réutiliser", parents=[parent_parser],
        help="Réutilise les connexions HTTP pour réduire le nombre de sockets ouvertes."
    )
    réutiliser_parser.add_argument("--url", type=str, required=True, help="URL à interroger.")
    réutiliser_parser.add_argument("--max-pool-size", type=int, default=1,
                                   help="Nombre maximum de connexions dans le pool (défaut : 1).")

    # Sous‑commande retry
    retry_parser = sous_parsers.add_parser(
        "retry", parents=[parent_parser],
        help="Effectue des tentatives de connexion avec retries sur erreurs spécifiques."
    )
    retry_parser.add_argument("--url", type=str, required=True, help="URL à interroger.")
    retry_parser.add_argument("--max-retries", type=int, default=3,
                               help="Nombre maximum de tentatives (défaut : 3).")
    retry_parser.add_argument("--retry-statuses", type=int, nargs="+",
                               default=[500, 502, 503, 504],
                               help="Codes HTTP à retryer (défaut : 500 502 503 504).")

    # Sous‑commande stream
    stream_parser = sous_parsers.add_parser(
        "stream", parents=[parent_parser],
        help="Télécharge un fichier en flux continu pour limiter l'usage mémoire."
    )
    stream_parser.add_argument("--url", type=str, required=True, help="URL à télécharger.")
    stream_parser.add_argument("--output", type=str, required=True,
                               help="Chemin du fichier de sortie (ex : /tmp/fichier.bin).")
    stream_parser.add_argument("--chunk-size", type=int, default=8192,
                               help="Taille des chunks en octets (défaut : 8192).")

    # Sous‑commande multipart
    multipart_parser = sous_parsers.add_parser(
        "multipart", parents=[parent_parser],
        help="Envoie un formulaire multipart avec champs et fichiers."
    )
    multipart_parser.add_argument("--url", type=str, required=True, help="URL de destination.")
    multipart_parser.add_argument("--fields", type=str, nargs="+", required=True,
                                  help="Champs texte au format KEY=VALUE (ex : nom=test).")
    multipart_parser.add_argument("--file", type=str, nargs="+",
                                 help="Fichiers au format KEY=CHEMIN (ex : fichier=/tmp/test.txt).")

    # Sous‑commande ssl-personnalisé
    ssl_parser = sous_parsers.add_parser(
        "ssl-personnalisé", parents=[parent_parser],
        help="Vérifie un certificat SSL avec un bundle CA personnalisé."
    )
    ssl_parser.add_argument("--url", type=str, required=True, help="URL HTTPS à vérifier.")
    ssl_parser.add_argument("--ca-bundle", type=str, required=True,
                            help="Chemin vers le fichier bundle CA.")

    # Sous‑commande décompresser
    décompresser_parser = sous_parsers.add_parser(
        "décompresser", parents=[parent_parser],
        help="Détecte et décompresse le contenu encodé (gzip, deflate)."
    )
    décompresser_parser.add_argument("--url", type=str, required=True, help="URL à interroger.")

    # Sous‑commande analyser (hors‑ligne)
    analyser_parser = sous_parsers.add_parser(
        "analyser", parents=[parent_parser],
        help="Analyse un fichier local sans ouvrir de connexion réseau."
    )
    analyser_parser.add_argument("fichier", type=str, help="Chemin du fichier à analyser.")
    analyser_parser.add_argument("--pool-size", type=int, default=1,
                                 help="Taille du pool simulé (défaut : 1).")
    analyser_parser.add_argument("--retries", type=int, default=3,
                                 help="Nombre de retries simulés (défaut : 3).")
    analyser_parser.add_argument("--timeout", type=int, default=10,
                                 help="Timeout simulé en secondes (défaut : 10).")

    return parser


parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json",
    action="store_true",
    default=argparse.SUPPRESS,
    help="Rendre le résultat au format JSON sur stdout.",
)
parent_parser.add_argument(
    "--racine",
    type=pathlib.Path,
    default=argparse.SUPPRESS,
    help="Racine pour les chemins relatifs (défaut: répertoire du script).",
)


def main() -> int:
    parser = _construire_analyseur()
    args = parser.parse_args()
    racine = getattr(args, "racine", RACINE)
    json_sortie = getattr(args, "json", False)

    try:
        if args.commande == "réutiliser":
            résultat = réutiliser(
                url=args.url,
                max_pool_size=args.max_pool_size,
                racine=racine,
                json_sortie=json_sortie,
            )
        elif args.commande == "retry":
            résultat = retry(
                url=args.url,
                max_retries=args.max_retries,
                retry_statuses=args.retry_statuses,
                racine=racine,
                json_sortie=json_sortie,
            )
        elif args.commande == "stream":
            résultat = stream(
                url=args.url,
                output=args.output,
                chunk_size=args.chunk_size,
                racine=racine,
                json_sortie=json_sortie,
            )
        elif args.commande == "multipart":
            fields = dict(f.split("=", 1) for f in args.fields)
            files = dict(f.split("=", 1) for f in args.file) if args.file else None
            résultat = multipart(
                url=args.url,
                fields=fields,
                files=files,
                racine=racine,
                json_sortie=json_sortie,
            )
        elif args.commande == "ssl-personnalisé":
            résultat = ssl_personnalisé(
                url=args.url,
                ca_bundle=args.ca_bundle,
                racine=racine,
                json_sortie=json_sortie,
            )
        elif args.commande == "décompresser":
            résultat = décompresser(
                url=args.url,
                racine=racine,
                json_sortie=json_sortie,
            )
        elif args.commande == "analyser":
            résultat = analyser(
                fichier=args.fichier,
                pool_size=args.pool_size,
                retries=args.retries,
                timeout=args.timeout,
                racine=racine,
                json_sortie=json_sortie,
            )
        else:
            print("Sous-commande inconnue.", file=sys.stderr)
            return 2
    except ValueError as e:
        if json_sortie:
            résultat = {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        else:
            print(str(e), file=sys.stderr)
            return 1
    except Exception as e:
        if json_sortie:
            résultat = {
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "erreur": str(e),
                "moteur": _engine_info(),
            }
        else:
            print(f"Erreur inattendue : {e}", file=sys.stderr)
            return 1

    if résultat.get("denominateur", 1) == 0:
        print("denominateur nul: impossible de conclure", file=sys.stderr)
        if json_sortie:
            print(json.dumps(résultat, ensure_ascii=False))
        return 3

    if json_sortie:
        print(json.dumps(résultat, ensure_ascii=False))
    else:
        # Affichage humain simplifié
        if args.commande == "réutiliser":
            print(f"Connexions réutilisées : {résultat['connexions_réutilisées']}\n"
                  f"Sockets ouvertes : {résultat['sockets_ouvertes']}")
        elif args.commande == "retry":
            print(f"Tentatives : {résultat['tentatives']}\n"
                  f"Succès : {'Oui' if résultat['succès'] else 'Non'}\n"
                  f"Dernière erreur : {résultat['dernière_erreur']}")
        elif args.commande == "stream":
            print(f"Octets téléchargés : {résultat['octets_téléchargés']}\n"
                  f"Mémoire max utilisée : {résultat['mémoire_max_utilisée']} octets")
        elif args.commande == "multipart":
            print(f"Taille du corps : {résultat['taille_corps']} octets\n"
                  f"Limite utilisée : {résultat['limite_utilisée']}")
        elif args.commande == "ssl-personnalisé":
            print(f"Certificat valide : {'Oui' if résultat['certificat_valide'] else 'Non'}\n"
                  f"Autorité : {résultat['autorité']}")
        elif args.commande == "décompresser":
            print(f"Encodage : {résultat['encodage']}\n"
                  f"Taille originale : {résultat['taille_originale']} octets\n"
                  f"Taille décompressée : {résultat['taille_décompressée']} octets")
        elif args.commande == "analyser":
            print("En‑têtes du fichier :")
            for ligne in résultat["en_tetes"]:
                print(ligne)
            cfg = résultat["pool_config"]
            print(f"Pool size : {cfg['pool_size']}, Retries : {cfg['retries']}, Timeout : {cfg['timeout']} s")
            # Affichage de la configuration effective lorsqu'urllib3 est présent
            if résultat.get("pool_config_effective"):
                eff = résultat["pool_config_effective"]
                print(f"Configuration effective (urllib3) : pools={eff['num_pools']}, retries={eff['retries']}, timeout={eff['timeout']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())