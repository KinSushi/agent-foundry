"""
QUESTION      Quels événements ce flux émet‑il en continu ?
MESURE        Observation directe des événements Server‑Sent Events (SSE) et
              WebSocket en temps réel, avec identification des types d’événements
              et de leur structure. Le fichier fourni est aussi accepté comme
              source d’événements « file ».
HYPOTHESES   Le flux émet des événements nommés avec des données structurées
              (JSON ou texte). Un fichier texte contient une ligne par événement.
LIMITES       Ne détecte pas les événements émis avant la connexion.
              Ne gère pas les flux chiffrés sans bibliothèques tierces.
CONTRE-EXEMPLES Un flux qui n’émet que des keep‑alive sans données utiles.
INVOCATION
    {outil} {fichier} --json
DOMAINE       Flux accessibles via HTTP/HTTPS avec en‑têtes SSE ou WebSocket,
              ou fichiers texte locaux.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import selectors
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Racine portable – surcharge possible via --racine
RACINE = Path(__file__).resolve().parent

# Encodage UTF‑8 pour les consoles Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = ["analyser_flux", "main"]


class _JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui laisse argparse gérer les erreurs inconnues."""

    def error(self, message: str) -> None:  # pragma: no cover
        # argparse renvoie déjà le code 2 et l’aide.
        super().error(message)


@dataclass
class Evenement:
    protocole: str
    type: str
    donnees: str
    identifiant: Optional[str] = None
    retry: Optional[int] = None
    timestamp: float = 0.0


async def _ecouter_sse_standard(url: str, timeout: float, out: asyncio.Queue) -> None:
    """Écoute un flux SSE avec les modules standard."""
    try:
        parsed = urlparse(url)
        reader, writer = await asyncio.open_connection(
            parsed.hostname,
            80 if parsed.scheme == "http" else 443,
            ssl=parsed.scheme == "https",
        )
        path = parsed.path or "/"
        writer.write(
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: keep-alive\r\n\r\n".encode()
        )
        await writer.drain()

        buffer = ""
        while True:
            data = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not data:
                break
            buffer += data.decode("utf-8", errors="replace")
            while "\n\n" in buffer:
                event_data, buffer = buffer.split("\n\n", 1)
                ev = _parser_evenement_sse(event_data)
                if ev:
                    await out.put(ev)
    except Exception as e:
        await out.put({"erreur": f"SSE standard échoué : {e}"})
    finally:
        if "writer" in locals():
            writer.close()
            await writer.wait_closed()


def _parser_evenement_sse(data: str) -> Optional[Evenement]:
    ev = Evenement(protocole="SSE", type="message", donnees="")
    for line in data.splitlines():
        if not line.strip() or line.startswith(":"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            if key == "event":
                ev.type = value
            elif key == "data":
                ev.donnees = value
            elif key == "id":
                ev.identifiant = value
            elif key == "retry":
                try:
                    ev.retry = int(value)
                except ValueError:
                    pass
    if ev.donnees:
        ev.timestamp = time.time()
        return ev
    return None


async def _ecouter_sse_tierce(url: str, timeout: float, out: asyncio.Queue) -> None:
    """Écoute un flux SSE avec httpx‑sse si disponible."""
    try:
        import httpx
        from httpx_sse import connect_sse

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with connect_sse(client, "GET", url) as source:
                async for sse in source.aiter_sse():
                    await out.put(
                        Evenement(
                            protocole="SSE",
                            type=sse.event or "message",
                            donnees=sse.data,
                            identifiant=sse.id,
                            retry=sse.retry,
                            timestamp=time.time(),
                        )
                    )
    except ImportError:
        await out.put({"erreur": "httpx‑sse non disponible"})
    except Exception as e:
        await out.put({"erreur": f"SSE tierce échoué : {e}"})


async def _ecouter_websocket_standard(url: str, timeout: float, out: asyncio.Queue) -> None:
    """Écoute un flux WebSocket avec les modules standard."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"

        sel = selectors.DefaultSelector()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)

        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)

        await asyncio.get_event_loop().sock_connect(sock, (host, port))

        key = base64.b64encode(hashlib.sha1(os.urandom(16)).digest()).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.send(handshake.encode())

        sel.register(sock, selectors.EVENT_READ)
        while True:
            events = sel.select(timeout=timeout)
            if not events:
                break
            for key, _ in events:
                data = key.fileobj.recv(4096)
                if not data:
                    break
                await out.put(
                    Evenement(
                        protocole="WebSocket",
                        type="message",
                        donnees=data.decode("utf-8", errors="replace"),
                        timestamp=time.time(),
                    )
                )
    except Exception as e:
        await out.put({"erreur": f"WebSocket standard échoué : {e}"})
    finally:
        if "sock" in locals():
            sock.close()


async def _ecouter_websocket_tierce(url: str, timeout: float, out: asyncio.Queue) -> None:
    """Écoute un flux WebSocket avec websockets si disponible."""
    try:
        import websockets

        async with websockets.connect(url, open_timeout=timeout) as ws:
            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    await out.put(
                        Evenement(
                            protocole="WebSocket",
                            type="message",
                            donnees=message,
                            timestamp=time.time(),
                        )
                    )
                except asyncio.TimeoutError:
                    break
    except ImportError:
        await out.put({"erreur": "websockets non disponible"})
    except Exception as e:
        await out.put({"erreur": f"WebSocket tierce échoué : {e}"})


async def _lire_fichier(path: Path, out: asyncio.Queue) -> None:
    """Lit un fichier texte et crée un événement par ligne."""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    await out.put(
                        Evenement(
                            protocole="file",
                            type="line",
                            donnees=line,
                            timestamp=time.time(),
                        )
                    )
    except Exception as e:
        await out.put({"erreur": f"Lecture fichier échouée : {e}"})


async def analyser_flux(url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Analyse les événements d’un flux ou d’un fichier."""
    out = asyncio.Queue()
    tasks: List[asyncio.Task] = []

    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        tasks.append(asyncio.create_task(_ecouter_sse_standard(url, timeout, out)))
        tasks.append(asyncio.create_task(_ecouter_sse_tierce(url, timeout, out)))
    elif parsed.scheme in ("ws", "wss"):
        tasks.append(asyncio.create_task(_ecouter_websocket_standard(url, timeout, out)))
        tasks.append(asyncio.create_task(_ecouter_websocket_tierce(url, timeout, out)))
    else:
        path = Path(url)
        if path.is_file():
            tasks.append(asyncio.create_task(_lire_fichier(path, out)))
        else:
            raise ValueError("URL doit être HTTP(S), WS(S) ou un fichier existant")

    evenements: List[Evenement] = []
    erreurs: List[str] = []
    try:
        while True:
            try:
                res = await asyncio.wait_for(out.get(), timeout=timeout)
                if isinstance(res, dict) and "erreur" in res:
                    erreurs.append(res["erreur"])
                else:
                    evenements.append(res)
            except asyncio.TimeoutError:
                break
    finally:
        for t in tasks:
            t.cancel()

    denominateur = len(evenements)
    examinateurs = [f"{e.protocole}:{e.type}" for e in evenements]

    resultat: Dict[str, Any] = {
        "denominateur": denominateur,
        "examines": examinateurs,
        "contrat": {
            "QUESTION": "Quels événements ce flux émet‑il en continu ?",
            "MESURE": "Protocoles SSE, WebSocket et fichiers texte analysés.",
            "HYPOTHESES": "Le flux ou le fichier fournit des lignes d’événements.",
            "LIMITES": "Pas d’événements précédant la connexion, dépendances tierces optionnelles.",
            "CONTRE-EXEMPLES": "Flux ne produisant que des keep‑alive.",
            "DOMAINE": "Flux HTTP/HTTPS, WS/WSS ou fichiers texte locaux.",
        },
    }
    if erreurs:
        resultat["erreur"] = erreurs[0] if len(erreurs) == 1 else erreurs
    return resultat


def main() -> int:
    """Point d’entrée CLI."""
    parser = _JSONArgumentParser(
        description="Écoute les événements d’un flux SSE ou WebSocket.",
        epilog="Exemple : python ecouter_evenements.py http://exemple.com/flux --timeout 15 --json",
    )
    parser.add_argument("url", help="URL du flux ou chemin d’un fichier texte.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout en secondes (défaut : 10)")
    parser.add_argument("--json", action="store_true", help="Sortie au format JSON")
    parser.add_argument("--racine", type=Path, help="Répertoire racine à ajouter au PYTHONPATH")

    try:
        args = parser.parse_args()
    except SystemExit:
        raise  # argparse a déjà géré l’erreur (code 2)

    if args.racine:
        sys.path.insert(0, str(args.racine.resolve()))

    try:
        resultat = asyncio.run(analyser_flux(args.url, args.timeout))
    except Exception as e:
        if args.json:
            json.dump({"erreur": str(e)}, sys.stdout, ensure_ascii=False)
            return 1
        print(f"Erreur lors de l’analyse : {e}", file=sys.stderr)
        return 1

    if resultat["denominateur"] == 0:
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        return 3

    if args.json:
        json.dump(resultat, sys.stdout, ensure_ascii=False)
        return 0

    for i, ev in enumerate(resultat.get("examines", []), 1):
        print(f"{i}. {ev}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())