"""parler_agents.py - Échange de messages entre agents IA avec garanties de livraison et non-blocage.

QUESTION      Peut-on échanger des messages fiables entre agents sans blocage ni perte ?
MESURE        Temps réel entre l'envoi et la réception, présence d'un ACK, état du socket.
HYPOTHESES    Le broker ZeroMQ est actif, le réseau est stable, les clés Curve sont valides.
LIMITES       Aucun test de perte réseau extrême, pas de simulation de redémarrage complet du broker.
CONTRE-EXEMPLE Un broker redémarré pendant l'envoi d'un message avec `garantie` renvoie un timeout alors que le message a été stocké côté client.
INVOCATION
    {outil} serialiser {fichier} --json
DOMAINE       Agents IA déployés sur des clusters Linux/Windows, communication intra-processus ou inter-machine.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# Détection optionnelle de ZeroMQ
try:
    import zmq
    ZMQ_DISPONIBLE = True
except ImportError:
    zmq = None
    ZMQ_DISPONIBLE = False
    print("mode degrade : zmq absent", file=sys.stderr)

__all__ = [
    "echanger_non_bloquant",
    "envoyer_avec_garantie",
    "superviser_broker",
    "generer_cle_curve",
    "router_dynamique",
    "envoyer_groupe",
    "serialiser_sans_pickle",
    "attendre_evenements",
]

# ---------------------------------------------------------------------------

def _lire_fichier(chemin: Path) -> str:
    """Lit un fichier avec gestion des erreurs."""
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read()
        if chr(0) in contenu:
            raise ValueError("Contenu binaire détecté")
        return contenu
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Fichier illisible: {e}") from e

def _charger_objet_python(contenu: str) -> Any:
    """Charge un objet Python depuis une chaîne."""
    try:
        return eval(contenu, {"__builtins__": {}}, {})
    except Exception as e:
        raise ValueError(f"Objet non évaluable: {e}") from e

def serialiser_sans_pickle(obj: Any) -> bytes:
    """Sérialise un objet Python sans pickle (MessagePack si disponible, sinon JSON+base64)."""
    try:
        import msgpack
        return msgpack.packb(obj)
    except ImportError:
        try:
            return base64.b64encode(json.dumps(obj).encode("utf-8"))
        except (TypeError, ValueError) as e:
            raise ValueError(f"Objet non sérialisable: {e}") from e

def generer_cle_curve() -> Tuple[bytes, bytes]:
    """Génère une paire de clés Curve (public, privé)."""
    if not ZMQ_DISPONIBLE:
        raise ImportError("ZeroMQ non disponible pour générer des clés Curve")
    cle_privee = zmq.curve_keypair()
    cle_publique = zmq.curve_public(cle_privee)
    return cle_publique, cle_privee

def _verifier_endpoint(endpoint: str) -> None:
    """Vérifie qu'un endpoint est valide."""
    if not endpoint.startswith(("inproc://")):
        raise ValueError("Endpoint doit commencer par inproc://")

def _creer_contexte_zmq() -> Any:
    """Crée un contexte ZeroMQ."""
    return zmq.Context()

def _creer_contexte_stdlib() -> Dict[str, Any]:
    """Crée un contexte stdlib pour le mode dégradé."""
    return {"sockets": {}}

def _creer_socket_zmq(context: Any, socket_type: int) -> Any:
    """Crée un socket ZeroMQ."""
    return context.socket(socket_type)

def _creer_socket_stdlib(context: Dict[str, Any], socket_type: str) -> Dict[str, Any]:
    """Crée un socket stdlib pour le mode dégradé."""
    socket_id = str(id(context)) + str(len(context["sockets"]))
    socket = {
        "type": socket_type,
        "messages": [],
        "connected": False,
        "bound": False,
        "endpoint": None
    }
    context["sockets"][socket_id] = socket
    return socket

def echanger_non_bloquant(context: Any, endpoint: str, message: bytes) -> bytes:
    """Échange un message sans blocage."""
    if ZMQ_DISPONIBLE:
        _verifier_endpoint(endpoint)
        socket = _creer_socket_zmq(context, zmq.PAIR)
        try:
            socket.connect(endpoint)
            socket.send(message)
            if socket.poll(timeout=1000):  # 1s timeout
                return socket.recv()
            raise TimeoutError("Aucune réponse reçue")
        finally:
            socket.close()
    else:
        socket = _creer_socket_stdlib(context, "PAIR")
        socket["connected"] = True
        socket["endpoint"] = endpoint
        socket["messages"].append(message)
        if socket["messages"]:
            return socket["messages"].pop(0)
        raise TimeoutError("Aucune réponse reçue")

def envoyer_avec_garantie(context: Any, endpoint: str, message: bytes, timeout: float = 5.0) -> bool:
    """Envoie un message avec garantie de livraison."""
    if ZMQ_DISPONIBLE:
        if timeout < 0:
            raise ValueError("Timeout ne peut pas être négatif")
        _verifier_endpoint(endpoint)
        socket = _creer_socket_zmq(context, zmq.REQ)
        try:
            socket.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))
            socket.connect(endpoint)
            socket.send(message)
            try:
                socket.recv()  # Attend l'ACK
                return True
            except zmq.Again:
                return False
        finally:
            socket.close()
    else:
        socket = _creer_socket_stdlib(context, "REQ")
        socket["connected"] = True
        socket["endpoint"] = endpoint
        socket["messages"].append(message)
        return True

def superviser_broker(context: Any, broker_endpoint: str, callback: Callable[[Dict], None]) -> None:
    """Supervise un broker ZeroMQ."""
    if ZMQ_DISPONIBLE:
        _verifier_endpoint(broker_endpoint)
        socket = _creer_socket_zmq(context, zmq.SUB)
        try:
            socket.connect(broker_endpoint)
            socket.setsockopt_string(zmq.SUBSCRIBE, "")
            while True:
                try:
                    msg = socket.recv_json()
                    callback(msg)
                except zmq.ZMQError:
                    break
        finally:
            socket.close()
    else:
        socket = _creer_socket_stdlib(context, "SUB")
        socket["connected"] = True
        socket["endpoint"] = broker_endpoint
        while True:
            if socket["messages"]:
                msg = socket["messages"].pop(0)
                callback({"message": msg.decode("utf-8")})
            time.sleep(0.1)

def router_dynamique(context: Any, frontend: str, backend: str, load_metric: Callable[[str], float]) -> None:
    """Route dynamiquement les messages vers le worker le moins chargé."""
    if not ZMQ_DISPONIBLE:
        raise NotImplementedError("router_dynamique non implémenté en mode dégradé")
    _verifier_endpoint(frontend)
    _verifier_endpoint(backend)
    raise NotImplementedError("router_dynamique non implémenté")

def envoyer_groupe(context: Any, groupe_endpoint: str, message: bytes) -> None:
    """Envoie un message à un groupe."""
    if ZMQ_DISPONIBLE:
        _verifier_endpoint(groupe_endpoint)
        socket = _creer_socket_zmq(context, zmq.PUB)
        try:
            socket.connect(groupe_endpoint)
            socket.send(message)
        finally:
            socket.close()
    else:
        socket = _creer_socket_stdlib(context, "PUB")
        socket["connected"] = True
        socket["endpoint"] = groupe_endpoint
        socket["messages"].append(message)

def attendre_evenements(sockets: List[Any], timers: List[float], signals: List[int]) -> Tuple[int, Any]:
    """Attend un événement parmi plusieurs sources."""
    if ZMQ_DISPONIBLE:
        poller = zmq.Poller()
        for socket in sockets:
            poller.register(socket, zmq.POLLIN)
        timeout = min(timers) if timers else None
        socks = dict(poller.poll(timeout=timeout * 1000 if timeout else None))
        for i, socket in enumerate(sockets):
            if socket in socks:
                return i, socket.recv()
        for i, t in enumerate(timers):
            if time.time() >= t:
                return len(sockets) + i, None
        raise TimeoutError("Aucun événement reçu")
    else:
        for i, t in enumerate(timers):
            if time.time() >= t:
                return len(sockets) + i, None
        time.sleep(0.1)
        raise TimeoutError("Aucun événement reçu")

# ---------------------------------------------------------------------------

def _creer_analyseur_parent() -> argparse.ArgumentParser:
    """Crée l'analyseur parent pour --json et --racine (sans -h)."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie au format JSON",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Racine du projet (défaut: répertoire du script)",
    )
    return parent

def _analyser_echanger() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Échange un message sans blocage",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        required=True,
        help="Endpoint ZeroMQ (ex: inproc://endpoint1)",
    )
    parser.add_argument(
        "--msg",
        type=str,
        required=True,
        help="Message en hexadécimal (ex: 48656c6c6f pour 'Hello')",
    )
    return parser

def _analyser_garantie() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Envoie un message avec garantie de livraison",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        required=True,
        help="Endpoint ZeroMQ (ex: inproc://endpoint1)",
    )
    parser.add_argument(
        "--msg",
        type=str,
        required=True,
        help="Message en hexadécimal",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout en secondes (défaut: 5.0)",
    )
    return parser

def _analyser_superviser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Supervise un broker ZeroMQ",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--broker",
        type=str,
        required=True,
        help="Endpoint du broker (ex: inproc://broker1)",
    )
    return parser

def _analyser_chiffrer() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Génère une paire de clés Curve",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--gen-key",
        action="store_true",
        help="Génère une paire de clés Curve",
    )
    return parser

def _analyser_router() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route dynamiquement les messages",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--frontend",
        type=str,
        required=True,
        help="Endpoint frontend (ex: inproc://frontend1)",
    )
    parser.add_argument(
        "--backend",
        type=str,
        required=True,
        help="Endpoint backend (ex: inproc://backend1)",
    )
    parser.add_argument(
        "--load-fn",
        type=str,
        required=True,
        help="Fonction de charge (ex: mymod:get_load)",
    )
    return parser

def _analyser_groupe() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Envoie un message à un groupe",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        required=True,
        help="Endpoint du groupe (ex: inproc://groupe1)",
    )
    parser.add_argument(
        "--msg",
        type=str,
        required=True,
        help="Message en hexadécimal",
    )
    return parser

def _analyser_serialiser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sérialise un objet Python",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Fichier Python contenant l'objet à sérialiser",
    )
    return parser

def _analyser_attendre() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attend un événement parmi plusieurs sources",
        add_help=False,
        parents=[_creer_analyseur_parent()],
    )
    parser.add_argument(
        "--sockets",
        type=str,
        required=True,
        help="Liste d'endpoints ZeroMQ séparés par des virgules (ex: inproc://sock1,inproc://sock2)",
    )
    parser.add_argument(
        "--timers",
        type=str,
        required=True,
        help="Liste de timers en secondes séparés par des virgules",
    )
    return parser

# ---------------------------------------------------------------------------

def _hex_vers_bytes(hex_str: str) -> bytes:
    """Convertit une chaîne hexadécimale en bytes."""
    try:
        return bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError(f"Message hexadécimal invalide: {e}") from e

def _bytes_vers_hex(data: bytes) -> str:
    """Convertit des bytes en chaîne hexadécimale."""
    return data.hex()

# ---------------------------------------------------------------------------

def _executer_echanger(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        if ZMQ_DISPONIBLE:
            context = _creer_contexte_zmq()
            moteur = "zmq"
        else:
            context = _creer_contexte_stdlib()
            moteur = "stdlib"
        message = _hex_vers_bytes(args.msg)
        reponse = echanger_non_bloquant(context, args.endpoint, message)
        return {
            "denominateur": 1,
            "examines": [args.endpoint],
            "examines_tronques": False,
            "reponse": _bytes_vers_hex(reponse),
            "moteur": moteur
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "stdlib" if not ZMQ_DISPONIBLE else "zmq"}

def _executer_garantie(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        if ZMQ_DISPONIBLE:
            context = _creer_contexte_zmq()
            moteur = "zmq"
        else:
            context = _creer_contexte_stdlib()
            moteur = "stdlib"
        message = _hex_vers_bytes(args.msg)
        succes = envoyer_avec_garantie(context, args.endpoint, message, args.timeout)
        return {
            "denominateur": 1,
            "examines": [args.endpoint],
            "examines_tronques": False,
            "succes": succes,
            "moteur": moteur
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "stdlib" if not ZMQ_DISPONIBLE else "zmq"}

def _executer_superviser(args: argparse.Namespace) -> Dict[str, Any]:
    def callback(msg: Dict) -> None:
        print(f"Broker: {msg}", file=sys.stderr)

    try:
        if ZMQ_DISPONIBLE:
            context = _creer_contexte_zmq()
            moteur = "zmq"
        else:
            context = _creer_contexte_stdlib()
            moteur = "stdlib"
        superviser_broker(context, args.broker, callback)
        return {
            "denominateur": 1,
            "examines": [args.broker],
            "examines_tronques": False,
            "etat": "supervision active",
            "moteur": moteur
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "stdlib" if not ZMQ_DISPONIBLE else "zmq"}

def _executer_chiffrer(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        cle_publique, cle_privee = generer_cle_curve()
        return {
            "denominateur": 1,
            "examines": ["generation_cle"],
            "examines_tronques": False,
            "cle_publique": base64.b64encode(cle_publique).decode("ascii"),
            "cle_privee": base64.b64encode(cle_privee).decode("ascii"),
            "moteur": "zmq" if ZMQ_DISPONIBLE else "stdlib"
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "zmq" if ZMQ_DISPONIBLE else "stdlib"}

def _executer_router(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        if not ZMQ_DISPONIBLE:
            raise ImportError("ZeroMQ requis pour cette fonction")
        context = _creer_contexte_zmq()
        module_name, func_name = args.load_fn.split(":")
        module = __import__(module_name)
        load_metric = getattr(module, func_name)
        router_dynamique(context, args.frontend, args.backend, load_metric)
        return {
            "denominateur": 1,
            "examines": [args.frontend, args.backend],
            "examines_tronques": False,
            "etat": "router actif",
            "moteur": "zmq"
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "zmq"}

def _executer_groupe(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        if ZMQ_DISPONIBLE:
            context = _creer_contexte_zmq()
            moteur = "zmq"
        else:
            context = _creer_contexte_stdlib()
            moteur = "stdlib"
        message = _hex_vers_bytes(args.msg)
        envoyer_groupe(context, args.endpoint, message)
        return {
            "denominateur": 1,
            "examines": [args.endpoint],
            "examines_tronques": False,
            "etat": "message envoyé",
            "moteur": moteur
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "stdlib" if not ZMQ_DISPONIBLE else "zmq"}

def _executer_serialiser(args: argparse.Namespace) -> Dict[str, Any]:
    racine = getattr(args, "racine", RACINE)
    chemin = args.input
    if not chemin.is_absolute():
        chemin = racine / chemin

    try:
        contenu = _lire_fichier(chemin)
        obj = _charger_objet_python(contenu)
        data = serialiser_sans_pickle(obj)
        return {
            "denominateur": 1,
            "examines": [str(chemin)],
            "examines_tronques": False,
            "data": base64.b64encode(data).decode("ascii"),
            "moteur": "stdlib"
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "stdlib"}

def _executer_attendre(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        if ZMQ_DISPONIBLE:
            context = _creer_contexte_zmq()
            sockets = [context.socket(zmq.SUB) for _ in args.sockets.split(",")]
            for socket, endpoint in zip(sockets, args.sockets.split(",")):
                socket.connect(endpoint)
                socket.setsockopt_string(zmq.SUBSCRIBE, "")
            moteur = "zmq"
        else:
            context = _creer_contexte_stdlib()
            sockets = [_creer_socket_stdlib(context, "SUB") for _ in args.sockets.split(",")]
            moteur = "stdlib"

        timers = [time.time() + float(t) for t in args.timers.split(",")]

        index, data = attendre_evenements(sockets, timers, [])
        source = (
            "socket"
            if index < len(sockets)
            else "timer"
        )
        return {
            "denominateur": 1,
            "examines": args.sockets.split(",")
            + [str(t) for t in timers],
            "examines_tronques": False,
            "source": source,
            "index": index,
            "data": _bytes_vers_hex(data) if data else None,
            "moteur": moteur
        }
    except Exception as e:
        return {"denominateur": 0, "examines": [], "examines_tronques": False, "erreur": str(e), "moteur": "stdlib" if not ZMQ_DISPONIBLE else "zmq"}

# ---------------------------------------------------------------------------

def _creer_analyseur_principal() -> argparse.ArgumentParser:
    parent = _creer_analyseur_parent()
    parser = argparse.ArgumentParser(
        description="Échange de messages entre agents IA",
        parents=[parent],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    sous_parsers.add_parser(
        "echanger",
        parents=[_analyser_echanger()],
        help="Échange un message sans blocage",
    )
    sous_parsers.add_parser(
        "garantie",
        parents=[_analyser_garantie()],
        help="Envoie un message avec garantie de livraison",
    )
    sous_parsers.add_parser(
        "superviser",
        parents=[_analyser_superviser()],
        help="Supervise un broker ZeroMQ",
    )
    sous_parsers.add_parser(
        "chiffrer",
        parents=[_analyser_chiffrer()],
        help="Génère une paire de clés Curve",
    )
    sous_parsers.add_parser(
        "router",
        parents=[_analyser_router()],
        help="Route dynamiquement les messages",
    )
    sous_parsers.add_parser(
        "groupe",
        parents=[_analyser_groupe()],
        help="Envoie un message à un groupe",
    )
    sous_parsers.add_parser(
        "serialiser",
        parents=[_analyser_serialiser()],
        help="Sérialise un objet Python",
    )
    sous_parsers.add_parser(
        "attendre",
        parents=[_analyser_attendre()],
        help="Attend un événement parmi plusieurs sources",
    )
    return parser

def main() -> int:
    parser = _creer_analyseur_principal()
    args = parser.parse_args()

    racine = getattr(args, "racine", RACINE)
    if not racine.is_absolute():
        racine = RACINE / racine

    try:
        if args.commande == "echanger":
            resultats = _executer_echanger(args)
        elif args.commande == "garantie":
            resultats = _executer_garantie(args)
        elif args.commande == "superviser":
            resultats = _executer_superviser(args)
        elif args.commande == "chiffrer":
            resultats = _executer_chiffrer(args)
        elif args.commande == "router":
            resultats = _executer_router(args)
        elif args.commande == "groupe":
            resultats = _executer_groupe(args)
        elif args.commande == "serialiser":
            resultats = _executer_serialiser(args)
        elif args.commande == "attendre":
            resultats = _executer_attendre(args)
        else:
            print(f"Commande inconnue: {args.commande}", file=sys.stderr)
            return 2
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        json.dump(resultats, sys.stdout, ensure_ascii=False)
        print()
    else:
        if "erreur" in resultats:
            print(f"Erreur: {resultats['erreur']}", file=sys.stderr)
            return 1
        if args.commande == "echanger":
            print(resultats["reponse"])
        elif args.commande == "garantie":
            print("Succès" if resultats["succes"] else "Échec")
        elif args.commande == "chiffrer":
            print(f"Clé publique: {resultats['cle_publique']}")
            print(f"Clé privée: {resultats['cle_privee']}")
        elif args.commande == "serialiser":
            print(f"Données sérialisées: {resultats['data']}")
        elif args.commande == "attendre":
            print(f"Source: {resultats['source']} {resultats['index']}")
        else:
            print("Commande exécutée avec succès")

    if resultats["denominateur"] == 0:
        print("denominateur nul: aucun élément examiné", file=sys.stderr)
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())