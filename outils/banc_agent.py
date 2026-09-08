"""Banc d'essai pour mesurer le coût réel d'un agent.

QUESTION       qu'est-ce que ce travail a réellement coûté ?
MESURE         psutil sur le processus ET sa descendance, échantillonné
               PENDANT l'exécution ; charge machine par cpu_percent ;
               jetons par tiktoken en conteneur quand l'exactitude est requise
HYPOTHÈSES    les processus enfants sont atteignables ; l'échantillonnage
               capte les processus courts
LIMITES        resource n'existe pas sous Windows ; un processus terminé avant
               l'échantillon est invisible ; le coût d'un appel réseau
               (modèle distant) n'est pas mesurable localement
CONTRE-EXEMPLES mesurer le parent seul rend 0 ms de CPU alors que l'enfant a
               travaillé 192 ms — mesuré, et le zéro passe pour un résultat
DOMAINE        processus locaux, Windows, cette machine
"""

from __future__ import annotations

import sys
import os
import time
import json
import subprocess
import argparse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import resource
except ModuleNotFoundError:
    resource = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import cpuinfo
except ImportError:
    cpuinfo = None

try:
    import tiktoken
except ImportError:
    tiktoken = None

RACINE = Path(__file__).resolve().parent

# Coût nominal annoncé dans le plan pour tiktoken en conteneur (mesuré à 728 ms).
COUT_NOMINAL_CONTENEUR_MS = 728.0

CONTRAT = {
    "QUESTION": "qu'est-ce que ce travail a réellement coûté ?",
    "MESURE": "psutil sur le processus ET sa descendance, échantillonné PENDANT l'exécution ; charge machine par cpu_percent ; jetons par tiktoken en conteneur quand l'exactitude est requise",
    "HYPOTHÈSES": "les processus enfants sont atteignables ; l'échantillonnage capte les processus courts",
    "LIMITES": "resource n'existe pas sous Windows ; un processus terminé avant l'échantillon est invisible ; le coût d'un appel réseau (modèle distant) n'est pas mesurable localement",
    "CONTRE-EXEMPLE": "mesurer le parent seul rend 0 ms de CPU alors que l'enfant a travaillé 192 ms — mesuré, et le zéro passe pour un résultat",
    "DOMAINE": "processus locaux, Windows, cette machine",
}

__all__ = [
    "verifier_source_python",
    "obtenir_info_machine",
    "mesurer_commande",
    "mesurer_session",
    "compter_jetons",
    "main",
]

def verifier_source_python(source: str, nom: str) -> bool:
    """Vérifie la validité d'un source Python via compile()."""
    try:
        compile(source, nom, "exec")
        return True
    except SyntaxError:
        return False


def obtenir_info_machine() -> dict:
    """Retourne les informations sur le CPU de la machine."""
    if cpuinfo:
        try:
            info = cpuinfo.get_cpu_info()
            return {
                "nom": info.get("brand_raw", "Inconnu"),
                "coeurs": info.get("count", 0),
            }
        except Exception:
            pass
    return {"nom": "Inconnu", "coeurs": 0}


def _construire_examines(commande: list[str]) -> list[str]:
    """
    Construit la liste des éléments réellement examinés pour la sous‑commande.
    Ici, on considère que la commande elle‑même (son premier token) est l'élément.
    """
    if not commande:
        return []
    # On ne garde que le premier token (exécutable) comme nom d'élément.
    return [commande[0]]


def mesurer_commande(commande: list[str]) -> dict:
    """Mesure le coût complet d'une commande en échantillonnant le processus et sa descendance."""
    # Validation du script Python éventuel
    if len(commande) >= 2 and commande[0].endswith("python"):
        fichier = commande[1]
        chemin = Path(fichier)
        if chemin.is_file() and fichier.endswith(".py"):
            try:
                source = chemin.read_text(encoding="utf-8")
                if not verifier_source_python(source, fichier):
                    return {
                        "commande": " ".join(commande),
                        "etat": "ERREUR_SOURCE",
                        "raison": "Syntaxe Python invalide",
                        "echantillons": 0,
                        "denominateur": 0,
                        "examines": [],
                        "examines_tronques": 0,
                    }
            except Exception:
                pass

    # Cas où psutil n'est pas disponible
    if psutil is None:
        debut = time.perf_counter()
        subprocess.run(commande, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fin = time.perf_counter()
        return {
            "commande": " ".join(commande),
            "temps_mur_ms": round((fin - debut) * 1000, 1),
            "cpu_total_ms": 0.0,
            "cpu_user_ms": 0.0,
            "cpu_system_ms": 0.0,
            "memoire_crete_mo": 0.0,
            "io_lu_mo": 0.0,
            "io_ecrit_mo": 0.0,
            "processus": 1,
            "charge_machine": 0.0,
            "machine": obtenir_info_machine(),
            "etat": "DEGRADE",
            "raison": "psutil indisponible, mesure limitée au temps mur",
            "echantillons": 0,
            "denominateur": 0,
            "examines": _construire_examines(commande),
            "examines_tronques": 0,
        }

    debut = time.perf_counter()
    proc = subprocess.Popen(commande, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = psutil.Process(proc.pid)

    echantillons = 0
    memoire_crete = 0
    cpu_user = 0.0
    cpu_system = 0.0
    io_lu = 0
    io_ecrit = 0
    nb_processus = 1
    charges = []

    while proc.poll() is None:
        try:
            charge = psutil.cpu_percent(interval=None)
            charges.append(charge)

            enfants = p.children(recursive=True)
            procs = [p] + enfants
            nb_processus = len(procs)

            mem_tot = 0
            user_tot = 0.0
            sys_tot = 0.0
            lu_tot = 0
            ecrit_tot = 0

            for pr in procs:
                try:
                    mem_tot += pr.memory_info().rss
                    ct = pr.cpu_times()
                    user_tot += ct.user
                    sys_tot += ct.system
                    io = pr.io_counters()
                    lu_tot += io.read_bytes
                    ecrit_tot += io.write_bytes
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if mem_tot > memoire_crete:
                memoire_crete = mem_tot
            cpu_user = user_tot
            cpu_system = sys_tot
            io_lu = lu_tot
            io_ecrit = ecrit_tot
            echantillons += 1
            time.sleep(0.01)
        except psutil.NoSuchProcess:
            break

    fin = time.perf_counter()
    temps_mur = (fin - debut) * 1000
    charge_moyenne = round(sum(charges) / len(charges), 1) if charges else 0.0

    etat = "OK"
    if echantillons == 0:
        etat = "NON_MESURE"

    examines = _construire_examines(commande)
    examines_tronques = 0
    if len(examines) > 200:
        examines_tronques = len(examines) - 200
        examines = examines[:200]

    return {
        "commande": " ".join(commande),
        "temps_mur_ms": round(temps_mur, 1),
        "cpu_total_ms": round((cpu_user + cpu_system) * 1000, 1),
        "cpu_user_ms": round(cpu_user * 1000, 1),
        "cpu_system_ms": round(cpu_system * 1000, 1),
        "memoire_crete_mo": round(memoire_crete / (1024 * 1024), 1),
        "io_lu_mo": round(io_lu / (1024 * 1024), 1),
        "io_ecrit_mo": round(io_ecrit / (1024 * 1024), 1),
        "processus": nb_processus,
        "charge_machine": charge_moyenne,
        "machine": obtenir_info_machine(),
        "etat": etat,
        "echantillons": echantillons,
        "denominateur": echantillons,
        "examines": examines,
        "examines_tronques": examines_tronques,
    }


def mesurer_session(commandes: list[list[str]]) -> dict:
    """Agrège le coût de N commandes et indique ce qui a été déporté."""
    resultats = []
    total_cpu = 0.0
    total_mur = 0.0
    denominateur_total = 0
    examines_total: list[str] = []

    for cmd in commandes:
        res = mesurer_commande(cmd)
        resultats.append(res)
        if res.get("etat") == "OK":
            total_cpu += res["cpu_total_ms"]
            total_mur += res["temps_mur_ms"]
        denominateur_total += res.get("denominateur", 0)
        examines_total.extend(res.get("examines", []))

    examines_tronques = 0
    if len(examines_total) > 200:
        examines_tronques = len(examines_total) - 200
        examines_total = examines_total[:200]

    return {
        "commandes": resultats,
        "total_cpu_ms": round(total_cpu, 1),
        "total_mur_ms": round(total_mur, 1),
        "nb_commandes": len(commandes),
        "deportement": "local gratuit",
        "denominateur": denominateur_total,
        "examines": examines_total,
        "examines_tronques": examines_tronques,
    }


def compter_jetons(texte: str, conteneur: bool) -> dict:
    """Compte les jetons d'un texte, en déportant si demandé, ou par approximation."""
    methode = ""
    nb_jetons = 0
    cout_ms = 0.0
    cout_nominal_ms = None
    ecart_nominal_ms = None

    if conteneur:
        if tiktoken:
            try:
                debut = time.perf_counter()
                enc = tiktoken.get_encoding("cl100k_base")
                nb_jetons = len(enc.encode(texte))
                fin = time.perf_counter()
                cout_ms = (fin - debut) * 1000
                methode = "tiktoken (conteneur)"
                cout_nominal_ms = COUT_NOMINAL_CONTENEUR_MS
                ecart_nominal_ms = round(cout_ms - cout_nominal_ms, 1)
            except Exception:
                methode = "approximation fixe à 4 car./jeton (conteneur, tiktoken en échec)"
                nb_jetons = round(len(texte) / 4.0)
                cout_nominal_ms = COUT_NOMINAL_CONTENEUR_MS
        else:
            methode = "approximation fixe à 4 car./jeton (conteneur, tiktoken absent)"
            nb_jetons = round(len(texte) / 4.0)
            cout_nominal_ms = COUT_NOMINAL_CONTENEUR_MS
    else:
        methode = "approximation fixe à 4 car./jeton (hôte, SAC signalé)"
        nb_jetons = round(len(texte) / 4.0)

    denominateur = 1  # on examine le texte une fois
    examines = ["texte"]
    examines_tronques = 0

    return {
        "texte_longueur": len(texte),
        "nb_jetons": nb_jetons,
        "methode": methode,
        "cout_ms": round(cout_ms, 1),
        "cout_nominal_ms": cout_nominal_ms,
        "ecart_nominal_ms": ecart_nominal_ms,
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Banc d'essai pour mesurer le coût réel d'un agent (processus, mémoire, CPU, jetons).",
        epilog="Exemple: python banc_agent.py mesurer python travail.py",
    )
    parser.add_argument("--racine", type=str, default=str(RACINE), help="Surcharge la racine du projet")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")

    subparsers = parser.add_subparsers(dest="sous_commande", required=True)

    p_mesurer = subparsers.add_parser("mesurer", help="Mesure le coût complet d'une commande")
    p_mesurer.add_argument("commande", nargs=argparse.REMAINDER, help="La commande à exécuter")

    p_session = subparsers.add_parser("session", help="Agrège le coût de N commandes séparées par ';'")
    p_session.add_argument("commandes", nargs="+", help="Les commandes à exécuter")

    p_jetons = subparsers.add_parser("jetons", help="Compte les jetons d'un texte")
    p_jetons.add_argument("texte", help="Le texte à analyser")
    p_jetons.add_argument("--conteneur", action="store_true", help="Utilise tiktoken en conteneur")

    args = parser.parse_args()

    _ = Path(args.racine)

    resultat = {}
    code_sortie = 0

    if args.sous_commande == "mesurer":
        if not args.commande:
            print("Erreur: commande manquante", file=sys.stderr)
            return 1
        res = mesurer_commande(args.commande)
        resultat = res
        if res.get("denominateur", 0) == 0:
            print("DENOMINATEUR ZERO - CONCLUSION REFUSEE", file=sys.stderr)
            code_sortie = 3
        elif res.get("etat") == "NON_MESURE":
            code_sortie = 2
        elif res.get("etat") == "DEGRADE":
            code_sortie = 3

    elif args.sous_commande == "session":
        cmds = [c.split() for c in " ".join(args.commandes).split(";") if c.strip()]
        res = mesurer_session(cmds)
        resultat = res
        if res.get("denominateur", 0) == 0:
            print("DENOMINATEUR ZERO - CONCLUSION REFUSEE", file=sys.stderr)
            code_sortie = 3
        elif any(r.get("etat") == "NON_MESURE" for r in res["commandes"]):
            code_sortie = 2

    elif args.sous_commande == "jetons":
        res = compter_jetons(args.texte, args.conteneur)
        resultat = res
        # Aucun cas d'erreur particulier pour jetons dans les exigences

    resultat["contrat"] = CONTRAT

    if args.json:
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
    else:
        if args.sous_commande == "mesurer":
            r = resultat
            print(f"Commande      : {r['commande']}")
            print(f"Temps mur     : {r['temps_mur_ms']} ms")
            print(f"CPU total     : {r['cpu_total_ms']} ms (user {r['cpu_user_ms']} · system {r['cpu_system_ms']})")
            print(f"Mémoire crête : {r['memoire_crete_mo']} Mo")
            print(f"E/S           : lu {r['io_lu_mo']} Mo · écrit {r['io_ecrit_mo']} Mo")
            print(f"Processus     : {r['processus']}")
            print(f"Charge machine: {r['charge_machine']} %")
            print(f"Dénominateur  : {r['denominateur']}")
            print(f"État          : {r['etat']} (échantillons: {r['echantillons']})")
            if r['etat'] == "NON_MESURE":
                print("AVERTISSEMENT: Dénominateur d'échantillons à zéro, conclusion refusée.", file=sys.stderr)
        elif args.sous_commande == "session":
            print(f"Session de {resultat['nb_commandes']} commandes")
            for r in resultat["commandes"]:
                print(f" - {r['commande']}: {r['temps_mur_ms']} ms, CPU {r['cpu_total_ms']} ms")
            print(f"Dénominateur total : {resultat['denominateur']}")
            print(f"Total mur: {resultat['total_mur_ms']} ms, Total CPU: {resultat['total_cpu_ms']} ms")
        elif args.sous_commande == "jetons":
            print(f"Texte longueur: {resultat['texte_longueur']}")
            print(f"Nb jetons     : {resultat['nb_jetons']}")
            print(f"Méthode       : {resultat['methode']}")
            print(f"Coût          : {resultat['cout_ms']} ms")
            if resultat.get("cout_nominal_ms") is not None:
                print(f"Coût nominal  : {resultat['cout_nominal_ms']} ms (conteneur)")
                if resultat.get("ecart_nominal_ms") is not None:
                    print(f"Écart nominal : {resultat['ecart_nominal_ms']} ms")

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())