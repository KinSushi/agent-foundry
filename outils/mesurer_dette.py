"""mesurer_dette.py
Outil de mesure de la dette technique d'un fichier Python.

CONTRAT
QUESTION       ce changement ajoute-t-il de la dette, et où ?
MESURE         complexité cyclomatique par comptage AST des branches ;
               volume de Halstead ; indice de maintenabilité
HYPOTHÈSES    le fichier est du Python valide ; les seuils conventionnels
               sont acceptés par l'appelant
LIMITES        la complexité ne mesure PAS la qualité — une fonction simple
               peut être fausse, une fonction à 30 branches peut être un
               automate légitime ; la formule de maintenabilité n'est pas
               normalisée ; ast.walk additionne les fonctions imbriquées
CONTRE-EXEMPLE `verifier.py` de ce projet sort à 13,7/100 et il fonctionne :
               un score faible signale, il ne condamne pas
DOMAINE        fichiers Python syntaxiquement valides
"""

from __future__ import annotations

import ast
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout et stderr (règle 2 du socle)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
# ------------------------------------------------------------

# ----------------------------------------------------------------------
# Core – aucune dépendance à argparse, aucune impression
# ----------------------------------------------------------------------


def _complexite_par_fonction(tree: ast.AST) -> Dict[str, int]:
    """Calcule la complexité cyclomatique de chaque fonction du module.

    La complexité est incrémentée selon les règles du plan :
    - If, For, While, With, Assert, IfExp, Match, ExceptHandler → +1
    - BoolOp → + (nombre de valeurs – 1)
    - comprehension → +1
    - Match.cases → +1 par case
    Les fonctions imbriquées ajoutent leur complexité à leurs parents.
    """
    complexites: Dict[str, int] = defaultdict(int)
    stack: List[str] = []

    class Visiteur(ast.NodeVisitor):
        def _ajouter(self, inc: int) -> None:
            for nom in stack:
                complexites[nom] += inc

        # ---- structures de contrôle ----
        def visit_If(self, node: ast.If) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_While(self, node: ast.While) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_With(self, node: ast.With) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_Assert(self, node: ast.Assert) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_IfExp(self, node: ast.IfExp) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        def visit_Match(self, node: ast.Match) -> None:
            # chaque case compte comme une branche
            self._ajouter(len(node.cases))
            self.generic_visit(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        # ---- booléens ----
        def visit_BoolOp(self, node: ast.BoolOp) -> None:
            inc = max(0, len(node.values) - 1)
            self._ajouter(inc)
            self.generic_visit(node)

        # ---- compréhensions ----
        def visit_comprehension(self, node: ast.comprehension) -> None:
            self._ajouter(1)
            self.generic_visit(node)

        # ---- fonctions (gestion de la pile) ----
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nom = ".".join(stack + [node.name]) if stack else node.name
            stack.append(nom)
            complexites.setdefault(nom, 0)
            self.generic_visit(node)
            stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)  # même traitement

    Visiteur().visit(tree)
    return dict(complexites)


def _halstead_metrics(tree: ast.AST) -> Tuple[int, int, int, int, float]:
    """Retourne (N1, N2, n1, n2, V) selon la formule de Halstead.

    N1 : nombre total d'opérateurs
    N2 : nombre total d'opérandes
    n1 : nombre d'opérateurs distincts
    n2 : nombre d'opérandes distincts
    V  : volume = (N1+N2) * log2(n1+n2)
    """
    operateurs: List[str] = []
    operandes: List[str] = []

    for node in ast.walk(tree):
        # Opérateurs
        if isinstance(node, ast.BinOp):
            operateurs.append(type(node.op).__name__)
        elif isinstance(node, ast.UnaryOp):
            operateurs.append(type(node.op).__name__)
        elif isinstance(node, ast.BoolOp):
            operateurs.append(type(node.op).__name__)
        elif isinstance(node, ast.Compare):
            operateurs.extend(type(op).__name__ for op in node.ops)
        elif isinstance(node, ast.AugAssign):
            operateurs.append(type(node.op).__name__)
        elif isinstance(node, ast.Call):
            operateurs.append("call")
        elif isinstance(node, ast.Subscript):
            operateurs.append("subscript")
        # Opérandes
        if isinstance(node, ast.Name):
            operandes.append(node.id)
        elif isinstance(node, ast.Constant):
            operandes.append(repr(node.value))

    N1 = len(operateurs)
    N2 = len(operandes)
    n1 = len(set(operateurs))
    n2 = len(set(operandes))
    total = n1 + n2
    V = (N1 + N2) * (math.log2(total) if total > 0 else 0.0)
    return N1, N2, n1, n2, V


def _sloc(source: str) -> int:
    """Compte les lignes de code effectives (exclut vides et commentaires)."""
    return sum(
        1
        for ligne in source.splitlines()
        if ligne.strip() and not ligne.lstrip().startswith("#")
    )


def _maintenabilite(volume: float, complexite_max: int, sloc: int) -> float:
    """Indice de maintenabilité selon la formule citée dans le plan."""
    if volume <= 0 or sloc <= 0:
        return 0.0
    mi = 171.0 - 5.2 * math.log(volume) - 0.23 * complexite_max - 16.2 * math.log(sloc)
    mi = max(0.0, mi)  # jamais négatif
    return mi * 100.0 / 171.0  # ramené sur 100


def _verdict_complexite(val: int) -> str:
    if val <= 5:
        return "SIMPLE"
    if 6 <= val <= 10:
        return "MODÉRÉE"
    if 11 <= val <= 20:
        return "COMPLEXE"
    return "À DÉCOUPER"


def mesurer(
    fichier: Path, seuil: int | None = None
) -> Tuple[List[Tuple[str, int, str]], Dict[str, object]]:
    """Analyse un fichier Python et renvoie les métriques demandées.

    Retourne une liste de (nom_fonction, complexité, verdict) ainsi qu'un
    dictionnaire de synthèse contenant le volume Halstead, l'indice de
    maintenabilité et la formule utilisée.
    """
    source = fichier.read_text(encoding="utf-8")
    try:
        compile(source, str(fichier), "exec")
    except SyntaxError as exc:
        raise ValueError(f"Syntaxe invalide dans {fichier}: {exc}") from exc

    tree = ast.parse(source, filename=str(fichier), mode="exec")
    complexites = _complexite_par_fonction(tree)

    if not complexites:
        # zéro fonction – on retourne vide, le décideur gérera le dénominateur zéro
        return [], {}

    max_complexite = max(complexites.values())
    verdicts = [(nom, c, _verdict_complexite(c)) for nom, c in complexites.items()]

    N1, N2, n1, n2, volume = _halstead_metrics(tree)
    sloc = _sloc(source)
    mi = _maintenabilite(volume, max_complexite, sloc)

    synthese = {
        "maintenabilite": round(mi, 1),
        "volume_halstead": round(volume, 1),
        "formule": "171 − 5,2·ln(V) − 0,23·C − 16,2·ln(SLOC) (ramené sur 100)",
        "seuils": "SIMPLE ≤5, MODÉRÉE 6–10, COMPLEXE 11–20, À DÉCOUPER >20",
    }

    # Gestion du seuil (règle 5 + 11)
    if seuil is not None and any(c > seuil for _, c, _ in verdicts):
        synthese["code_sortie"] = 1  # défaut trouvé
    else:
        synthese["code_sortie"] = 0

    return verdicts, synthese


def deriver(
    avant: Path, apres: Path, seuil: int | None = None
) -> Tuple[List[Tuple[str, int, int, int, str]], Dict[str, object]]:
    """Compare deux versions d'un même fichier.

    Retourne pour chaque fonction : (nom, complexité_avant,
    complexité_après, delta, verdict_delta).
    """
    avant_res, _ = mesurer(avant)
    apres_res, _ = mesurer(apres)

    avant_dict = {nom: c for nom, c, _ in avant_res}
    apres_dict = {nom: c for nom, c, _ in apres_res}

    noms = sorted(set(avant_dict) | set(apres_dict))
    derivations = []
    for nom in noms:
        c_av = avant_dict.get(nom, 0)
        c_ap = apres_dict.get(nom, 0)
        delta = c_ap - c_av
        if delta > 0:
            verdict = "DETTE AJOUTÉE"
        elif delta < 0:
            verdict = "DETTE RÉDUITE"
        else:
            verdict = "PAS DE DETTE"
        derivations.append((nom, c_av, c_ap, delta, verdict))

    # seuil appliqué sur le delta maximal positif
    code_sortie = 0
    if seuil is not None and any(d > seuil for _, _, _, d, _ in derivations):
        code_sortie = 1

    synthese = {"code_sortie": code_sortie}
    return derivations, synthese

# ----------------------------------------------------------------------
# CLI – uniquement ici que l'on touche à argparse / print / sys.stderr
# ----------------------------------------------------------------------


def _afficher_humain_mesure(
    resultats: List[Tuple[str, int, str]], synthese: Dict[str, object]
) -> None:
    for nom, c, v in resultats:
        print(f"{nom:30} {c:3}   {v}")
    print("--")
    print(
        f"maintenabilité {synthese['maintenabilite']}/100   "
        f"volume {synthese['volume_halstead']}   formule : {synthese['formule']}"
    )
    print(f"seuils : {synthese['seuils']}")


def _afficher_humain_derivation(
    derivations: List[Tuple[str, int, int, int, str]]
) -> None:
    for nom, av, ap, delta, verdict in derivations:
        signe = f"+{delta}" if delta > 0 else str(delta)
        print(f"{nom:30} {av:3} -> {ap:3}   ({signe})   *** {verdict} ***")


def _json_sortie(
    mode: str,
    data: dict,
) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def main() -> int:
    import argparse  # Import localisé pour respecter la séparation du cœur

    parser = argparse.ArgumentParser(
        description="Mesure la dette technique d'un fichier Python.",
        epilog="Exemple : python -m outils.mesurer_dette mesurer mon_module.py --seuil 20",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du projet (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande mesurer
    p_mesurer = subparsers.add_parser(
        "mesurer", help="Mesurer la dette d'un fichier Python."
    )
    p_mesurer.add_argument("fichier", type=Path, help="Fichier Python à analyser.")
    p_mesurer.add_argument(
        "--seuil",
        type=int,
        help="Seuil de complexité : code de sortie non nul si dépassé.",
    )

    # sous‑commande deriver
    p_deriver = subparsers.add_parser(
        "deriver", help="Comparer deux versions d'un même fichier."
    )
    p_deriver.add_argument("avant", type=Path, help="Version antérieure du fichier.")
    p_deriver.add_argument("apres", type=Path, help="Version actuelle du fichier.")
    p_deriver.add_argument(
        "--seuil",
        type=int,
        help="Seuil de delta de complexité : code de sortie non nul si dépassé.",
    )

    args = parser.parse_args()

    # Gestion du paramètre racine (non utilisé directement ici, mais conforme à la règle)
    racine = args.racine

    try:
        if args.commande == "mesurer":
            resultats, synthese = mesurer(args.fichier, seuil=args.seuil)
            denom = len(resultats)
            if denom == 0:
                print("Aucun élément examiné ; impossible de conclure.", file=sys.stderr)
                return 3
            if args.json:
                sortie = {
                    "denominateur": denom,
                    "resultats": [
                        {"fonction": n, "complexite": c, "verdict": v}
                        for n, c, v in resultats
                    ],
                    "synthese": synthese,
                    "contrat": {
                        "QUESTION": "ce changement ajoute-t-il de la dette, où ?",
                        "MESURE": "complexité cyclomatique, volume Halstead, indice de maintenabilité",
                        "HYPOTHÈSES": "fichier Python valide ; seuils conventionnels",
                        "LIMITES": "complexité ≠ qualité ; formule non normalisée ; fonctions imbriquées comptées ensemble",
                        "CONTRE-EXEMPLES": "`verifier.py` donne 13,7/100 mais ne doit pas être condamné",
                        "DOMAINE": "fichiers Python syntaxiquement valides",
                    },
                }
                print(_json_sortie("mesurer", sortie))
                return synthese.get("code_sortie", 0)
            else:
                _afficher_humain_mesure(resultats, synthese)
                return synthese.get("code_sortie", 0)

        else:  # deriver
            derivations, synthese = deriver(args.avant, args.apres, seuil=args.seuil)
            denom = len(derivations)
            if denom == 0:
                print("Aucun élément examiné ; impossible de conclure.", file=sys.stderr)
                return 3
            if args.json:
                sortie = {
                    "denominateur": denom,
                    "derivations": [
                        {
                            "fonction": n,
                            "complexite_avant": av,
                            "complexite_apres": ap,
                            "delta": d,
                            "verdict": v,
                        }
                        for n, av, ap, d, v in derivations
                    ],
                    "synthese": synthese,
                    "contrat": {
                        "QUESTION": "ce changement ajoute-t-il de la dette, où ?",
                        "MESURE": "différence de complexité cyclomatique entre deux versions",
                        "HYPOTHÈSES": "les deux fichiers sont valides ; seuils conventionnels",
                        "LIMITES": "ne mesure pas l'impact fonctionnel ; ne considère que la complexité",
                        "CONTRE-EXEMPLES": "une fonction ajoutée sans changement de complexité n'est pas de la dette",
                        "DOMAINE": "comparaison de deux fichiers Python valides",
                    },
                }
                print(_json_sortie("deriver", sortie))
                return synthese.get("code_sortie", 0)
            else:
                _afficher_humain_derivation(derivations)
                return synthese.get("code_sortie", 0)

    except RuntimeError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 2  # code dédié au zéro fonction / situation non concluante
    except ValueError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 3  # syntaxe invalide
    except Exception as e:  # pragma: no cover
        print(f"Erreur inattendue : {e}", file=sys.stderr)
        return 99


if __name__ == "__main__":
    raise SystemExit(main())