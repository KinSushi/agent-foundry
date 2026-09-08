#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resoudre_noms.py

QUESTION      Quelle est l'adresse réelle de ce nom, et par quel chemin DNS ?
MESURE        Résolution DNS via socket.getaddrinfo (standard) et, si disponible,
              la bibliothèque dnspython pour obtenir les serveurs interrogés.
              L'encodage IDNA est effectué avec le module idna lorsqu'il est présent.
HYPOTHÈSES    Le nom fourni est un domaine valide (ou une adresse IP littérale).
              Le résolveur système fonctionne correctement.
LIMITES       Aucun enregistrement MX, TXT, DNSSEC ou autre type que A/AAAA n'est
              interrogé. Sans dnspython, le chemin DNS exact (serveurs interrogés)
              ne peut être connu.
CONTRE-EXEMPLES Un nom qui ne possède que des enregistrements MX (pas d'A/AAAA)
              sera considéré comme non résolu alors qu'il est valide pour la messagerie.
DOMAINE       Résolution de noms d'hôtes pour des services TCP/IP (HTTP, SSH, etc.)
              où seules les adresses A/AAAA sont nécessaires.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---- Encodage stdout/stderr (règle 2) ----
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ---- Imports optionnels (règle 1) ----
try:
    import dns.resolver  # type: ignore
    _HAVE_DNSPYTHON = True
except Exception:  # ImportError ou autre
    dns = None  # type: ignore
    _HAVE_DNSPYTHON = False

try:
    import idna  # type: ignore
    _HAVE_IDNA = True
except Exception:
    idna = None  # type: ignore
    _HAVE_IDNA = False

# ---- Racine du projet (règle 6) ----
RACINE = Path(__file__).resolve().parent

# ---- Codes de sortie (règle 5) ----
_CODE_SUCCES = 0          # rien à signaler
_CODE_RESULTAT = 1        # résultat trouvé (adresses obtenues)
_CODE_ERREUR_USAGE = 2    # mauvais usage
_CODE_REFUS_CONCLUSION = 3  # dénominateur nul

# ---- Interface publique (règle 8) ----
__all__ = [
    "resoudre_un_nom",
    "resoudre_noms",
    "main",
]

class _ToolError(RuntimeError):
    """Exception interne pour signaler une erreur d'usage avec code de sortie."""
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _encoder_idna(nom: str) -> str:
    """Encode un nom en ASCII avec IDNA si le module est disponible."""
    if _HAVE_IDNA and idna is not None:
        try:
            return idna.encode(nom).decode("ascii")
        except Exception:
            return nom
    return nom


def _resoudre_avec_dnspython(nom_ascii: str) -> Tuple[List[str], Optional[List[str]], str]:
    """Résolution via dnspython (si disponible)."""
    if not _HAVE_DNSPYTHON or dns is None:
        return [], None, "indisponible"

    resolver = dns.resolver.Resolver()
    adresses: List[str] = []
    try:
        for rr in resolver.resolve(nom_ascii, "A"):
            adresses.append(rr.address)
    except Exception:
        pass
    try:
        for rr in resolver.resolve(nom_ascii, "AAAA"):
            adresses.append(rr.address)
    except Exception:
        pass

    serveurs = getattr(resolver, "nameservers", None)
    if serveurs is not None:
        serveurs = [str(s) for s in serveurs]
    return adresses, serveurs, "dnspython"


def _resoudre_avec_socket(nom_ascii: str) -> Tuple[List[str], None, str]:
    """Résolution via socket.getaddrinfo (méthode système)."""
    adresses: List[str] = []
    try:
        infos = socket.getaddrinfo(
            nom_ascii,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=0,
            flags=0,
        )
        vus = set()
        for _fam, _type, _proto, _canon, sockaddr in infos:
            ip = sockaddr[0]
            if ip not in vus:
                vus.add(ip)
                adresses.append(ip)
    except Exception:
        pass
    return adresses, None, "socket.getaddrinfo"


def _verifier_cible(nom: str) -> None:
    """
    Vérifie les contraintes R1, R2, R3 sur le « cible » lorsqu'il s'agit d'un chemin.
    - R1 : cible inexistante → erreur claire.
    - R2 : mauvais type (dossier attendu fichier ou inverse) → erreur claire.
    - R3 : fichier illisible ou binaire .py → erreur claire.
    """
    est_chemin = os.sep in nom or nom.endswith(".py")
    if not est_chemin:
        return

    p = Path(nom)
    if not p.exists():
        raise _ToolError(
            f"Erreur : la cible '{nom}' n'existe pas.",
            _CODE_ERREUR_USAGE,
        )
    if p.is_dir():
        raise _ToolError(
            f"Erreur : la cible '{nom}' est un répertoire alors qu'un fichier était attendu.",
            _CODE_ERREUR_USAGE,
        )
    try:
        with p.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read(1024)  # lecture partielle
        if "\ufffd" in contenu:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "caractère de remplacement")
    except (OSError, UnicodeDecodeError) as exc:
        raise _ToolError(
            f"Erreur : le fichier '{nom}' est illisible ({exc}).",
            _CODE_ERREUR_USAGE,
        ) from exc


def resoudre_un_nom(nom: str, online: bool) -> Dict[str, Any]:
    """Résout un seul nom (ou cible) en fonction du mode réseau."""
    _verifier_cible(nom)

    if not online:
        return {
            "nom": nom,
            "adresses": [],
            "chemin_dns": None,
            "methode": "offline",
        }

    nom_ascii = _encoder_idna(nom)

    adresses, chemin_dns, methode = _resoudre_avec_dnspython(nom_ascii)
    if methode == "indisponible":
        adresses, chemin_dns, methode = _resoudre_avec_socket(nom_ascii)

    return {
        "nom": nom,
        "adresses": adresses,
        "chemin_dns": chemin_dns,
        "methode": methode,
    }


def resoudre_noms(noms: List[str], online: bool) -> Tuple[int, List[str], List[Dict[str, Any]]]:
    """Résout une liste de noms en respectant le mode réseau."""
    examines = noms[:]
    resultats = [resoudre_un_nom(n, online) for n in examines]
    return len(examines), examines, resultats


def _afficher_humain(resultats: List[Dict[str, Any]], offline: bool) -> None:
    """Affichage lisible par un humain sur stdout."""
    if offline:
        print("Mode hors ligne : aucune résolution réseau effectuée.", file=sys.stdout)
    for r in resultats:
        print(f"Nom : {r['nom']}")
        if r["adresses"]:
            print("  Adresses : " + ", ".join(r["adresses"]))
        else:
            print("  Adresses : (aucune)")
        if r["chemin_dns"] is not None:
            print("  Chemin DNS : " + ", ".join(r["chemin_dns"]))
        else:
            print("  Chemin DNS : non disponible (résolution système)")
        print(f"  Méthode utilisée : {r['methode']}")
        print()


def _afficher_json(denominateur: int, examines: List[str],
                   resultats: List[Dict[str, Any]], offline: bool) -> None:
    """Affichage JSON conforme aux règles 4."""
    examines_tronques = len(examines) > 200
    examines_limite = examines[:200]

    doc = __doc__ or ""
    sections = ["QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
                "CONTRE-EXEMPLES", "DOMAINE"]
    contrat: Dict[str, str] = {}
    for sec in sections:
        pattern = rf'^{sec}\s+(.*?)(?=^\w|\Z)'
        match = re.search(pattern, doc, re.MULTILINE | re.DOTALL)
        contrat[sec] = match.group(1).strip() if match else ""

    sortie = {
        "denominateur": denominateur,
        "examines": examines_limite,
        "examines_tronques": examines_tronques,
        "mode_offline": offline,
        "contrat": contrat,
        "resultats": resultats,
    }
    json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _echec_usage(message: str) -> int:
    """Affiche un message d'erreur sur stderr et retourne le code d'usage."""
    print(message, file=sys.stderr)
    return _CODE_ERREUR_USAGE


def main(argv: Optional[List[str]] = None) -> int:
    """Point d'entrée de l'outil."""
    parser = argparse.ArgumentParser(
        description="Résout un nom d'hôte en adresse IP et indique le chemin DNS utilisé.",
        epilog="Exemple : %(prog)s example.com",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "noms",
        nargs="+",
        help="Nom(s) de domaine ou chemin de fichier à analyser (exemple : example.com)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON au lieu du format lisible",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Surcharge la racine du projet et l'ajoute au sys.path",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Autorise les accès réseau (désactivé par défaut)",
    )

    args = parser.parse_args(argv)

    if args.racine is not None:
        racine_resolue = args.racine.resolve()
        if racine_resolue.is_dir():
            sys.path.insert(0, str(racine_resolue))
        else:
            return _echec_usage(
                f"Erreur : le répertoire --racine '{args.racine}' n'existe pas ou n'est pas un dossier."
            )

    if not args.noms:
        return _echec_usage("Erreur : aucun nom fourni.")

    try:
        denominateur, examines, resultats = resoudre_noms(args.noms, args.online)
    except _ToolError as te:
        print(te.message, file=sys.stderr)
        return te.code

    if denominateur == 0:
        print(
            "Erreur : aucun élément examiné, impossible de conclure.",
            file=sys.stderr,
        )
        return _CODE_REFUS_CONCLUSION

    if args.json:
        _afficher_json(denominateur, examines, resultats, not args.online)
        return _CODE_RESULTAT if any(r["adresses"] for r in resultats) else _CODE_SUCCES
    else:
        _afficher_humain(resultats, not args.online)
        return _CODE_RESULTAT if any(r["adresses"] for r in resultats) else _CODE_SUCCES


if __name__ == "__main__":
    raise SystemExit(main())