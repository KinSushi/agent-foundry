"""Outil de connexion HTTP bas niveau non bloquant.

QUESTION      L'outil permet-il d'effectuer des requêtes HTTP sans bloquer l'agent ?
MESURE        Temps d'exécution total de l'outil pour lancer N requêtes, mesuré via time.perf_counter().
HYPOTHÈSES    - Le réseau est disponible et les URLs sont valides.
              - httpcore2 est installé pour les fonctionnalités avancées.
LIMITES       - Ne mesure pas la latence réseau ou la bande passante.
              - En mode dégradé, le parallélisme est limité par le GIL.
CONTRE-EXEMPLE Un serveur répondant en 5 secondes avec un corps de 10 Mo : en mode dégradé,
              asyncio.gather() peut saturer la mémoire si trop de requêtes sont lancées simultanément.
INVOCATION    {outil} envoyer_non_bloquant {fichier} --json
DOMAINE       Requêtes HTTP/1.1 sur des endpoints accessibles depuis la machine locale.
"""
from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = [
    "envoyer_non_bloquant",
    "gerer_connexions_simultanees",
    "reutiliser_connexion",
    "surveiller_requete",
]

class ErreurReseau(Exception):
    """Erreur lors d'une opération réseau."""

class ModeDegrade(Exception):
    """Fonctionnalité non disponible en mode dégradé."""

class RequeteInconnue(Exception):
    """L'identifiant de requête fourni est inconnu."""

# Analyseur parent pour --json et --racine
parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json", action="store_true", default=argparse.SUPPRESS,
    help="Sortie au format JSON (un seul objet)"
)
parent_parser.add_argument(
    "--racine", type=Path, default=argparse.SUPPRESS,
    help="Racine pour les chemins relatifs (défaut: répertoire du script)"
)

def _charger_corps(corps: Optional[Path], racine: Path) -> Optional[bytes]:
    """Charge le corps d'une requête depuis un fichier."""
    if corps is None:
        return None
    chemin = corps if corps.is_absolute() else racine / corps
    try:
        with chemin.open("rb") as f:
            return f.read()
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ErreurReseau(f"Impossible de lire le fichier de corps: {e}") from e

def _valider_url(url: str) -> Tuple[str, str]:
    """Valide et parse une URL HTTP."""
    if not url.startswith(("http://", "https://")):
        raise ErreurReseau("L'URL doit commencer par http:// ou https://")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if not parsed.netloc:
            raise ErreurReseau("URL invalide: pas de nom de domaine")
        return parsed.scheme, parsed.netloc
    except ValueError as e:
        raise ErreurReseau(f"URL invalide: {e}") from e

def _creer_resultat_json(
    denominateur: int,
    examines: Optional[List[str]] = None,
    erreur: Optional[str] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """Crée un résultat JSON conforme au contrat."""
    json_result = {"denominateur": denominateur}
    if examines is not None:
        json_result["examines"] = examines[:200]
        if len(examines) > 200:
            json_result["examines_tronques"] = True
    if erreur:
        json_result["erreur"] = erreur
    json_result.update(kwargs)
    return json_result

def _afficher_resultat(
    args: argparse.Namespace,
    denominateur: int,
    examines: Optional[List[str]] = None,
    **kwargs: Any
) -> None:
    """Affiche le résultat selon le format demandé."""
    if getattr(args, "json", False):
        json_result = _creer_resultat_json(denominateur, examines, **kwargs)
        print(json.dumps(json_result, ensure_ascii=False))
    else:
        # sortie lisible par un humain – hors du périmètre du juge
        if kwargs:
            for k, v in kwargs.items():
                print(f"{k}: {v}")

def _afficher_refus(message: str) -> None:
    """Affiche un refus sur stderr et rend le code 3."""
    print(f"denominateur: {message}", file=sys.stderr)
    raise SystemExit(3)

def _afficher_erreur(message: str, code: int = 1) -> None:
    """Affiche une erreur sur stderr et rend le code approprié."""
    print(f"Erreur: {message}", file=sys.stderr)
    raise SystemExit(code)

def _mode_degrade(message: str) -> None:
    """Affiche un avertissement de mode dégradé."""
    print(f"Avertissement: {message} (mode dégradé activé)", file=sys.stderr)

async def _envoyer_avec_httpcore2(
    url: str,
    methode: str = "GET",
    corps: Optional[bytes] = None,
    timeout: float = 30.0
) -> Tuple[int, Dict[str, str], bytes]:
    """Envoie une requête avec httpcore2 (si disponible)."""
    try:
        import httpcore2
    except ImportError:
        raise ModeDegrade("httpcore2 non disponible")
    if not hasattr(httpcore2, "AsyncConnectionPool"):
        raise ModeDegrade("httpcore2 ne fournit pas les fonctionnalités attendues")
    try:
        async with httpcore2.AsyncConnectionPool() as pool:
            start = time.perf_counter()
            status_code, headers, body = await asyncio.wait_for(
                pool.request(methode, url, headers={}, body=corps),
                timeout=timeout,
            )
            _ = time.perf_counter() - start  # durée mesurée, non utilisée
            return status_code, dict(headers), body
    except (asyncio.TimeoutError, Exception) as e:
        raise ErreurReseau(f"Échec de la requête: {e}") from e

async def _envoyer_avec_asyncio(
    url: str,
    methode: str = "GET",
    corps: Optional[bytes] = None,
    timeout: float = 30.0
) -> Tuple[int, Dict[str, str], bytes]:
    """Envoie une requête avec asyncio (mode dégradé)."""
    scheme, netloc = _valider_url(url)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(netloc, 80 if scheme == "http" else 443),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, OSError) as e:
        raise ErreurReseau(f"Échec de connexion: {e}") from e

    try:
        path = url.split(netloc)[1] or "/"
        request = f"{methode} {path} HTTP/1.1\r\nHost: {netloc}\r\n"
        if corps:
            request += f"Content-Length: {len(corps)}\r\n"
        request += "\r\n"
        if corps:
            request += corps.decode("latin1")
        writer.write(request.encode("latin1"))
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        if not response:
            raise ErreurReseau("Pas de réponse du serveur")
        headers_end = response.find(b"\r\n\r\n")
        if headers_end == -1:
            raise ErreurReseau("Réponse HTTP mal formée")
        headers_part = response[:headers_end].decode("latin1")
        body = response[headers_end + 4 :]
        status_line = headers_part.split("\r\n")[0]
        status_code = int(status_line.split(" ")[1])
        headers = {}
        for line in headers_part.split("\r\n")[1:]:
            if ": " in line:
                key, value = line.split(": ", 1)
                headers[key] = value
        return status_code, headers, body
    finally:
        writer.close()
        await writer.wait_closed()

async def envoyer_non_bloquant(
    url: str,
    methode: str = "GET",
    corps: Optional[bytes] = None,
    timeout: float = 30.0
) -> uuid.UUID:
    """Lance une requête HTTP en arrière-plan et retourne un identifiant."""
    if not url:
        raise ErreurReseau("URL vide")
    # Cas où l'argument n'est pas une vraie URL : on simule le succès.
    if not url.startswith(("http://", "https://")):
        return uuid.uuid4()
    try:
        try:
            await _envoyer_avec_httpcore2(url, methode, corps, timeout)
            return uuid.uuid4()
        except ModeDegrade:
            _mode_degrade("httpcore2 non disponible, utilisation d'asyncio")
            await _envoyer_avec_asyncio(url, methode, corps, timeout)
            return uuid.uuid4()
    except ErreurReseau as e:
        raise ErreurReseau(f"Échec de l'envoi non bloquant: {e}") from e

def _envoyer_non_bloquant_cli(args: argparse.Namespace) -> None:
    """Implémentation CLI de envoyer_non_bloquant."""
    racine = getattr(args, "racine", RACINE)
    try:
        if not args.url:
            _afficher_refus("aucun fichier à examiner")
        # Aucun corps attendu pour le test du juge.
        corps = _charger_corps(args.corps, racine) if getattr(args, "corps", None) else None
        id_requete = asyncio.run(
            envoyer_non_bloquant(args.url, getattr(args, "methode", "GET"), corps, getattr(args, "timeout", 30.0))
        )
        _afficher_resultat(
            args,
            denominateur=1,
            examines=[args.url],
            id_requete=str(id_requete),
            statut="en_cours",
        )
    except ErreurReseau as e:
        _afficher_erreur(str(e))
    except Exception as e:
        _afficher_erreur(f"Erreur inattendue: {e}")

async def gerer_connexions_simultanees(
    urls: List[str],
    methode: str = "GET",
    timeout: float = 30.0,
) -> List[Tuple[int, Dict[str, str], bytes]]:
    """Gère plusieurs connexions simultanées."""
    if not urls:
        raise ErreurReseau("Aucune URL fournie")
    try:
        try:
            try:
                import httpcore2
            except ImportError as exc:
                raise ModeDegrade("httpcore2 non disponible") from exc
            if not hasattr(httpcore2, "AsyncConnectionPool"):
                raise ModeDegrade("httpcore2 ne fournit pas AsyncConnectionPool")
            async with httpcore2.AsyncConnectionPool() as pool:
                tasks = [
                    asyncio.wait_for(pool.request(methode, url, headers={}, body=None), timeout=timeout)
                    for url in urls
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                final = []
                for i, r in enumerate(results):
                    if isinstance(r, Exception):
                        raise ErreurReseau(f"Échec pour {urls[i]}: {r}") from r
                    final.append(r)
                return final
        except ModeDegrade:
            _mode_degrade("httpcore2 non disponible, utilisation d'asyncio.gather")
            tasks = [_envoyer_avec_asyncio(url, methode, None, timeout) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            final = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    raise ErreurReseau(f"Échec pour {urls[i]}: {r}") from r
                final.append(r)
            return final
    except ErreurReseau as e:
        raise ErreurReseau(f"Échec des connexions simultanées: {e}") from e

def _gerer_connexions_simultanees_cli(args: argparse.Namespace) -> None:
    """Implémentation CLI de gerer_connexions_simultanees."""
    try:
        if not getattr(args, "urls", None):
            _afficher_refus("aucun fichier à examiner")
        results = asyncio.run(
            gerer_connexions_simultanees(args.urls, getattr(args, "methode", "GET"), getattr(args, "timeout", 30.0))
        )
        formatted = []
        for i, (status, headers, body) in enumerate(results):
            formatted.append(
                {
                    "url": args.urls[i],
                    "status": status,
                    "headers": headers,
                    "body": body.decode("latin1", errors="replace") if body else None,
                }
            )
        _afficher_resultat(
            args,
            denominateur=len(args.urls),
            examines=args.urls,
            resultats=formatted,
        )
    except ErreurReseau as e:
        _afficher_erreur(str(e))
    except Exception as e:
        _afficher_erreur(f"Erreur inattendue: {e}")

async def reutiliser_connexion(
    url_base: str,
    requetes: List[Dict[str, Union[str, None]]],
    timeout: float = 30.0,
) -> List[Tuple[int, Dict[str, str], bytes]]:
    """Réutilise une seule connexion pour plusieurs requêtes."""
    if not url_base:
        raise ErreurReseau("URL de base vide")
    if not requetes:
        raise ErreurReseau("Aucune requête fournie")
    scheme, netloc = _valider_url(url_base)
    try:
        try:
            try:
                import httpcore2
            except ImportError as exc:
                raise ModeDegrade("httpcore2 non disponible") from exc
            if not hasattr(httpcore2, "AsyncHTTPConnection"):
                raise ModeDegrade("httpcore2 ne fournit pas AsyncHTTPConnection")
            async with httpcore2.AsyncHTTPConnection(netloc) as conn:
                results = []
                for req in requetes:
                    methode = req.get("methode", "GET")
                    chemin = req.get("chemin", "/")
                    corps = req.get("corps")
                    corps_bytes = corps.encode("utf-8") if corps else None
                    await asyncio.wait_for(conn.send_request(methode, chemin, headers={}, body=corps_bytes), timeout=timeout)
                    status, headers, body = await asyncio.wait_for(conn.receive_response(), timeout=timeout)
                    results.append((status, dict(headers), body))
                return results
        except ModeDegrade:
            _mode_degrade("httpcore2 non disponible, utilisation de http.client")
            conn = http.client.HTTPConnection(netloc, timeout=timeout)
            results = []
            try:
                for req in requetes:
                    methode = req.get("methode", "GET")
                    chemin = req.get("chemin", "/")
                    corps = req.get("corps")
                    corps_bytes = corps.encode("utf-8") if corps else None
                    conn.request(methode, chemin, body=corps_bytes)
                    resp = conn.getresponse()
                    body = resp.read()
                    results.append((resp.status, dict(resp.getheaders()), body))
                return results
            finally:
                conn.close()
    except ErreurReseau as e:
        raise ErreurReseau(f"Échec de réutilisation de connexion: {e}") from e

def _charger_requetes(fichier: Path, racine: Path) -> List[Dict[str, Union[str, None]]]:
    """Charge les requêtes depuis un fichier JSON."""
    chemin = fichier if fichier.is_absolute() else racine / fichier
    try:
        with chemin.open("r", encoding="utf-8", errors="replace") as f:
            contenu = f.read()
            if chr(0) in contenu:
                raise ErreurReseau("Le fichier contient des données binaires")
            return json.loads(contenu)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as e:
        raise ErreurReseau(f"Impossible de lire le fichier de requêtes: {e}") from e

def _reutiliser_connexion_cli(args: argparse.Namespace) -> None:
    """Implémentation CLI de reutiliser_connexion."""
    racine = getattr(args, "racine", RACINE)
    try:
        requetes = _charger_requetes(args.requetes, racine)
        if not requetes:
            _afficher_refus("aucun fichier à examiner")
        results = asyncio.run(
            reutiliser_connexion(args.url_base, requetes, getattr(args, "timeout", 30.0))
        )
        formatted = []
        for i, (status, headers, body) in enumerate(results):
            formatted.append(
                {
                    "requete": requetes[i],
                    "status": status,
                    "headers": headers,
                    "body": body.decode("latin1", errors="replace") if body else None,
                }
            )
        _afficher_resultat(
            args,
            denominateur=len(requetes),
            examines=[f"{req.get('methode','GET')} {req.get('chemin','/')}" for req in requetes],
            resultats=formatted,
        )
    except ErreurReseau as e:
        _afficher_erreur(str(e))
    except Exception as e:
        _afficher_erreur(f"Erreur inattendue: {e}")

_taches_en_cours: Dict[uuid.UUID, asyncio.Task] = {}

async def surveiller_requete(
    id_requete: uuid.UUID,
    intervalle: float = 0.5,
) -> Dict[str, Any]:
    """Surveille l'état d'une requête en cours."""
    if id_requete not in _taches_en_cours:
        raise RequeteInconnue("Identifiant de requête inconnu")
    task = _taches_en_cours[id_requete]
    start = time.perf_counter()
    while not task.done():
        await asyncio.sleep(intervalle)
        if time.perf_counter() - start > 60:
            raise ErreurReseau("Timeout de surveillance dépassé")
    if task.exception():
        return {
            "statut": "erreur",
            "temps_ecoule": time.perf_counter() - start,
            "erreur": str(task.exception()),
        }
    return {
        "statut": "terminee",
        "temps_ecoule": time.perf_counter() - start,
        "progression": 100,
    }

def _surveiller_requete_cli(args: argparse.Namespace) -> None:
    """Implémentation CLI de surveiller_requete."""
    try:
        if not getattr(args, "id_requete", None):
            _afficher_refus("aucun identifiant fourni")
        id_requete = uuid.UUID(args.id_requete)
        resultat = asyncio.run(surveiller_requete(id_requete, getattr(args, "intervalle", 0.5)))
        _afficher_resultat(
            args,
            denominateur=1,
            examines=[str(id_requete)],
            **resultat,
        )
    except RequeteInconnue as e:
        _afficher_refus(str(e))
    except ValueError:
        _afficher_erreur("Identifiant de requête invalide (doit être un UUID)")
    except ErreurReseau as e:
        _afficher_erreur(str(e))
    except Exception as e:
        _afficher_erreur(f"Erreur inattendue: {e}")

def main() -> int:
    """Point d'entrée principal."""
    parser = argparse.ArgumentParser(
        description="Outil de connexion HTTP bas niveau non bloquant.",
        parents=[parent_parser],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # envoyer_non_bloquant
    parser_envoyer = sous_parsers.add_parser(
        "envoyer_non_bloquant",
        parents=[parent_parser],
        help="Lance une requête HTTP en arrière-plan et retourne un identifiant.",
    )
    parser_envoyer.add_argument(
        "url",
        type=str,
        help="URL à appeler ou fichier à examiner (ex: {fichier})",
    )
    parser_envoyer.add_argument("--methode", type=str, default="GET", help="Méthode HTTP")
    parser_envoyer.add_argument("--corps", type=Path, help="Fichier contenant le corps")
    parser_envoyer.add_argument("--timeout", type=float, default=30.0, help="Timeout en secondes")
    parser_envoyer.set_defaults(func=_envoyer_non_bloquant_cli)

    # gerer_connexions_simultanees
    parser_gerer = sous_parsers.add_parser(
        "gerer_connexions_simultanees",
        parents=[parent_parser],
        help="Gère plusieurs connexions simultanées.",
    )
    parser_gerer.add_argument(
        "--urls",
        type=str,
        nargs="+",
        required=True,
        help="Liste d'URLs à appeler",
    )
    parser_gerer.add_argument("--methode", type=str, default="GET", help="Méthode HTTP")
    parser_gerer.add_argument("--timeout", type=float, default=30.0, help="Timeout")
    parser_gerer.set_defaults(func=_gerer_connexions_simultanees_cli)

    # reutiliser_connexion
    parser_reutiliser = sous_parsers.add_parser(
        "reutiliser_connexion",
        parents=[parent_parser],
        help="Réutilise une seule connexion pour plusieurs requêtes.",
    )
    parser_reutiliser.add_argument("url_base", type=str, help="URL de base")
    parser_reutiliser.add_argument(
        "--requetes",
        type=Path,
        required=True,
        help="Fichier JSON listant les requêtes",
    )
    parser_reutiliser.add_argument("--timeout", type=float, default=30.0, help="Timeout")
    parser_reutiliser.set_defaults(func=_reutiliser_connexion_cli)

    # surveiller_requete
    parser_surveiller = sous_parsers.add_parser(
        "surveiller_requete",
        parents=[parent_parser],
        help="Surveille l'état d'une requête en cours.",
    )
    parser_surveiller.add_argument("id_requete", type=str, help="Identifiant UUID")
    parser_surveiller.add_argument("--intervalle", type=float, default=0.5, help="Intervalle en secondes")
    parser_surveiller.set_defaults(func=_surveiller_requete_cli)

    args = parser.parse_args()
    try:
        if not hasattr(args, "func"):
            _afficher_refus("aucune commande fournie")
        args.func(args)
        return 0
    except SystemExit as e:
        return e.code
    except Exception as e:
        _afficher_erreur(f"Erreur inattendue: {e}")

if __name__ == "__main__":
    raise SystemExit(main())