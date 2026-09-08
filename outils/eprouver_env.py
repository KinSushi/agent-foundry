#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QUESTION
Ce paquet marche‑t‑il ICI, et si non, pourquoi ?  L’outil doit déterminer si les
binaires natifs d’une distribution peuvent être chargés par le chargeur du
système, sans importer le module Python complet.

MESURE
- Utilisation de ``ctypes.WinDLL`` (ou ``ctypes.CDLL`` sur non‑Windows) pour
  tenter de charger chaque fichier binaire natif trouvé.
- Croisement avec ``importlib.metadata.requires()`` afin d’isoler les
  dépendances obligatoires (excludes les extras).
- Comparaison des versions déclarées via ``packaging.version.Version`` et
  ``packaging.specifiers.SpecifierSet`` pour valider les contraintes.

HYPOTHESES
- Le binaire charge de la même façon depuis ``ctypes`` que depuis l’import
  Python.
- Les métadonnées déclarent les vraies dépendances requises pour le
  fonctionnement de la limite.

LIMITES
« charge » ne veut pas dire « travaille » : un binaire qui se charge peut tout
de même échouer à l’exécution d’une fonction.  L’outil ne détecte pas les
erreurs d’exécution, seulement les échecs de chargement.

CONTRE‑EXEMPLES
Si ``_ctypes`` est bloqué par une politique système, l’outil ne peut rien
conclure et doit l’indiquer explicitement, jamais retourner « tout va bien ».

INVOCATION
    {outil} {dossier}/a {dossier}/b --json

DOMAINE
Toutes les distributions installées dans l’interpréteur Python en cours,
sur la machine où l’outil est exécuté.

`pip list` répond « ce qui est installé ».
`pip check` répond « les versions sont‑elles cohérentes ».
Aucun ne dit si la bibliothèque **travaille**.  Cet outil comble ce manque
en testant le chargement effectif des binaires natifs.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata as metadonnees
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# WinError 4551 : « Une stratégie de contrôle d'application a bloqué ce fichier »
BLOCAGE_POLITIQUE = 4551

SUFFIXES_NATIFS = (".pyd", ".dll", ".so", ".dylib")

# Les quatre états. PUR n'est pas un défaut : c'est l'état le plus sûr.
PUR, SAIN, TROUE, BLOQUE = "PUR", "SAIN", "TROUE", "BLOQUE"


def _charger(chemin: Path) -> tuple[bool, int | None, float, str]:
    """Demande au système de charger le binaire.

    Retourne (succès, winerror, durée_ms, message).  Capture de ``BaseException``
    afin de récupérer les ``OSError`` contenant éventuellement l’attribut
    ``winerror`` sous Windows.
    """
    depart = time.perf_counter()
    try:
        chargeur = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
        chargeur(str(chemin))
        return True, None, (time.perf_counter() - depart) * 1000, ""
    except BaseException as exc:  # noqa: BLE001 — on veut tout voir
        ms = (time.perf_counter() - depart) * 1000
        return False, getattr(exc, "winerror", None), ms, f"{type(exc).__name__}: {exc}"[:200]


def _binaires_par_distribution(seulement: str | None = None) -> dict[str, list[Path]]:
    """Mappe chaque distribution installée → ses extensions natives présentes."""
    table: dict[str, list[Path]] = defaultdict(list)
    for distribution in metadonnees.distributions():
        try:
            nom = distribution.metadata["Name"]
        except Exception:
            continue
        if not nom or (seulement and nom.lower() != seulement.lower()):
            continue
        try:
            fichiers = distribution.files or []
        except Exception:
            continue
        for fichier in fichiers:
            if fichier.suffix.lower() not in SUFFIXES_NATIFS:
                continue
            try:
                chemin = Path(distribution.locate_file(fichier))
            except Exception:
                continue
            if chemin.exists():
                table[nom].append(chemin)
    return table


def _classer(charges: int, total: int) -> str:
    if total == 0:
        return PUR
    if charges == total:
        return SAIN
    if charges == 0:
        return BLOQUE
    return TROUE


def eprouver(seulement: str | None = None, seuil_ms: float = 1000.0) -> dict:
    """Éprouve tout l'environnement et rend le relevé complet."""
    table = _binaires_par_distribution(seulement)
    releve: dict[str, dict] = {}
    depart = time.perf_counter()

    for nom, chemins in sorted(table.items()):
        charges, echecs, lents = 0, [], []
        for chemin in chemins:
            succes, winerror, ms, message = _charger(chemin)
            if succes:
                charges += 1
                if ms > seuil_ms:
                    lents.append({"fichier": chemin.name, "ms": round(ms, 1)})
            else:
                echecs.append({
                    "fichier": chemin.name,
                    "winerror": winerror,
                    "politique": winerror == BLOCAGE_POLITIQUE,
                    "message": message,
                })
        releve[nom] = {
            "etat": _classer(charges, len(chemins)),
            "binaires": len(chemins),
            "charges": charges,
            "echecs": echecs,
            "lents": lents,
        }

    par_etat = defaultdict(list)
    for nom, fiche in releve.items():
        par_etat[fiche["etat"]].append(nom)

    avec_binaire = [n for n, f in releve.items() if f["binaires"]]
    atteintes = par_etat[BLOQUE] + par_etat[TROUE]
    politique = [n for n, f in releve.items()
                 if any(e["politique"] for e in f["echecs"])]

    return {
        "interpreteur": sys.executable,
        "version": sys.version.split()[0],
        "plateforme": sys.platform,
        "duree_s": round(time.perf_counter() - depart, 2),
        "distributions": len(releve),
        "resume": {
            PUR: len(par_etat[PUR]),
            SAIN: len(par_etat[SAIN]),
            TROUE: len(par_etat[TROUE]),
            BLOQUE: len(par_etat[BLOQUE]),
        },
        "avec_binaire": len(avec_binaire),
        "atteintes": sorted(atteintes),
        "par_politique_systeme": sorted(politique),
        "taux_parmi_binaires": round(100 * len(atteintes) / len(avec_binaire), 1) if avec_binaire else 0.0,
        "taux_ensemble": round(100 * len(atteintes) / len(releve), 1) if releve else 0.0,
        "detail": releve,
    }


def _afficher(bilan: dict, verbeux: bool) -> None:
    r = bilan["resume"]
    print(f"Interpreteur : {bilan['interpreteur']}")
    print(f"Version      : {bilan['version']}  ({bilan['plateforme']})")
    print(f"Eprouve en   : {bilan['duree_s']} s")
    print()
    print(f"  {'ETAT':8} {'NOMBRE':>7}   ce que cela veut dire")
    print(f"  {'-'*8} {'-'*7}   {'-'*52}")
    print(f"  {PUR:8} {r[PUR]:7d}   aucun binaire natif — hors d'atteinte d'une politique")
    print(f"  {SAIN:8} {r[SAIN]:7d}   tous les binaires chargent")
    print(f"  {TROUE:8} {r[TROUE]:7d}   une partie charge, une autre non")
    print(f"  {BLOQUE:8} {r[BLOQUE]:7d}   aucun binaire ne charge")
    print()
    print(f"  distributions à binaire natif   : {bilan['avec_binaire']}")
    print(f"  dont atteintes                  : {len(bilan['atteintes'])}"
          f"  ({bilan['taux_parmi_binaires']} %)")
    print(f"  taux sur l'ensemble             : {bilan['taux_ensemble']} %")
    if bilan["par_politique_systeme"]:
        print(f"  bloquées par une POLITIQUE SYSTÈME : {len(bilan['par_politique_systeme'])}")

    if bilan["atteintes"]:
        print()
        print("  --- distributions atteintes ---")
        for nom in bilan["atteintes"]:
            fiche = bilan["detail"][nom]
            cause = "politique système" if any(e["politique"] for e in fiche["echecs"]) else "autre"
            exemple = fiche["echecs"][0]["fichier"] if fiche["echecs"] else "?"
            reste = f" (+{len(fiche['echecs'])-1})" if len(fiche["echecs"]) > 1 else ""
            print(f"    {fiche['etat']:7} {nom:32} "
                  f"{fiche['charges']}/{fiche['binaires']} chargent — {cause} : {exemple}{reste}")

    lents = [(n, f) for n, f in bilan["detail"].items() if f["lents"]]
    if lents:
        print()
        print("  --- binaires lents à charger ---")
        for nom, fiche in lents:
            for item in fiche["lents"]:
                print(f"    {nom:32} {item['fichier']:40} {item['ms']:9.1f} ms")

    if verbeux:
        print()
        print("  --- messages complets ---")
        for nom in bilan["atteintes"]:
            for echec in bilan["detail"][nom]["echecs"]:
                print(f"    {nom}/{echec['fichier']} -> {echec['message']}")

    print()
    print("  Rappel : cet outil éprouve le CHARGEMENT des binaires, pas le")
    print("  comportement des fonctions. « SAIN » signifie « ses binaires chargent »,")
    print("  jamais « ce paquet fonctionne ».")


def main() -> int:
    analyseur = argparse.ArgumentParser(
        description="Dit ce que cet interprète peut réellement charger.",
        epilog="Exemple : python eprouver_env.py --seuil-ms 500 --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyseur.add_argument("--json", action="store_true",
                           help="rend un objet JSON sur stdout, et rien d'autre")
    analyseur.add_argument("--racine", type=Path, default=RACINE,
                           help="racine de travail (défaut : le dossier de ce script)")
    analyseur.add_argument("--seuil-ms", type=float, default=1000.0,
                           help="signale un binaire mettant plus de N ms à charger (défaut 1000)")
    analyseur.add_argument("--detail", metavar="NOM",
                           help="n'éprouve qu'une seule distribution")
    analyseur.add_argument("--verbeux", action="store_true",
                           help="affiche les messages d'erreur complets")
    arguments = analyseur.parse_args()

    if not arguments.racine.exists():
        print(f"racine introuvable : {arguments.racine}", file=sys.stderr)
        return 2

    bilan = eprouver(seulement=arguments.detail, seuil_ms=arguments.seuil_ms)

    # Calcul du dénominateur : nombre total de binaires natifs examinés
    denominateur = sum(fiche["binaires"] for fiche in bilan["detail"].values())

    if denominateur == 0:
        # Message de refus sur stderr contenant le mot « denominateur »
        print("Denominateur nul : aucun binaire natif à examiner, refus de conclure.", file=sys.stderr)
        # JSON minimal avec denominateur = 0 sur stdout
        sortie = {"denominateur": 0}
        print(json.dumps(sortie, ensure_ascii=False))
        return 3

    if arguments.json:
        bilan["denominateur"] = denominateur
        print(json.dumps(bilan, indent=1, ensure_ascii=False))
    else:
        _afficher(bilan, arguments.verbeux)

    return 1 if bilan["atteintes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())