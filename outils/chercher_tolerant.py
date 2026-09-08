#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chercher_tolerant – recherche tolérante de fragments dans du code Python.

QUESTION
    Ce fragment existe‑t‑il, même mal cité, et où exactement ?
MESURE
    Recherche fuzzy avec la bibliothèque ``regex`` (ou recherche exacte si
    indisponible) ; utilise le drapeau ``BESTMATCH`` et les attributs
    ``fuzzy_counts`` pour obtenir la distance et la nature des fautes.
HYPOTHÈSES
    Le fragment cité est proche du réel (≤ N fautes) et le texte est
    encodé en UTF‑8.
LIMITES
    La recherche fuzzy est coûteuse ; la tolérance ne peut dépasser
    ``len(fragment)//3``. Au‑delà, l’outil refuse de conclure.
CONTRE‑EXEMPLE
    Avec ``--fautes 10`` sur un fragment de 12 caractères, la recherche
    renverrait presque tout ; l’outil refuse donc.
DOMAINE
    Textes courts (extraits de code), recherche tolérante de fragments.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 1. Import facultatif de ``regex``
try:
    import regex  # type: ignore
except ImportError:  # pragma: no cover
    regex = None  # noqa: N816

# --------------------------------------------------------------------------- #
# Contrat exposé dans la sortie JSON
CONTRAT = {
    "QUESTION": "Ce fragment existe‑t‑il, même mal cité, et où exactement ?",
    "MESURE": (
        "Recherche fuzzy avec la bibliothèque ``regex`` (ou recherche exacte si "
        "indisponible) ; utilise le drapeau ``BESTMATCH`` et les attributs "
        "``fuzzy_counts`` pour obtenir la distance et la nature des fautes."
    ),
    "HYPOTHÈSES": (
        "Le fragment cité est proche du réel (≤ N fautes) et le texte est encodé "
        "en UTF‑8."
    ),
    "LIMITES": (
        "La recherche fuzzy est coûteuse ; la tolérance ne peut dépasser "
        "``len(fragment)//3``. Au‑delà, l’outil refuse de conclure."
    ),
    "CONTRE‑EXEMPLE": (
        "Avec ``--fautes 10`` sur un fragment de 12 caractères, la recherche "
        "renverrait presque tout ; l’outil refuse donc."
    ),
    "DOMAINE": "Textes courts (extraits de code), recherche tolérante de fragments.",
}

# --------------------------------------------------------------------------- #
# Structures de données

@dataclass
class Resultat:
    fichier: str
    ligne: int
    offset: int
    distance: int
    substitutions: int
    insertions: int
    suppressions: int
    extrait: str
    réel: str

# --------------------------------------------------------------------------- #
# Fonctions cœur (sans argparse, sans I/O)

def _valider_fautes(fragment: str, fautes: int) -> None:
    """Vérifie la cohérence de ``fautes`` ; lève ``ValueError`` le cas échéant."""
    if not fragment:
        raise ValueError("Le fragment recherché ne doit pas être vide.")
    max_fautes = len(fragment) // 3
    if fautes > max_fautes:
        raise ValueError(
            f"La tolérance ({fautes}) dépasse le tiers de la longueur du fragment "
            f"({len(fragment)} → max = {max_fautes})."
        )
    if fautes < 0:
        raise ValueError("La tolérance ne peut être négative.")

def _chercher_fuzzy_in_text(
    fragment: str,
    texte: str,
    fautes: int,
) -> List[Resultat]:
    """Recherche le fragment dans ``texte`` avec tolérance ``fautes``.
    Retourne une liste de résultats (au maximum un par texte pour ce mode).
    """
    if regex is None:
        # Fallback : recherche exacte uniquement
        idx = texte.find(fragment)
        if idx == -1:
            return []
        distance = 0
        subs = ins = suppr = 0
        réel = fragment
    else:
        pattern = regex.escape(fragment) + f"{{e<={fautes}}}"
        try:
            matches = list(regex.finditer(
                pattern,
                texte,
                flags=regex.BESTMATCH,
            ))
        except regex.error as exc:  # pragma: no cover
            print(f"Erreur de compilation regex : {exc}", file=sys.stderr)
            return []
        if not matches:
            return []
        # On ne garde que le meilleur match par texte (comme auparavant)
        match = matches[0]
        idx = match.start()
        subs, ins, suppr = match.fuzzy_counts  # type: ignore[attr-defined]
        distance = subs + ins + suppr
        réel = texte[match.start():match.end()]

    # Calcul de la ligne
    ligne = texte.count("\n", 0, idx) + 1
    # Extraction d’un extrait de contexte (80 caractères autour)
    debut = max(0, idx - 40)
    fin = min(len(texte), idx + 40)
    extrait = texte[debut:fin].replace("\n", "↵")

    return [Resultat(
        fichier="",  # sera rempli par l’appelant
        ligne=ligne,
        offset=idx,
        distance=distance,
        substitutions=subs,
        insertions=ins,
        suppressions=suppr,
        extrait=extrait,
        réel=réel,
    )]

def trouver(
    fragment: str,
    racine: Path,
    fautes: int,
) -> List[Resultat]:
    """Parcourt récursivement ``racine`` à la recherche du ``fragment`` tolérant.
    Retourne la liste des résultats trouvés.
    """
    _valider_fautes(fragment, fautes)
    resultats: List[Resultat] = []

    for fichier in racine.rglob("*.py"):
        try:
            texte = fichier.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover
            print(f"Impossible de lire {fichier} : {exc}", file=sys.stderr)
            continue

        res = _chercher_fuzzy_in_text(fragment, texte, fautes)
        for r in res:
            r.fichier = str(fichier)
            resultats.append(r)

    return resultats

def extraire_blocs(
    texte: str,
    ouvrant: str = "(",
    fermant: str = ")",
) -> List[Tuple[int, int]]:
    """Renvoie la liste des intervalles (début, fin) des blocs correctement
    imbriqués définis par ``ouvrant``/``fermant``.
    Utilise ``regex`` avec la récursion ``(?R)`` si disponible, sinon
    implémentation pure Python.
    """
    if regex is not None:
        # Construction du motif récursif
        motif = (
            regex.escape(ouvrant)
            + r"(?:[^"
            + regex.escape(ouvrant)
            + regex.escape(fermant)
            + r"]++|(?R))*"
            + regex.escape(fermant)
        )
        try:
            matches = list(regex.finditer(motif, texte))
            return [(m.start(), m.end()) for m in matches]
        except regex.error:  # pragma: no cover
            # En cas d'erreur, on retombe sur l'implémentation pure Python
            pass

    # Implémentation pure Python (fallback)
    stack: List[int] = []
    blocs: List[Tuple[int, int]] = []

    for i, ch in enumerate(texte):
        if ch == ouvrant:
            stack.append(i)
        elif ch == fermant and stack:
            debut = stack.pop()
            blocs.append((debut, i + 1))

    return sorted(blocs, key=lambda b: b[0])

def compter_graphemes(texte: str) -> int:
    r"""Compte les graphemes de ``texte``.
    Utilise ``regex`` avec ``\X`` si disponible, sinon compte les caractères.
    """
    if regex is not None:
        return len(regex.findall(r"\X", texte))
    # Fallback : approximation naïve
    return len(texte)

# --------------------------------------------------------------------------- #
# Interface en ligne de commande

def _afficher_humain(resultats: List[Resultat], json_mode: bool, fautes: int = 0, fragment: str = "", denominateur: int = 0) -> None:
    if json_mode:
        # Le JSON est géré ailleurs
        return

    if not resultats:
        print(f"Aucun fragment trouvé, même à {fautes} fautes près.")
    else:
        for r in resultats:
            print(f"TROUVÉ   {r.fichier}:{r.ligne}  offset {r.offset}  distance {r.distance}")
            print(f"         {r.substitutions} substitution(s), "
                  f"{r.insertions} insertion(s), {r.suppressions} suppression(s)")
            print(f"cité     {fragment}")
            print(f"réel     {r.réel}")
            print("-" * 40)

    if denominateur > 0:
        print(f"Éléments examinés : {denominateur}")

def _afficher_json(
    data: dict,
    denominateur: int,
    examines: List[str],
    examines_tronques: int = 0,
) -> None:
    payload = {
        **data,
        "denominateur": denominateur,
        "examines": examines,
        "contrat": CONTRAT,
    }
    if examines_tronques > 0:
        payload["examines_tronques"] = examines_tronques
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

def _verifier_regex() -> bool:
    """Vérifie que ``regex`` fonctionne réellement."""
    if regex is None:
        return True  # pas de regex, on utilisera le fallback
    try:
        regex.search(r"(a){e<=1}", "b")
        return True
    except Exception:
        return False

def main(argv: Optional[List[str]] = None) -> int:
    # Vérification de regex au démarrage
    if not _verifier_regex():
        print("BLOQUÉ : la bibliothèque regex est présente mais ne fonctionne pas.", file=sys.stderr)
        return 4

    parser = argparse.ArgumentParser(
        prog="chercher_tolerant",
        description="Recherche tolérante de fragments dans des fichiers Python.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Répertoire racine à parcourir (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON unique sur stdout.",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande « chercher »
    sp_chercher = subparsers.add_parser(
        "chercher",
        help="Recherche un fragment avec tolérance.",
        description=(
            "Exemple : python -m outils.chercher_tolerant chercher "
            "\"def calculer_total(\" --racine . --fautes 2"
        ),
    )
    sp_chercher.add_argument("fragment", help="Fragment littéral à rechercher.")
    sp_chercher.add_argument(
        "--fautes",
        type=int,
        default=0,
        help="Nombre maximal de fautes tolérées (≤ len(fragment)//3).",
    )

    # sous‑commande « structure »
    sp_structure = subparsers.add_parser(
        "structure",
        help="Extrait les blocs correctement imbriqués.",
    )
    sp_structure.add_argument("fichier", type=Path, help="Fichier à analyser.")
    sp_structure.add_argument(
        "--ouvrant",
        default="(",
        help="Caractère ouvrant (défaut : '(').",
    )
    sp_structure.add_argument(
        "--fermant",
        default=")",
        help="Caractère fermant (défaut : ')').",
    )

    # sous‑commande « graphemes »
    sp_graphemes = subparsers.add_parser(
        "graphemes",
        help="Compte les graphemes d’un texte.",
    )
    sp_graphemes.add_argument("texte", help="Texte à analyser.")

    args = parser.parse_args(argv)

    # --------------------------------------------------------------- #
    # Gestion des sous‑commandes
    if args.commande == "chercher":
        try:
            resultats = trouver(args.fragment, args.racine, args.fautes)
        except ValueError as ve:
            print(f"Erreur : {ve}", file=sys.stderr)
            return 3  # code dédié aux paramètres invalides

        denominateur = 0
        examines = []
        examines_tronques = 0
        # Nous devons recalculer le denominateur et les examines car la fonction trouver ne les retourne pas
        # Refaisons le parcours pour collecter ces informations (sans refaire la recherche)
        for fichier in args.racine.rglob("*.py"):
            try:
                # On tente de lire le fichier pour savoir s'il est examinable
                fichier.read_text(encoding="utf-8", errors="replace")
                denominateur += 1
                if len(examines) < 200:
                    examines.append(str(fichier))
                else:
                    examines_tronques += 1
            except OSError:
                # On ne compte pas les fichiers qu'on ne peut pas lire
                pass

        if denominateur == 0:
            print("Zéro élément examiné", file=sys.stderr)
            return 3

        if args.json:
            data = {"resultats": [asdict(r) for r in resultats]}
            _afficher_json(data, denominateur, examines, examines_tronques)
        else:
            _afficher_humain(resultats, args.json, args.fautes, args.fragment, denominateur)
        return 0 if resultats else 1

    if args.commande == "structure":
        try:
            texte = args.fichier.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover
            print(f"Impossible de lire {args.fichier} : {exc}", file=sys.stderr)
            return 2
        blocs = extraire_blocs(texte, args.ouvrant, args.fermant)
        denominateur = 1
        examines = [str(args.fichier)]
        examines_tronques = 0
        if args.json:
            data = {"blocs": [{"début": d, "fin": f} for d, f in blocs]}
            _afficher_json(data, denominateur, examines, examines_tronques)
        else:
            if not blocs:
                print("Aucun bloc correctement imbriqué trouvé.")
            for d, f in blocs:
                print(f"Bloc de {d} à {f}")
            print(f"Élements examinés : {denominateur}")
        return 0

    if args.commande == "graphemes":
        nb = compter_graphemes(args.texte)
        denominateur = 1
        examines = ["<texte fourni>"]
        examines_tronques = 0
        if args.json:
            data = {"graphemes": nb}
            _afficher_json(data, denominateur, examines, examines_tronques)
        else:
            print(f"Nombre de graphemes : {nb}")
            print(f"Élements examinés : {denominateur}")
        return 0

    return 0  # jamais atteint

if __name__ == "__main__":
    raise SystemExit(main())