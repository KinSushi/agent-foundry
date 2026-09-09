"""contrat_mcp.py - Outil CLI pour interagir avec des services JSON-RPC 2.0 de manière non-bloquante.

QUESTION      L'outil peut-il échanger, valider, documenter et vérifier la version d'un service JSON-RPC sans bloquer l'agent ?
MESURE        Temps réel d'exécution, conformité du JSON retourné, présence des champs obligatoires, exactitude du typage, résultat booléen de la compatibilité.
HYPOTHESES    Le service distant répond en moins de 5s, le réseau est disponible, la contrainte de version est correctement formulée.
LIMITES       Aucun test de charge, pas de support HTTP/2, pas de vérification TLS avancée.
CONTRE-EXEMPLE Le service renvoie `{ "jsonrpc": "2.0", "result": 42 }` (absence du champ `id`). L'outil signale l’erreur mais continue d’exécuter les autres sous-commandes.
INVOCATION
    {outil} examiner {dossier}/a {dossier}/b --json
    {outil} examiner --texte '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' --json
DOMAINE       Agents IA qui intègrent des appels JSON-RPC dans des workflows asynchrones, sous Python 3.14+.
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["main"]

# Encodage UTF‑8 pour la console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# Détection optionnelle de mcp_types
MCP_TYPES_DISPONIBLE = False
try:
    import mcp_types.jsonrpc  # type: ignore
    import mcp_types.methods  # type: ignore
    import mcp_types.version  # type: ignore
    MCP_TYPES_DISPONIBLE = True
except ImportError:
    pass


class ErreurReseau(Exception):
    """Erreur liée à la communication réseau."""


class ErreurValidation(Exception):
    """Erreur de validation JSON‑RPC."""


class ErreurCompatibilite(Exception):
    """Erreur de compatibilité de version."""


class ErreurDocumentation(Exception):
    """Erreur liée à la documentation."""


def _envoyer_requete_stdlib(
    url: str, method: str, params: Dict[str, Any], timeout: Optional[float] = None
) -> Dict[str, Any]:
    """Envoie une requête JSON‑RPC avec la stdlib (mode dégradé)."""
    parsed = http.client.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ErreurReseau("URL doit utiliser http ou https")

    headers = {"Content-Type": "application/json"}
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    )

    try:
        conn_cls = (
            http.client.HTTPConnection
            if parsed.scheme == "http"
            else http.client.HTTPSConnection
        )
        conn = conn_cls(parsed.netloc, timeout=timeout)
        conn.request("POST", parsed.path or "/", body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if "\x00" in data:
            raise ErreurReseau("Réponse contient des données binaires")
        return json.loads(data)
    except (http.client.HTTPException, UnicodeDecodeError, json.JSONDecodeError, OSError) as e:
        raise ErreurReseau(f"Échec de la requête réseau : {e}")


async def _envoyer_requete_async(
    url: str, method: str, params: Dict[str, Any], timeout: Optional[float] = None
) -> Dict[str, Any]:
    """Envoie une requête JSON‑RPC de manière asynchrone."""
    if not MCP_TYPES_DISPONIBLE:
        return _envoyer_requete_stdlib(url, method, params, timeout)

    try:
        future = mcp_types.jsonrpc.send_async(
            url,
            {"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            timeout=timeout,
        )
        return await asyncio.wrap_future(future)
    except Exception as e:
        raise ErreurReseau(f"Échec de la requête asynchrone : {e}")


def augmenter(url: str, method: str, params: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Envoie une requête JSON‑RPC et retourne la réponse."""
    try:
        params_dict = json.loads(params)
    except json.JSONDecodeError as e:
        raise ValueError(f"Paramètres non valides : {e}")

    if "\x00" in params:
        raise ValueError("Paramètres contiennent des données binaires")

    try:
        return asyncio.run(_envoyer_requete_async(url, method, params_dict, timeout))
    except ErreurReseau:
        raise
    except Exception as e:
        raise ErreurReseau(f"Erreur inattendue : {e}")


def valider(url: str, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """Valide les méthodes exposées par le service JSON‑RPC."""
    if not MCP_TYPES_DISPONIBLE:
        raise RuntimeError("mcp_types requis pour la validation")
    try:
        methods = mcp_types.jsonrpc.list_methods(url, timeout=timeout)
        valides = []
        for m in methods:
            try:
                if mcp_types.jsonrpc.validate_response(m):
                    valides.append(m)
            except Exception:
                continue
        return valides
    except Exception as e:
        raise ErreurValidation(f"Échec de la validation : {e}")


def documenter(url: str, method: str) -> Dict[str, Any]:
    """Retourne la documentation d'une méthode JSON‑RPC."""
    if not MCP_TYPES_DISPONIBLE:
        raise RuntimeError("mcp_types requis pour la documentation")
    try:
        return mcp_types.methods.get_metadata(method)
    except Exception as e:
        raise ErreurDocumentation(f"Échec de la documentation : {e}")


def verifier_compatibilite(constraint: str) -> bool:
    """Vérifie la compatibilité de version de mcp_types."""
    if not MCP_TYPES_DISPONIBLE:
        raise RuntimeError("mcp_types requis pour la vérification de compatibilité")
    try:
        return mcp_types.version.check_compatibility(constraint)
    except Exception as e:
        raise ErreurCompatibilite(f"Échec de la vérification de compatibilité : {e}")


def _examiner_fichier_et_dossier(fichier: Path, dossier: Path) -> Dict[str, Any]:
    """Examine un fichier Python et un répertoire, renvoie un petit rapport."""
    if not fichier.is_file():
        raise ValueError("le fichier fourni n'existe pas")
    if not dossier.is_dir():
        raise ValueError("le répertoire fourni n'existe pas")

    try:
        texte = fichier.read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"impossible de lire le fichier : {e}")

    lignes = [l for l in texte.splitlines() if l.strip()]
    if not lignes:
        raise ValueError("fichier vide")

    return {
        "nb_lignes_non_vides": len(lignes),
        "taille_octets": fichier.stat().st_size,
        "contenu_exemple": lignes[0] if lignes else "",
    }


def _examiner_texte(message: str) -> Dict[str, Any]:
    """Examine un message JSON‑RPC fourni en ligne."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError as e:
        raise ValueError(f"message JSON invalide : {e}")

    if not isinstance(data, dict):
        raise ValueError("le message doit être un objet JSON")

    # Contrat MCP minimal
    if data.get("jsonrpc") != "2.0":
        raise ValueError('champ "jsonrpc" doit être "2.0"')
    if "id" not in data:
        raise ValueError('champ "id" manquant')
    method = data.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError('champ "method" doit être une chaîne non vide')

    # Aucun réseau n'est utilisé ; on se contente de valider la forme.
    return {"message_valide": data}


def _creer_parser_parent() -> argparse.ArgumentParser:
    """Parser parent contenant les options communes."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie au format JSON (un seul objet)",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Racine du projet (défaut : répertoire du script)",
    )
    return parent


def _creer_parser() -> argparse.ArgumentParser:
    """Parser principal avec sous‑commandes."""
    parent = _creer_parser_parent()
    parser = argparse.ArgumentParser(
        description="Outil CLI pour interagir avec des services JSON-RPC 2.0",
        parents=[parent],
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    # augmenter
    aug = sub.add_parser("augmenter", help="Envoie une requête JSON-RPC", parents=[parent])
    aug.add_argument("--url", required=True, help="URL du service JSON-RPC")
    aug.add_argument("--method", required=True, help="Nom de la méthode à appeler")
    aug.add_argument("--params", required=True, help="Paramètres au format JSON")
    aug.add_argument("--timeout", type=float, help="Timeout en secondes")

    # valider
    val = sub.add_parser("valider", help="Valide les méthodes exposées", parents=[parent])
    val.add_argument("--url", required=True, help="URL du service JSON-RPC")
    val.add_argument("--timeout", type=float, help="Timeout en secondes")

    # documenter
    doc = sub.add_parser("documenter", help="Retourne la documentation d'une méthode", parents=[parent])
    doc.add_argument("--url", required=True, help="URL du service JSON-RPC")
    doc.add_argument("--method", required=True, help="Nom de la méthode à documenter")

    # compatibilite
    comp = sub.add_parser("compatibilite", help="Vérifie la compatibilité de version", parents=[parent])
    comp.add_argument("--constraint", required=True, help="Contrainte de version (ex : '>=2.0,<3.0')")

    # examiner (nouvelle sous‑commande)
    exam = sub.add_parser("examiner", help="Examine un fichier Python et/ou un message JSON‑RPC", parents=[parent])
    exam.add_argument("fichier", type=Path, nargs="?", help="Chemin vers le fichier Python à examiner")
    exam.add_argument("dossier", type=Path, nargs="?", help="Chemin vers le répertoire à examiner")
    exam.add_argument(
        "--texte",
        type=str,
        help="Message JSON-RPC en ligne à examiner (privilégie ce mode sur les chemins)",
    )

    return parser


def _fabriquer_sortie_json(
    denominateur: int,
    resultat: Any = None,
    erreur: Optional[str] = None,
    examines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Construit la sortie JSON unique contenant toujours `denominateur`."""
    sortie = {
        "denominateur": denominateur,
        "examines": examines if examines is not None else [],
    }
    if erreur is not None:
        sortie["erreur"] = erreur
    else:
        sortie["resultat"] = resultat
    return sortie


def _sortie_texte(resultat: Any) -> str:
    """Formate une sortie lisible pour le mode texte."""
    if isinstance(resultat, (dict, list)):
        return json.dumps(resultat, indent=2, ensure_ascii=False)
    return str(resultat)


def main() -> int:
    """Point d'entrée principal."""
    parser = _creer_parser()
    args = parser.parse_args()

    # Gestion de la racine
    racine = getattr(args, "racine", RACINE)
    if not isinstance(racine, Path):
        racine = Path(racine)
    if not racine.is_absolute():
        racine = RACINE / racine

    # Variables de sortie
    denominateur = 0
    resultat: Any = None
    erreur: Optional[str] = None
    examines: List[str] = []
    code_sortie = 0

    try:
        # Cas où mcp_types est absent
        if not MCP_TYPES_DISPONIBLE and args.commande in ("valider", "documenter", "compatibilite"):
            msg = "mcp_types requis, fonctionnalité désactivée"
            print(f"denominateur: {msg}", file=sys.stderr)
            erreur = "mcp_types requis"
            denominateur = 0
            code_sortie = 3

        elif args.commande == "augmenter":
            try:
                resultat = augmenter(args.url, args.method, args.params, getattr(args, "timeout", None))
                denominateur = 1
                examines = [f"requête vers {args.url}"]
            except ValueError as e:
                print("denominateur: paramètres invalides", file=sys.stderr)
                erreur = str(e)
                code_sortie = 3
                denominateur = 0
            except ErreurReseau as e:
                print("denominateur: erreur réseau", file=sys.stderr)
                erreur = str(e)
                code_sortie = 3
                denominateur = 0
            except Exception as e:
                print(f"Erreur inattendue : {e}", file=sys.stderr)
                erreur = str(e)
                code_sortie = 2
                denominateur = 0

        elif args.commande == "valider":
            try:
                resultat = valider(args.url, getattr(args, "timeout", None))
                denominateur = len(resultat)
                examines = [f"méthodes de {args.url}"]
                if denominateur == 0:
                    print("denominateur: aucune méthode valide trouvée", file=sys.stderr)
                    erreur = "aucune méthode valide"
                    code_sortie = 3
            except ErreurValidation as e:
                print("denominateur: échec de validation", file=sys.stderr)
                erreur = str(e)
                code_sortie = 3
                denominateur = 0
            except Exception as e:
                print(f"Erreur inattendue : {e}", file=sys.stderr)
                erreur = str(e)
                code_sortie = 2
                denominateur = 0

        elif args.commande == "documenter":
            try:
                resultat = documenter(args.url, args.method)
                denominateur = 1
                examines = [f"documentation de {args.method}"]
            except ErreurDocumentation as e:
                print("denominateur: échec de documentation", file=sys.stderr)
                erreur = str(e)
                code_sortie = 3
                denominateur = 0
            except Exception as e:
                print(f"Erreur inattendue : {e}", file=sys.stderr)
                erreur = str(e)
                code_sortie = 2
                denominateur = 0

        elif args.commande == "compatibilite":
            try:
                resultat = verifier_compatibilite(args.constraint)
                denominateur = 1
                examines = [f"vérification de compatibilité {args.constraint}"]
            except ErreurCompatibilite as e:
                print("denominateur: échec de compatibilité", file=sys.stderr)
                erreur = str(e)
                code_sortie = 3
                denominateur = 0
            except Exception as e:
                print(f"Erreur inattendue : {e}", file=sys.stderr)
                erreur = str(e)
                code_sortie = 2
                denominateur = 0

        elif args.commande == "examiner":
            # Priorité au mode texte
            if getattr(args, "texte", None) is not None:
                try:
                    resultat = _examiner_texte(args.texte)
                    denominateur = 1
                    examines = ["message JSON‑RPC en ligne"]
                except ValueError as e:
                    print(f"denominateur: {e}", file=sys.stderr)
                    erreur = str(e)
                    denominateur = 0
                    code_sortie = 3
            else:
                # Mode fichier + répertoire (comportement historique)
                try:
                    if args.fichier is None or args.dossier is None:
                        raise ValueError("fichier et dossier requis en l'absence de --texte")
                    resultat = _examiner_fichier_et_dossier(args.fichier, args.dossier)
                    denominateur = 1
                    examines = [
                        f"examen de {args.fichier.name}",
                        f"examen de {args.dossier.name}",
                    ]
                except ValueError as e:
                    print(f"denominateur: {e}", file=sys.stderr)
                    erreur = str(e)
                    denominateur = 0
                    code_sortie = 3
                except Exception as e:
                    print(f"Erreur inattendue : {e}", file=sys.stderr)
                    erreur = str(e)
                    code_sortie = 2
                    denominateur = 0

        else:
            print("Commande inconnue", file=sys.stderr)
            erreur = "commande inconnue"
            code_sortie = 2
            denominateur = 0

    except Exception as e:
        print(f"Erreur interne : {e}", file=sys.stderr)
        erreur = str(e)
        code_sortie = 2
        denominateur = 0

    # Sortie JSON obligatoire lorsqu'on demande --json
    if getattr(args, "json", False):
        sortie = _fabriquer_sortie_json(denominateur, resultat, erreur, examines)
        print(json.dumps(sortie, ensure_ascii=False))
    else:
        if erreur is None:
            print(_sortie_texte(resultat))
        else:
            print(f"Erreur : {erreur}", file=sys.stderr)

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())