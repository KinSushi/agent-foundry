"""QUESTION
Comment obtenir un jeton d’accès pour ce service ?
MESURE
Utiliser OAuth2 avec MSAL ou, à défaut, indiquer l’impossibilité.
HYPOTHESES
Le client fournit client_id, authority, scopes et éventuellement redirect_uri.
LIMITES
Sans bibliothèque tierce (msal) le flux complet n’est pas implémenté.
CONTRE-EXEMPLES
Un appel sans client_id ou authority ne peut aboutir.
INVOCATION
    {outil} --client-id test --authority https://login.microsoftonline.com/common --scope User.Read --json
DOMAINE
Authentification OAuth2 pour services Microsoft.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# 2. Gestion de l’encodage de la console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__: List[str] = ["authentifier", "obtenir_contrat"]

def _charger_msal() -> Any:
    """Essayer d’importer msal, sinon retourner None."""
    try:
        import msal  # type: ignore
        return msal
    except ImportError:
        return None

def authentifier(
    client_id: str,
    authority: str,
    scopes: List[str],
    redirect_uri: Optional[str] = None,
    cache_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Retourne un dictionnaire contenant le jeton d’accès ou une description d’erreur.
    Le champ « code » indique le statut : 0 = succès, >0 = problème.
    """
    msal = _charger_msal()
    if msal is None:
        return {
            "erreur": "msal non installé ; flux OAuth complet indisponible.",
            "code": 1,
        }

    # Gestion du cache si demandé
    token_cache = None
    if cache_path is not None:
        try:
            from msal_extensions import FileCache  # type: ignore
            token_cache = FileCache(str(cache_path))
        except Exception as exc:  # pragma: no cover
            sys.stderr.write(f"Attention : impossible d’utiliser le cache : {exc}\n")

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=authority,
        token_cache=token_cache,
    )

    # Tentative d’acquisition silencieuse
    result = app.acquire_token_silent(scopes, account=None)
    if not result:
        # Flux device – le plus simple sans serveur web
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            return {"erreur": "Échec de l’initiation du flux device.", "code": 2}
        sys.stderr.write(flow["message"] + "\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" in result:
        return {"access_token": result["access_token"], "code": 0}
    return {"erreur": result.get("error_description", "Erreur inconnue"), "code": 3}

def obtenir_contrat() -> Dict[str, str]:
    """Extrait les sept sections du docstring pour le champ « contrat » du JSON."""
    sections = [
        "QUESTION",
        "MESURE",
        "HYPOTHESES",
        "LIMITES",
        "CONTRE-EXEMPLES",
        "INVOCATION",
        "DOMAINE",
    ]
    doc = __doc__ or ""
    contrat: Dict[str, str] = {}
    for sec in sections:
        start = doc.find(sec)
        if start == -1:
            contrat[sec.lower()] = ""
            continue
        # Position du texte après le titre
        after = start + len(sec)
        # Recherche du prochain titre
        next_pos = len(doc)
        for other in sections:
            if other == sec:
                continue
            pos = doc.find(other, after)
            if pos != -1 and pos < next_pos:
                next_pos = pos
        contenu = doc[after:next_pos].strip()
        contrat[sec.lower()] = contenu
    return contrat

def _afficher_resultat(
    resultat: Dict[str, Any],
    args: argparse.Namespace,
    examines: List[str],
) -> int:
    """Affiche le résultat selon le mode choisi et renvoie le code de sortie."""
    if args.json:
        sortie = {
            "contrat": obtenir_contrat(),
            "denominateur": len(examines),
            "examines": examines[:200],
            "examines_tronques": len(examines) > 200,
            "resultat": resultat,
        }
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return resultat.get("code", 1)
    # Mode texte humain
    if "access_token" in resultat:
        sys.stdout.write(f"Jeton d’accès : {resultat['access_token']}\n")
        return 0
    sys.stderr.write(f"Erreur : {resultat.get('erreur', 'Inconnue')}\n")
    return resultat.get("code", 1)

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Obtenir un jeton d’accès OAuth2 via MSAL.",
        epilog=(
            "Exemple : python authentifier_oauth.py "
            "--client-id <id> --authority https://login.microsoftonline.com/common "
            "--scope User.Read"
        ),
    )
    parser.add_argument("--client-id", help="Identifiant du client (application).")
    parser.add_argument(
        "--authority",
        help="URL de l’autorité (ex. https://login.microsoftonline.com/common).",
    )
    parser.add_argument(
        "--scope",
        action="append",
        help="Scope à demander (peut être répété).",
    )
    parser.add_argument("--redirect-uri", help="URI de redirection (facultatif).")
    parser.add_argument("--cache", type=Path, help="Chemin du fichier de cache token (facultatif).")
    parser.add_argument(
        "--racine",
        type=Path,
        help="Chemin racine à préfixer dans sys.path (remplace le répertoire du script).",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON unique.")
    args = parser.parse_args(argv)

    # 6. Gestion du chemin racine
    racine = args.racine or Path(__file__).resolve().parent
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    # Validation et collecte des éléments examinés
    examines: List[str] = []
    missing: List[str] = []
    if args.client_id:
        examines.append("client_id")
    else:
        missing.append("--client-id")
    if args.authority:
        examines.append("authority")
    else:
        missing.append("--authority")
    if args.scope and len(args.scope) > 0:
        examines.append("scopes")
    else:
        missing.append("--scope")

    # Si aucun élément n’a été effectivement examiné, on refuse de conclure
    if len(examines) == 0:
        if args.json:
            sortie = {
                "contrat": obtenir_contrat(),
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "resultat": {"erreur": "Aucun élément à examiner.", "code": 3},
            }
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        sys.stderr.write("Denominateur nul : aucun élément examiné.\n")
        return 3

    # Gestion des erreurs de validation (arguments manquants mais partiels)
    if missing:
        if args.json:
            code_map = {"--client-id": 4, "--authority": 5, "--scope": 6}
            code = code_map.get(missing[0], 1)
            erreur_msg = f"Erreur : {missing[0]} est requis."
            resultat = {"erreur": erreur_msg, "code": code}
            sortie = {
                "contrat": obtenir_contrat(),
                "denominateur": len(examines),
                "examines": examines[:200],
                "examines_tronques": len(examines) > 200,
                "resultat": resultat,
            }
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return code
        else:
            for m in missing:
                sys.stderr.write(f"Erreur : {m} est requis.\n")
            code_map = {"--client-id": 4, "--authority": 5, "--scope": 6}
            return code_map.get(missing[0], 1)

    # Exécution normale
    try:
        resultat = authentifier(
            client_id=args.client_id,
            authority=args.authority,
            scopes=args.scope,
            redirect_uri=args.redirect_uri,
            cache_path=args.cache,
        )
    except Exception as exc:
        resultat = {"erreur": str(exc), "code": 1}
    return _afficher_resultat(resultat, args, examines)

if __name__ == "__main__":
    raise SystemExit(main())