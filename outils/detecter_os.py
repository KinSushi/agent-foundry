#!/usr/bin/env python3.14
"""
QUESTION      Sur quel système cette machine tourne-t-elle ?
MESURE        Détection du système d'exploitation, de la distribution (si applicable),
              des caractéristiques CPU et de la configuration Python.
HYPOTHÈSES    Les modules standard (platform, sysconfig, os) sont disponibles et fiables.
              Les modules tiers (distro, py-cpuinfo) améliorent la précision si présents.
LIMITES       Ne détecte pas les conteneurs ou machines virtuelles comme entités séparées.
              La détection CPU est limitée aux informations accessibles sans privilèges root.
CONTRE-EXEMPLES Sur certains systèmes BSD, la détection de la distribution peut échouer.
DOMAINE       Machines physiques et virtuelles exécutant Python 3.14+.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import sysconfig
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "detecter_systeme",
    "formater_sortie_humaine",
    "formater_sortie_json",
    "main",
]

RACINE = Path(__file__).resolve().parent

def detecter_systeme(racine: Optional[Path] = None) -> Dict[str, Any]:
    """Détecte le système, la distribution, le CPU et la configuration Python.

    Args:
        racine: Chemin racine pour surcharger sys.path (non utilisé ici).

    Returns:
        Dictionnaire contenant les informations détectées.
    """
    resultat: Dict[str, Any] = {
        "systeme": {},
        "distribution": {},
        "cpu": {},
        "python": {},
        "denominateur": 0,
        "examines": [],
        "examines_tronques": False,
    }

    # --- Système de base (platform) ---
    try:
        resultat["systeme"]["nom"] = platform.system()
        resultat["systeme"]["version"] = platform.release()
        resultat["systeme"]["machine"] = platform.machine()
        resultat["systeme"]["nœud"] = platform.node()
        resultat["systeme"]["version_complete"] = platform.version()
        resultat["denominateur"] += 4
        resultat["examines"].extend(["systeme.nom", "systeme.version", "systeme.machine", "systeme.nœud"])
    except Exception as e:
        print(f"Erreur lors de la détection système: {e}", file=sys.stderr)

    # --- Distribution (distro si disponible, sinon platform) ---
    try:
        import distro
        distro_info = distro.info()
        resultat["distribution"] = {
            "id": distro_info.get("id", ""),
            "nom": distro_info.get("name", ""),
            "version": distro_info.get("version", ""),
            "version_majeure": distro_info.get("version_parts", {}).get("major", ""),
            "version_mineure": distro_info.get("version_parts", {}).get("minor", ""),
            "comme": distro_info.get("like", ""),
            "codename": distro_info.get("codename", ""),
        }
        resultat["denominateur"] += 7
        resultat["examines"].extend([
            "distribution.id", "distribution.nom", "distribution.version",
            "distribution.version_majeure", "distribution.version_mineure",
            "distribution.comme", "distribution.codename"
        ])
    except ImportError:
        print("Module 'distro' non disponible, détection de distribution en mode dégradé.", file=sys.stderr)
        try:
            if platform.system() == "Linux":
                os_release = platform.freedesktop_os_release()
                resultat["distribution"] = {
                    "id": os_release.get("ID", ""),
                    "nom": os_release.get("NAME", ""),
                    "version": os_release.get("VERSION_ID", ""),
                    "comme": os_release.get("ID_LIKE", ""),
                    "codename": os_release.get("VERSION_CODENAME", ""),
                }
                resultat["denominateur"] += 5
                resultat["examines"].extend([
                    "distribution.id", "distribution.nom", "distribution.version",
                    "distribution.comme", "distribution.codename"
                ])
        except Exception as e:
            print(f"Erreur lors de la détection de distribution (Linux): {e}", file=sys.stderr)
    except Exception as e:
        print(f"Erreur lors de la détection de distribution (distro): {e}", file=sys.stderr)

    # --- CPU (py-cpuinfo si disponible, sinon platform) ---
    try:
        import cpuinfo
        cpu_info = cpuinfo.get_cpu_info()
        resultat["cpu"] = {
            "marque": cpu_info.get("brand_raw", ""),
            "architecture": cpu_info.get("arch", ""),
            "bits": cpu_info.get("bits", ""),
            "fréquence": cpu_info.get("hz_actual_friendly", ""),
            "cœurs": cpu_info.get("count", ""),
            "drapeaux": cpu_info.get("flags", []),
        }
        resultat["denominateur"] += 6
        resultat["examines"].extend([
            "cpu.marque", "cpu.architecture", "cpu.bits",
            "cpu.fréquence", "cpu.cœurs", "cpu.drapeaux"
        ])
    except ImportError:
        print("Module 'py-cpuinfo' non disponible, détection CPU en mode dégradé.", file=sys.stderr)
        try:
            resultat["cpu"] = {
                "processeur": platform.processor(),
                "architecture": platform.architecture()[0],
                "machine": platform.machine(),
            }
            resultat["denominateur"] += 3
            resultat["examines"].extend(["cpu.processeur", "cpu.architecture", "cpu.machine"])
        except Exception as e:
            print(f"Erreur lors de la détection CPU (platform): {e}", file=sys.stderr)
    except Exception as e:
        print(f"Erreur lors de la détection CPU (py-cpuinfo): {e}", file=sys.stderr)

    # --- Configuration Python ---
    try:
        resultat["python"] = {
            "version": platform.python_version(),
            "version_tuple": platform.python_version_tuple(),
            "implementation": platform.python_implementation(),
            "compilateur": platform.python_compiler(),
            "branche": platform.python_branch(),
            "revision": platform.python_revision(),
            "construction": platform.python_build(),
            "plateforme": sysconfig.get_platform(),
        }
        resultat["denominateur"] += 8
        resultat["examines"].extend([
            "python.version", "python.version_tuple", "python.implementation",
            "python.compilateur", "python.branche", "python.revision",
            "python.construction", "python.plateforme"
        ])
    except Exception as e:
        print(f"Erreur lors de la détection Python: {e}", file=sys.stderr)

    # Tronquer la liste des éléments examinés si nécessaire
    if len(resultat["examines"]) > 200:
        resultat["examines"] = resultat["examines"][:200]
        resultat["examines_tronques"] = True

    return resultat

def formater_sortie_humaine(resultat: Dict[str, Any]) -> str:
    """Formate les résultats pour une sortie lisible par un humain."""
    lignes = []

    # Système
    lignes.append("=== Système ===")
    lignes.append(f"Nom: {resultat['systeme'].get('nom', 'Inconnu')}")
    lignes.append(f"Version: {resultat['systeme'].get('version', 'Inconnu')}")
    lignes.append(f"Machine: {resultat['systeme'].get('machine', 'Inconnu')}")
    lignes.append(f"Nœud: {resultat['systeme'].get('nœud', 'Inconnu')}")
    lignes.append(f"Version complète: {resultat['systeme'].get('version_complete', 'Inconnu')}")

    # Distribution
    lignes.append("\n=== Distribution ===")
    if resultat["distribution"]:
        lignes.append(f"ID: {resultat['distribution'].get('id', 'Inconnu')}")
        lignes.append(f"Nom: {resultat['distribution'].get('nom', 'Inconnu')}")
        lignes.append(f"Version: {resultat['distribution'].get('version', 'Inconnu')}")
        lignes.append(f"Version majeure: {resultat['distribution'].get('version_majeure', 'Inconnu')}")
        lignes.append(f"Version mineure: {resultat['distribution'].get('version_mineure', 'Inconnu')}")
        lignes.append(f"Comme: {resultat['distribution'].get('comme', 'Inconnu')}")
        lignes.append(f"Codename: {resultat['distribution'].get('codename', 'Inconnu')}")
    else:
        lignes.append("Aucune information de distribution disponible.")

    # CPU
    lignes.append("\n=== CPU ===")
    if resultat["cpu"]:
        lignes.append(f"Marque: {resultat['cpu'].get('marque', 'Inconnu')}")
        lignes.append(f"Architecture: {resultat['cpu'].get('architecture', 'Inconnu')}")
        lignes.append(f"Bits: {resultat['cpu'].get('bits', 'Inconnu')}")
        lignes.append(f"Fréquence: {resultat['cpu'].get('fréquence', 'Inconnu')}")
        lignes.append(f"Cœurs: {resultat['cpu'].get('cœurs', 'Inconnu')}")
        if "drapeaux" in resultat["cpu"]:
            lignes.append(f"Drapeaux: {', '.join(resultat['cpu']['drapeaux'][:10])}{'...' if len(resultat['cpu']['drapeaux']) > 10 else ''}")
    else:
        lignes.append("Aucune information CPU disponible.")

    # Python
    lignes.append("\n=== Python ===")
    lignes.append(f"Version: {resultat['python'].get('version', 'Inconnu')}")
    lignes.append(f"Version (tuple): {'.'.join(resultat['python'].get('version_tuple', ('Inconnu', 'Inconnu', 'Inconnu')))}")
    lignes.append(f"Implementation: {resultat['python'].get('implementation', 'Inconnu')}")
    lignes.append(f"Compilateur: {resultat['python'].get('compilateur', 'Inconnu')}")
    lignes.append(f"Branche: {resultat['python'].get('branche', 'Inconnu')}")
    lignes.append(f"Révision: {resultat['python'].get('revision', 'Inconnu')}")
    lignes.append(f"Construction: {', '.join(resultat['python'].get('construction', ('Inconnu', 'Inconnu')))}")
    lignes.append(f"Plateforme: {resultat['python'].get('plateforme', 'Inconnu')}")

    # Statistiques
    lignes.append("\n=== Statistiques ===")
    lignes.append(f"Éléments examinés: {resultat['denominateur']}")
    if resultat["examines_tronques"]:
        lignes.append(f"Liste des éléments examinés tronquée à 200 entrées.")

    return "\n".join(lignes)

def formater_sortie_json(resultat: Dict[str, Any]) -> str:
    """Formate les résultats en JSON."""
    return json.dumps(resultat, indent=2, ensure_ascii=False)

def analyser_arguments() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Détecte le système d'exploitation, la distribution, le CPU et la configuration Python.",
        epilog="Exemple: python detecter_os.py --json"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Affiche le résultat au format JSON."
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Chemin racine pour surcharger sys.path (non utilisé ici)."
    )
    return parser.parse_args()

def main() -> int:
    """Point d'entrée principal."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = analyser_arguments()

    if args.racine and args.racine.exists():
        sys.path.insert(0, str(args.racine))

    resultat = detecter_systeme(args.racine)

    if args.json:
        print(formater_sortie_json(resultat))
        return 0

    if resultat["denominateur"] == 0:
        print("Aucun élément n'a pu être examiné. Refus de conclure.", file=sys.stderr)
        return 3

    print(formater_sortie_humaine(resultat))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())