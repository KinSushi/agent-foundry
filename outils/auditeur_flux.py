"""QUESTION
Quels flux réseau sont actifs ?
MESURE
Analyse des fichiers /proc/net/tcp et /proc/net/tcp6 pour extraire les connexions TCP.
HYPOTHESES
Le système d'exploitation expose les informations réseau via /proc/net.
LIMITES
Ne fonctionne que sur les systèmes Linux disposant de /proc/net ; ne détecte pas les flux HTTP/2/TLS non établis.
CONTRE-EXEMPLES
Sur Windows, macOS ou tout système sans /proc, l'outil indique l'impossibilité d'analyse.
INVOCATION
    {outil} --json
DOMAINE
Inspection passive des flux réseau actifs au niveau du noyau."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

# ----------------------------------------------------------------------
# Réglage de l'encodage de la console (règle 2)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
# ----------------------------------------------------------------------

def _construire_sortie_json(denominateur: int, examines: List[Dict[str, Any]], contrat: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """Construit l'objet JSON de sortie avec toutes les clés obligatoires."""
    base = {
        "denominateur": denominateur,
        "examines": examines,
        "contrat": contrat,
    }
    base.update(extra)
    return base

class JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui, en mode JSON, renvoie les erreurs sous forme d'un
    seul objet JSON sur stdout au lieu d'un message d'usage sur stderr."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_mode: bool = False

    def error(self, message: str) -> None:
        if self._json_mode:
            sortie = _construire_sortie_json(
                denominateur=0,
                examines=[],
                contrat={
                    "question": "Quels flux réseau sont actifs ?",
                    "mesure": "Analyse des fichiers /proc/net/tcp et /proc/net/tcp6",
                    "hypotheses": "Le système expose les informations via /proc/net",
                    "limites": "Ne fonctionne que sur Linux avec /proc/net",
                    "contre_exemples": "Windows, macOS ou systèmes sans /proc",
                    "domaine": "Inspection passive des flux réseau actifs",
                },
                error=message
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.flush()
            sys.exit(2)
        else:
            super().error(message)

def _lire_fichier_proc(chemin: Path) -> List[Dict[str, Any]]:
    """Lit un fichier /proc/net/tcp* et renvoie la liste des flux décodés.

    Aucun diagnostic n'est émis ; les erreurs d'accès sont silencieuses pour
    respecter la séparation cœur/CLI.
    """
    flux: List[Dict[str, Any]] = []
    try:
        lignes = chemin.read_text(encoding="utf-8").splitlines()
    except OSError:
        return flux

    for ligne in lignes[1:]:
        parties = ligne.split()
        if len(parties) < 10:
            continue
        local_hex, distant_hex, etat_hex = parties[1], parties[2], parties[3]

        def _hex_ip_port(val: str) -> str:
            ip_hex, port_hex = val.split(":")
            if len(ip_hex) == 8:  # IPv4 little‑endian
                ip = ".".join(str(int(ip_hex[i:i + 2], 16)) for i in range(6, -2, -2))
            else:  # IPv6 little‑endian
                groupes = [ip_hex[i:i + 4] for i in range(0, 32, 4)]
                groupes = [g[::-1] for g in groupes]
                ip = ":".join(groupes)
            port = str(int(port_hex, 16))
            return f"{ip}:{port}"

        flux.append(
            {
                "local": _hex_ip_port(local_hex),
                "distant": _hex_ip_port(distant_hex),
                "etat": etat_hex,
            }
        )
    return flux

def analyser_flux() -> Dict[str, Any]:
    """Analyse les flux réseau actifs.

    Retourne un dictionnaire contenant :
        - denominateur : nombre total de flux réellement examinés,
        - examines    : liste (max 200) des flux décodés,
        - examines_tronques : booléen indiquant si la liste a été tronquée.
    Si le répertoire /proc/net est absent, le dénominateur vaut 0.
    """
    proc_dir = Path("/proc/net")
    if not proc_dir.is_dir():
        return {
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False,
            "contrat": {
                "question": "Quels flux réseau sont actifs ?",
                "mesure": "Analyse des fichiers /proc/net/tcp et /proc/net/tcp6",
                "hypotheses": "Le système expose les informations via /proc/net",
                "limites": "Ne fonctionne que sur Linux avec /proc/net",
                "contre_exemples": "Windows, macOS ou systèmes sans /proc",
                "domaine": "Inspection passive des flux réseau actifs",
            }
        }

    tous_flux: List[Dict[str, Any]] = []
    for nom in ("tcp", "tcp6"):
        tous_flux.extend(_lire_fichier_proc(proc_dir / nom))

    denominateur = len(tous_flux)
    tronque = denominateur > 200
    examines = tous_flux[:200]

    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": tronque,
        "contrat": {
            "question": "Quels flux réseau sont actifs ?",
            "mesure": "Analyse des fichiers /proc/net/tcp et /proc/net/tcp6",
            "hypotheses": "Le système expose les informations via /proc/net",
            "limites": "Ne fonctionne que sur Linux avec /proc/net",
            "contre_exemples": "Windows, macOS ou systèmes sans /proc",
            "domaine": "Inspection passive des flux réseau actifs",
        }
    }

def _afficher_humain(résultat: Dict[str, Any]) -> None:
    """Affiche le résultat de façon lisible pour un humain."""
    denom = résultat.get("denominateur", 0)
    if denom == 0:
        print("Aucun flux réseau actif détecté.")
        return

    print(f"{denom} flux réseau actif(s) détecté(s) :")
    for i, flux in enumerate(résultat["examines"], 1):
        print(
            f"{i}. Local : {flux['local']} → Distant : {flux['distant']} (état : {flux['etat']})"
        )
    if résultat.get("examines_tronques"):
        print("… (liste tronquée à 200 éléments)")

def _afficher_json(résultat: Dict[str, Any]) -> None:
    """Émet le résultat au format JSON conforme à la règle 4."""
    json.dump(résultat, sys.stdout, ensure_ascii=False, indent=2)

def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = JSONArgumentParser(
        description="Auditeur de flux réseau actifs.",
        epilog="Exemple d’appel réel : python auditeur_flux.py --json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à préfixer dans sys.path (par défaut le répertoire du script).",
    )
    # Indiquer au parser si le mode JSON est demandé afin que les erreurs
    # soient renvoyées sous forme JSON sur stdout.
    parser._json_mode = "--json" in argv

    args = parser.parse_args(argv)

    # Gestion du paramètre --racine (règle 6)
    racine = args.racine.resolve()
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    résultat = analyser_flux()

    if résultat["denominateur"] == 0:
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            _afficher_json(résultat)
        return 3

    if args.json:
        _afficher_json(résultat)
    else:
        _afficher_humain(résultat)

    return 0

__all__ = ["analyser_flux"]

if __name__ == "__main__":
    raise SystemExit(main())