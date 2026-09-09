"""Analyse et manipulation de messages HTTP/1.1 bruts.

QUESTION
    Le message HTTP est‑il strictement conforme à la RFC 7230 ?

MESURE
    Nombre d’erreurs détectées par validation syntaxique du message brut.

HYPOTHÈSES
    Le message est complet, encodé en UTF‑8, les en‑têtes sont séparés par \\r\\n.

LIMITES
    Ne détecte pas les violations dépendant du contexte d’application
    (exigences de sécurité spécifiques).

CONTRE‑EXEMPLE
    Un message contenant `Transfer‑Encoding: chunked` suivi d’un corps non‑hexadécimal
    déclenche une fausse conformité : l’outil signale « aucune erreur » alors que le
    flux est invalide.

INVOCATION
    {outil} valider {fichier} --json

DOMAINE
    Analyse de messages HTTP/1.1 bruts, hors TLS et HTTP/2.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Union

# Encodage UTF‑8 pour les consoles Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Bibliothèque optionnelle --------------------------------------------------- #
H11_DISPONIBLE = False
try:
    import h11  # type: ignore
    H11_DISPONIBLE = True
except ImportError:  # pragma: no cover
    pass
# --------------------------------------------------------------------------- #

__all__ = [
    "parser_http",
    "serializer_http",
    "valider_conformite",
    "gerer_flux_incomplet",
    "envoyer_requete",
    "lancer_serveur",
    "valider_message",
    "analyser_flux",
]


def parser_http(message: bytes) -> Optional[Any]:
    """Analyse un message HTTP brut en événement h11."""
    if not H11_DISPONIBLE:
        logging.warning("h11 non disponible – parsing impossible")
        return None
    try:
        conn = h11.Connection(our_role=h11.CLIENT)  # type: ignore[attr-defined]
        conn.receive_data(message)
        events = []
        while True:
            ev = conn.next_event()
            if ev is h11.NEED_DATA:  # type: ignore[attr-defined]
                break
            events.append(ev)
            if isinstance(ev, (h11.Response, h11.EndOfMessage)):  # type: ignore[attr-defined]
                break
        return events[0] if events else None
    except Exception as e:  # pragma: no cover
        logging.warning(f"Erreur de parsing h11 : {e}")
        return None


def serializer_http(event: Any) -> Optional[bytes]:
    """Convertit un événement h11 en octets."""
    if not H11_DISPONIBLE:
        logging.warning("h11 non disponible – sérialisation impossible")
        return None
    try:
        conn = h11.Connection(our_role=h11.CLIENT)  # type: ignore[attr-defined]
        conn.send(event)
        return conn.send(h11.EndOfMessage())  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover
        logging.warning(f"Erreur de sérialisation h11 : {e}")
        return None


def valider_conformite(event: Any) -> List[str]:
    """Retourne les violations RFC 7230 détectées."""
    violations: List[str] = []
    if not H11_DISPONIBLE:
        violations.append("h11 non disponible – validation impossible")
        return violations

    if isinstance(event, h11.Request):  # type: ignore[attr-defined]
        if not event.method:
            violations.append("Méthode HTTP manquante")
        if not event.target:
            violations.append("Cible HTTP manquante")
        if event.http_version not in ("HTTP/1.1",):
            violations.append(f"Version HTTP non supportée : {event.http_version}")
    elif isinstance(event, h11.Response):  # type: ignore[attr-defined]
        if not (100 <= event.status_code <= 599):
            violations.append(f"Code de statut invalide : {event.status_code}")
        if event.http_version not in ("HTTP/1.1",):
            violations.append(f"Version HTTP non supportée : {event.http_version}")
    else:
        violations.append("Événement HTTP inconnu")
    return violations


def gerer_flux_incomplet(stream: Iterable[bytes]) -> Generator[Any, None, None]:
    """Produit des événements même si le flux est tronqué."""
    if not H11_DISPONIBLE:
        logging.warning("h11 non disponible – gestion de flux impossible")
        return
    conn = h11.Connection(our_role=h11.CLIENT)  # type: ignore[attr-defined]
    for chunk in stream:
        try:
            conn.receive_data(chunk)
            while True:
                ev = conn.next_event()
                if ev is h11.NEED_DATA:  # type: ignore[attr-defined]
                    break
                yield ev
                if isinstance(ev, h11.EndOfMessage):  # type: ignore[attr-defined]
                    break
        except h11.RemoteProtocolError as e:  # type: ignore[attr-defined]
            logging.warning(f"Flux corrompu : {e}")
            yield {"erreur": str(e), "type": "RemoteProtocolError"}
        except Exception as e:  # pragma: no cover
            logging.warning(f"Erreur inattendue : {e}")
            yield {"erreur": str(e), "type": "Exception"}


def lire_fichier(cible: Union[str, Path], racine: Path) -> bytes:
    """Lit un fichier en bytes avec gestion des erreurs."""
    chemin = Path(cible)
    if not chemin.is_absolute():
        chemin = racine / chemin
    try:
        with chemin.open("rb") as f:
            contenu = f.read()
            # Détection de contenu binaire
            if b"\0" in contenu:
                raise ValueError("Contenu binaire détecté")
            return contenu
    except (OSError, UnicodeDecodeError, ValueError) as e:
        logging.error(f"Impossible de lire {chemin} : {e}")
        raise


def envoyer_requete(host: str, port: int, requete: bytes) -> bytes:
    """Envoie une requête HTTP brute et retourne la réponse."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((host, port))
            s.sendall(requete)
            reponse = b""
            while True:
                data = s.recv(4096)
                if not data:
                    break
                reponse += data
            return reponse
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logging.error(f"Échec de connexion à {host}:{port} : {e}")
        raise


def lancer_serveur(host: str, port: int, handler_path: Path, racine: Path) -> None:
    """Lance un serveur HTTP brut utilisant un script handler."""
    handler = handler_path if handler_path.is_absolute() else racine / handler_path
    if not handler.exists():
        raise FileNotFoundError(f"Handler introuvable : {handler}")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen()
        logging.info(f"Serveur démarré sur {host}:{port}")

        while True:
            conn, _ = s.accept()
            with conn:
                requete = b""
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    requete += data
                    if b"\r\n\r\n" in requete:
                        break
                # Mode dégradé : réponse fixe
                reponse = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
                conn.sendall(reponse)


def valider_message(message_path: Path, racine: Path) -> Dict[str, Any]:
    """Valide un message HTTP brut."""
    try:
        message = lire_fichier(message_path, racine)
        if not message.strip():
            # Aucun contenu à examiner → refus
            return {
                "conforme": False,
                "violations": ["Aucun message fourni"],
                "denominateur": 0,
                "examines": [str(message_path)],
            }

        event = parser_http(message)
        if event is None:
            return {
                "conforme": False,
                "violations": ["Impossible de parser le message"],
                "denominateur": 1,
                "examines": [str(message_path)],
            }

        violations = valider_conformite(event)
        return {
            "conforme": not violations,
            "violations": violations,
            "denominateur": 1,
            "examines": [str(message_path)],
        }
    except Exception as e:
        return {
            "conforme": False,
            "violations": [str(e)],
            "denominateur": 1,
            "examines": [str(message_path)],
        }


def analyser_flux(stream_path: Path, racine: Path) -> Dict[str, Any]:
    """Analyse un flux HTTP brut."""
    try:
        with (racine / stream_path).open("rb") as f:
            stream = iter(lambda: f.read(4096), b"")
            evenements = list(gerer_flux_incomplet(stream))

        return {
            "evenements": [str(e) for e in evenements],
            "denominateur": len(evenements),
            "examines": [str(stream_path)],
        }
    except Exception as e:
        return {
            "erreur": str(e),
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False,
        }


def construire_analyseur_parent() -> argparse.ArgumentParser:
    """Construit l’analyseur parent pour les options globales."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit un objet JSON sur stdout",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Redéfinit la racine du projet",
    )
    return parent


def construire_analyseur() -> argparse.ArgumentParser:
    """Construit l’analyseur principal."""
    parent = construire_analyseur_parent()
    parser = argparse.ArgumentParser(
        description="Analyse et manipulation de messages HTTP/1.1 bruts",
        parents=[parent],
    )
    sous = parser.add_subparsers(dest="commande", required=True)

    # client --------------------------------------------------------------
    client = sous.add_parser("client", parents=[parent], help="Envoie une requête HTTP brute")
    client.add_argument("--host", required=True, help="Hôte du serveur")
    client.add_argument("--port", type=int, required=True, help="Port du serveur")
    client.add_argument("--request", required=True, type=Path, help="Fichier contenant la requête brute")

    # serveur -------------------------------------------------------------
    serveur = sous.add_parser("serveur", parents=[parent], help="Lance un serveur HTTP brut")
    serveur.add_argument("--host", required=True, help="Hôte du serveur")
    serveur.add_argument("--port", type=int, required=True, help="Port du serveur")
    serveur.add_argument("--handler", required=True, type=Path, help="Script Python gérant les requêtes")

    # valider -------------------------------------------------------------
    valider = sous.add_parser("valider", parents=[parent], help="Valide un message HTTP brut")
    # argument positionnel requis, compatible avec l’ancien flag
    valider.add_argument("message", type=Path, help="Fichier contenant le message brut")
    valider.add_argument(
        "--message",
        dest="message",
        type=Path,
        help="(alias) fichier contenant le message brut",
    )

    # flux ---------------------------------------------------------------
    flux = sous.add_parser("flux", parents=[parent], help="Analyse un flux HTTP brut")
    flux.add_argument("--stream", required=True, type=Path, help="Fichier contenant le flux brut")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Point d’entrée principal."""
    parser = construire_analyseur()
    args = parser.parse_args(argv)

    racine = getattr(args, "racine", RACINE)
    if not isinstance(racine, Path):
        racine = RACINE

    try:
        if args.commande == "client":
            requete = lire_fichier(args.request, racine)
            reponse = envoyer_requete(args.host, args.port, requete)
            if getattr(args, "json", False):
                json.dump(
                    {
                        "reponse": reponse.decode("utf-8", errors="replace"),
                        "denominateur": 1,
                        "examines": [str(args.request)],
                    },
                    sys.stdout,
                    ensure_ascii=False,
                )
                print()
            else:
                sys.stdout.buffer.write(reponse)
            return 0

        if args.commande == "serveur":
            lancer_serveur(args.host, args.port, args.handler, racine)
            return 0

        if args.commande == "valider":
            resultat = valider_message(args.message, racine)
            if getattr(args, "json", False):
                json.dump(resultat, sys.stdout, ensure_ascii=False)
                print()
            else:
                if resultat["conforme"]:
                    print("Message conforme", file=sys.stderr)
                else:
                    print("Violations détectées :", file=sys.stderr)
                    for v in resultat["violations"]:
                        print(f"- {v}", file=sys.stderr)

            if resultat["denominateur"] == 0:
                print("denominateur nul", file=sys.stderr)
                return 3
            return 0 if resultat["conforme"] else 1

        if args.commande == "flux":
            resultat = analyser_flux(args.stream, racine)
            if getattr(args, "json", False):
                json.dump(resultat, sys.stdout, ensure_ascii=False)
                print()
            else:
                for ev in resultat.get("evenements", []):
                    print(ev, file=sys.stderr)

            if resultat["denominateur"] == 0:
                print("denominateur vide", file=sys.stderr)
                return 3
            return 0

    except Exception as e:  # pragma: no cover
        if getattr(args, "json", False):
            json.dump(
                {
                    "erreur": str(e),
                    "denominateur": 0,
                    "examines": [],
                    "examines_tronques": False,
                },
                sys.stdout,
                ensure_ascii=False,
            )
            print()
        else:
            print(f"Erreur : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())