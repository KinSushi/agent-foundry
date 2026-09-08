"""QUESTION
Interroger un service HTTP et déterminer sa réponse ainsi que les modalités de négociation.
MESURE
Utilisation de bibliothèques standard ou tierces (httpx, aiohttp, requests, urllib3) pour récupérer la réponse HTTP.
HYPOTHESES
Le service répond avec un code HTTP, des en‑têtes et un corps texte ou binaire.
LIMITES
Pas de support natif HTTP/2 ou de retries avancés sans bibliothèques tierces.
CONTRE-EXEMPLES
Un service qui nécessite une authentification complexe ou un protocole propriétaire ne sera pas géré.
INVOCATION
    {outil} https://example.com --json --reseau
DOMAINE
Analyse de services HTTP accessibles via une URL.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Reconfiguration de la sortie pour UTF‑8 même sous cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["interroger_http", "analyser_http"]

def _importer_modules_optionnels() -> Tuple[Dict[str, Any], List[str]]:
    """Importe les modules tiers éventuels.

    Retourne un tuple (modules, absents) où *modules* est un dict des modules
    importés et *absents* la liste des noms qui n’ont pas pu être importés.
    """
    modules: Dict[str, Any] = {}
    absents: List[str] = []
    return modules, absents  # Désactivation des imports réseau par défaut

def _construire_sortie_json(denominateur: int, resultat: Dict[str, Any] = None,
                          erreur: str = None) -> Dict[str, Any]:
    """Construit le dictionnaire de sortie JSON standardisé.

    Args:
        denominateur: Valeur entière du dénominateur (0 pour refus)
        resultat: Dictionnaire de résultat si succès
        erreur: Message d'erreur si échec

    Returns:
        Dictionnaire prêt pour json.dump()
    """
    base = {
        "denominateur": denominateur,
        "contrat": _extraire_contrat()
    }

    if erreur:
        base["erreur"] = erreur
    elif resultat:
        base["resultat"] = resultat
    else:
        base["examines"] = []

    return base

def interroger_http(url: str, allow_network: bool = False) -> Dict[str, Any]:
    """Effectue une requête GET synchronisée sur *url*.

    Retourne un dictionnaire contenant :
        - status  : code HTTP (int)
        - headers : mapping des en‑têtes (dict)
        - body    : corps décodé en UTF‑8 ou encodé en base64 (str)
    """
    if not allow_network:
        raise RuntimeError("Accès réseau désactivé par défaut. Utilisez --reseau pour l'activer.")

    modules, _ = _importer_modules_optionnels()

    # Fallback bibliothèque standard (seule autorisée sans option)
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.getcode()
        headers = dict(resp.getheaders())
        body_bytes = resp.read()
        try:
            body = body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body = base64.b64encode(body_bytes).decode("ascii")
        return {"status": status, "headers": headers, "body": body}

def analyser_http(url: str, allow_network: bool = False) -> Tuple[int, Dict[str, Any], List[str]]:
    """Analyse le service HTTP indiqué par *url*.

    Retourne (denominateur, résultat, diagnostics).
    *denominateur* vaut 1 si la requête a réussi, 0 sinon.
    *résultat* est le dictionnaire produit par :func:`interroger_http`.
    *diagnostics* contient les messages à envoyer sur stderr.
    """
    diagnostics: List[str] = []
    if not allow_network:
        diagnostics.append("Denominateur nul : accès réseau désactivé, refus de conclure.")
        return 0, {}, diagnostics

    try:
        resultat = interroger_http(url, allow_network=True)
        return 1, resultat, diagnostics
    except (urllib.error.URLError, ValueError) as exc:
        diagnostics.append(f"Erreur d’accès à l’URL « {url} » : {exc}")
        return 0, {}, diagnostics
    except Exception as exc:  # aucune exception ne doit remonter
        diagnostics.append(f"Erreur inattendue : {exc}")
        return 0, {}, diagnostics

def _extraire_contrat() -> Dict[str, str]:
    """Construit le dictionnaire « contrat » à partir de la docstring du module."""
    sections = ("QUESTION", "MESURE", "HYPOTHESES", "LIMITES", "CONTRE-EXEMPLES", "DOMAINE")
    contrat: Dict[str, str] = {}
    doc = __doc__ or ""
    for i, sec in enumerate(sections):
        start = doc.find(sec)
        if start == -1:
            continue
        # le contenu commence après le titre et le saut de ligne suivant
        content_start = doc.find("\n", start) + 1
        # fin au prochain titre ou à la fin du docstring
        end = len(doc)
        for nxt in sections[i + 1 :]:
            nxt_pos = doc.find(nxt, content_start)
            if nxt_pos != -1:
                end = nxt_pos
                break
        contrat[sec.lower()] = doc[content_start:end].strip()
    return contrat

def _construire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interroge un service HTTP et rapporte sa réponse.",
        epilog="Exemple d’appel réel : python interroger_http.py https://example.com --json --reseau",
    )
    parser.add_argument(
        "cible",
        help="URL du service HTTP à interroger (ex. https://example.com)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="Chemin racine à préfixer à sys.path avant importation éventuelle.",
    )
    parser.add_argument(
        "--reseau",
        action="store_true",
        help="Autoriser l'accès réseau (désactivé par défaut).",
    )
    return parser

def main() -> int:
    parser = _construire_parser()
    args = parser.parse_args()

    # Gestion du paramètre --racine
    if args.racine is not None:
        racine_path = args.racine.resolve()
        if not racine_path.is_dir():
            if args.json:
                sortie = _construire_sortie_json(0, erreur=f"Le chemin fourni à --racine n'est pas un répertoire : {racine_path}")
                json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            else:
                print(
                    f"Le chemin fourni à --racine n'est pas un répertoire : {racine_path}",
                    file=sys.stderr,
                )
            return 2

        sys.path.insert(0, str(racine_path))

    denominateur, resultat, diagnostics = analyser_http(args.cible, allow_network=args.reseau)

    for msg in diagnostics:
        print(msg, file=sys.stderr)

    if denominateur == 0:
        if args.json:
            sortie = _construire_sortie_json(0, erreur="Impossible de conclure : aucune donnée valide n'a été obtenue.")
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 3

    # Construction de la sortie
    if args.json:
        sortie = _construire_sortie_json(denominateur, resultat=resultat)
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Statut HTTP : {resultat.get('status')}")
        print("En‑têtes :")
        for k, v in resultat.get("headers", {}).items():
            print(f"  {k}: {v}")
        print("\nCorps de la réponse :")
        print(resultat.get("body", ""))

    return 0

if __name__ == "__main__":
    raise SystemExit(main())