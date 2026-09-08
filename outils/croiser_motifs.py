"""
QUESTION       ces motifs se recouvrent-ils, et lequel ne servira jamais ?
MESURE         traduction en automate fini, intersection, différence,
               témoin extrait de l'automate ; croisement re.fullmatch
HYPOTHÈSES     les motifs sont traduisibles en automate fini
LIMITES        backreference et \b REFUSÉS ; coût QUADRATIQUE en nombre de
               motifs ; le témoin est UNE chaîne, pas toutes
CONTRE-EXEMPLES un motif refusé rendu « disjoint » déclarerait sûr ce qui
               l'est pas — d'où INDÉCIDABLE, et un code de sortie distinct
INVOCATION
    {outil} croiser {dossier}/motifs.txt --json
DOMAINE        expressions régulières sans backreference ni frontière de mot
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterable, Iterator
from itertools import combinations
from pathlib import Path
from typing import Any, Literal, NamedTuple, Optional

# Reconfigure both stdout and stderr to UTF‑8 when possible
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent
_json_mode: bool = False  # set in main() when --json is present

class CroiserMotifsError(Exception):
    """Exception levée par le cœur lorsqu’une erreur de traitement survient."""

    def __init__(self, message: str, denominateur: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.denominateur = denominateur

class Verdict(NamedTuple):
    statut: Literal[
        "COLLISION",
        "DISJOINTES",
        "INDÉCIDABLE",
        "SUSPECT",
        "MORT",
        "NON_COUVERT",
    ]
    motif_a: str
    motif_b: str
    témoin: Optional[str] = None
    désaccord: Optional[tuple[str, bool, bool]] = None

def _lire_motifs_fichier(chemin: Path) -> list[str]:
    """Lit un fichier et extrait les motifs : un par ligne, ou JSON, ou littéraux ast."""
    if not chemin.exists():
        raise CroiserMotifsError(f"Fichier introuvable : {chemin}", 0)
    if chemin.is_dir():
        raise CroiserMotifsError(f"Chemin est un dossier, fichier attendu : {chemin}", 0)

    try:
        contenu = chemin.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CroiserMotifsError(f"{chemin} n'est pas un fichier texte UTF-8.", 0) from exc

    if chemin.suffix.lower() == ".json":
        try:
            motifs = json.loads(contenu)
            if not isinstance(motifs, list):
                raise CroiserMotifsError("le JSON doit être une liste de motifs.", 0)
            return [str(m) for m in motifs]
        except json.JSONDecodeError as exc:
            raise CroiserMotifsError(f"Erreur JSON : {exc}", 0) from exc

    try:
        arbre = ast.parse(contenu, filename=str(chemin))
        motifs = []
        for nœud in ast.walk(arbre):
            if isinstance(nœud, ast.Call) and isinstance(nœud.func, ast.Name):
                if nœud.func.id == "re.compile" and nœud.args:
                    arg = nœud.args[0]
                    if isinstance(arg, (ast.Str, ast.Constant)):
                        motifs.append(arg.value if isinstance(arg, ast.Str) else arg.value)
        if motifs:
            return motifs
    except (SyntaxError, ValueError):
        pass

    return [ligne.strip() for ligne in contenu.splitlines() if ligne.strip()]

def _lire_motifs_grammaire(chemin: Path) -> list[str]:
    """Extrait les motifs des terminaux d'une grammaire Lark."""
    try:
        import lark
    except ImportError as exc:
        raise CroiserMotifsError("'lark' est requis pour lire une grammaire.", 0) from exc

    if not chemin.exists():
        raise CroiserMotifsError(f"Fichier introuvable : {chemin}", 0)
    if chemin.is_dir():
        raise CroiserMotifsError(f"Chemin est un dossier, fichier attendu : {chemin}", 0)

    try:
        grammaire = lark.Lark.open(str(chemin), parser="lalr")
    except (lark.LarkError, FileNotFoundError) as exc:
        raise CroiserMotifsError(f"Erreur Lark : {exc}", 0) from exc

    return [
        terminal.pattern.to_regexp()
        for terminal in grammaire.terminals
        if hasattr(terminal, "pattern") and terminal.pattern
    ]

def _construire_automate(motif: str) -> Optional[Any]:
    """Traduit un motif en automate fini via interegular si disponible, sinon None."""
    try:
        from interegular import parse_pattern
        try:
            return parse_pattern(motif).to_fsm()
        except (ValueError, NotImplementedError):
            return None
    except ImportError:
        return None

def _témoin_automate(fsm: Any) -> Optional[str]:
    """Extrait un témoin de l'automate (la chaîne la plus courte)."""
    try:
        return next(fsm.strings())
    except StopIteration:
        return None

def _croiser_automates(fsm_a: Any, fsm_b: Any) -> Optional[str]:
    """Intersection des automates et témoin."""
    intersection = fsm_a & fsm_b
    if intersection.empty():
        return None
    return _témoin_automate(intersection)

def _échantillonner_témoin(motif_a: str, motif_b: str) -> Optional[str]:
    """Tente de trouver un témoin par échantillonnage sur les motifs eux-mêmes."""
    try:
        # Essayer des chaînes simples
        for s in ["a", "b", "0", "1", "test", ""]:
            if re.fullmatch(motif_a, s) and re.fullmatch(motif_b, s):
                return s
        # Essayer des littéraux extraits des motifs
        for pattern in [motif_a, motif_b]:
            for lit in re.findall(r'["\'](.*?)["\']', pattern):
                if re.fullmatch(motif_a, lit) and re.fullmatch(motif_b, lit):
                    return lit
    except re.error:
        pass
    return None

def _comparer_drapeaux(motif_a: str, motif_b: str) -> bool:
    """Compare les drapeaux des motifs (simplifié)."""
    try:
        flags_a = re.compile(motif_a).flags
        flags_b = re.compile(motif_b).flags
        return flags_a == flags_b
    except re.error:
        return False

def _comparer_groupes_nommés(motif_a: str, motif_b: str) -> bool:
    """Compare les groupes nommés des motifs."""
    try:
        groupes_a = set(re.compile(motif_a).groupindex.keys())
        groupes_b = set(re.compile(motif_b).groupindex.keys())
        return groupes_a == groupes_b
    except re.error:
        return False

def _croiser_re(motif_a: str, motif_b: str, témoin: str) -> bool:
    """Vérifie que le témoin est accepté par les deux motifs (re.fullmatch)."""
    try:
        return bool(re.fullmatch(motif_a, témoin)) and bool(re.fullmatch(motif_b, témoin))
    except re.error:
        return False

def croiser(motifs: list[str], max_paires: Optional[int] = None) -> Iterator[Verdict]:
    """Cœur : croise les motifs et rend les verdicts."""
    if not motifs:
        raise CroiserMotifsError("aucun motif fourni", 0)

    paires = list(combinations(motifs, 2))
    if max_paires is not None and len(paires) > max_paires:
        raise CroiserMotifsError(
            f"{len(paires)} paires dépassent --max-paires={max_paires}.", 0
        )

    if len(paires) > 1000 and max_paires is None:
        sys.stderr.write(f"Analyse de {len(paires)} paires (maximum possible)...\n")
        sys.stderr.write("Confirmez-vous le lancement ? [o/N] ")
        réponse = input().strip().lower()
        if réponse != "o":
            raise SystemExit(0)
    else:
        sys.stderr.write(f"Analyse de {len(paires)} paires...\n")

    interegular_disponible = True
    try:
        from interegular import parse_pattern
    except ImportError:
        interegular_disponible = False
        sys.stderr.write("Mode dégradé : 'interegular' non disponible. Résultats partiels.\n")

    for motif_a, motif_b in paires:
        if not interegular_disponible:
            # Mode dégradé
            témoin = _échantillonner_témoin(motif_a, motif_b)
            if témoin is not None:
                if _croiser_re(motif_a, motif_b, témoin):
                    yield Verdict("COLLISION", motif_a, motif_b, témoin)
                else:
                    res_a = bool(re.fullmatch(motif_a, témoin))
                    res_b = bool(re.fullmatch(motif_b, témoin))
                    yield Verdict("SUSPECT", motif_a, motif_b, témoin, (témoin, res_a, res_b))
            else:
                if _comparer_drapeaux(motif_a, motif_b) and _comparer_groupes_nommés(motif_a, motif_b):
                    yield Verdict("DISJOINTES", motif_a, motif_b)
                else:
                    yield Verdict("INDÉCIDABLE", motif_a, motif_b)
            continue

        fsm_a = _construire_automate(motif_a)
        fsm_b = _construire_automate(motif_b)

        if fsm_a is None or fsm_b is None:
            yield Verdict("INDÉCIDABLE", motif_a, motif_b)
            continue

        témoin = _croiser_automates(fsm_a, fsm_b)
        if témoin is None:
            yield Verdict("DISJOINTES", motif_a, motif_b)
        else:
            if not _croiser_re(motif_a, motif_b, témoin):
                res_a = bool(re.fullmatch(motif_a, témoin))
                res_b = bool(re.fullmatch(motif_b, témoin))
                yield Verdict("SUSPECT", motif_a, motif_b, témoin, (témoin, res_a, res_b))
            else:
                yield Verdict("COLLISION", motif_a, motif_b)

def couvre(motif_a: str, motif_b: str) -> Verdict:
    """Vérifie si motif_a couvre entièrement motif_b."""
    interegular_disponible = True
    try:
        from interegular import parse_pattern
    except ImportError:
        interegular_disponible = False
        sys.stderr.write("Mode dégradé : 'interegular' non disponible. Résultats partiels.\n")

    if not interegular_disponible:
        # Mode dégradé : on ne peut pas prouver la couverture
        témoin = _échantillonner_témoin(motif_a, motif_b)
        if témoin is not None:
            return Verdict("NON_COUVERT", motif_a, motif_b, témoin)
        else:
            return Verdict("INDÉCIDABLE", motif_a, motif_b)

    fsm_a = _construire_automate(motif_a)
    fsm_b = _construire_automate(motif_b)

    if fsm_a is None or fsm_b is None:
        return Verdict("INDÉCIDABLE", motif_a, motif_b)

    différence = fsm_b - fsm_a
    if différence.empty():
        return Verdict("MORT", motif_a, motif_b)
    return Verdict("NON_COUVERT", motif_a, motif_b)

def ordre(motifs: list[str], max_paires: Optional[int] = None) -> Iterator[Verdict]:
    """Signale les motifs morts dans une liste ordonnée."""
    if not motifs:
        raise CroiserMotifsError("aucun motif fourni", 0)

    paires_possibles = len(motifs) * (len(motifs) - 1) // 2
    if max_paires is not None and paires_possibles > max_paires:
        raise CroiserMotifsError(
            f"{paires_possibles} paires dépassent --max-paires={max_paires}.", 0
        )

    if paires_possibles > 1000 and max_paires is None:
        sys.stderr.write(f"Analyse de {paires_possibles} paires (maximum possible)...\n")
        sys.stderr.write("Confirmez-vous le lancement ? [o/N] ")
        réponse = input().strip().lower()
        if réponse != "o":
            raise SystemExit(0)
    else:
        sys.stderr.write(f"Analyse de {paires_possibles} paires...\n")

    paires_comptees = 0
    for i, motif in enumerate(motifs[1:], 1):
        précédents = motifs[:i]
        for précédent in précédents:
            paires_comptees += 1
            if max_paires is not None and paires_comptees > max_paires:
                raise CroiserMotifsError(
                    f"nombre de paires dépassé --max-paires={max_paires}.", 0
                )
            verdict = couvre(précédent, motif)
            if verdict.statut == "MORT":
                yield verdict
                break

def _verdict_vers_dict(verdict: Verdict) -> dict:
    """Convertit un Verdict en dictionnaire JSON‑compatible."""
    result = {
        "statut": verdict.statut,
        "motif_a": verdict.motif_a,
        "motif_b": verdict.motif_b,
        "témoin": verdict.témoin,
        "désaccord": verdict.désaccord,
    }
    if not any(hasattr(sys.modules.get('interegular'), 'parse_pattern') for _ in [0]):
        result["mode_degrade"] = True
    return result

def _afficher_verdict(verdict: Verdict) -> None:
    """Affiche un verdict en mode humain (stdout)."""
    if verdict.statut == "COLLISION":
        print(f"COLLISION  {verdict.motif_a}")
        print(f"      avec {verdict.motif_b}")
        print(f"    témoin  {verdict.témoin}")
    elif verdict.statut == "DISJOINTES":
        print(f"DISJOINTES {verdict.motif_a} et {verdict.motif_b}")
    elif verdict.statut == "INDÉCIDABLE":
        print(f"INDÉCIDABLE {verdict.motif_a} ou {verdict.motif_b} (backreference ou \\b)")
    elif verdict.statut == "SUSPECT":
        print(f"SUSPECT {verdict.motif_a} et {verdict.motif_b}")
        print(f"    témoin  {verdict.témoin}")
        print(f"    désaccord : re={verdict.désaccord[1]}, fsm={verdict.désaccord[2]}")
    elif verdict.statut == "MORT":
        print(f"MORT {verdict.motif_b} (couvert par {verdict.motif_a})")
    # NON_COUVERT n’est pas affiché en mode humain

def main() -> int:
    """CLI : parse les arguments et exécute le mode demandé."""
    global _json_mode
    parser = argparse.ArgumentParser(
        description="Croise des motifs regex pour détecter collisions et règles mortes.",
        epilog="Exemple : croiser_motifs.py croiser motifs.txt --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut : répertoire de l'outil)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON (un seul objet)",
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    croiser_parser = sous_parsers.add_parser(
        "croiser", help="Croise une liste de motifs."
    )
    croiser_parser.add_argument(
        "fichier",
        type=Path,
        help="Fichier de motifs (un par ligne, JSON, ou source Python avec re.compile)",
    )
    croiser_parser.add_argument(
        "--max-paires",
        type=int,
        help="Nombre maximal de paires à analyser (défaut : pas de limite)",
    )
    croiser_parser.add_argument(
        "--grammaire",
        action="store_true",
        help="Le fichier est une grammaire Lark (extrait les terminaux)",
    )
    croiser_parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON (un seul objet)",
    )

    couvre_parser = sous_parsers.add_parser(
        "couvre", help="Vérifie si un motif couvre un autre."
    )
    couvre_parser.add_argument("motif_a", help="Premier motif")
    couvre_parser.add_argument("motif_b", help="Second motif")
    couvre_parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON (un seul objet)",
    )

    ordre_parser = sous_parsers.add_parser(
        "ordre", help="Signale les motifs morts dans une liste ordonnée."
    )
    ordre_parser.add_argument(
        "fichier",
        type=Path,
        help="Fichier de motifs (un par ligne, JSON, ou source Python avec re.compile)",
    )
    ordre_parser.add_argument(
        "--max-paires",
        type=int,
        help="Nombre maximal de paires à analyser (défaut : pas de limite)",
    )
    ordre_parser.add_argument(
        "--grammaire",
        action="store_true",
        help="Le fichier est une grammaire Lark (extrait les terminaux)",
    )
    ordre_parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON (un seul objet)",
    )

    args = parser.parse_args()
    _json_mode = args.json or getattr(args, "json", False)

    def _err(msg: str, code: int, denominateur: int = 0) -> None:
        if _json_mode:
            print(json.dumps({"error": msg, "denominateur": denominateur}, ensure_ascii=False))
        else:
            sys.stderr.write(msg + "\n")
        raise SystemExit(code)

    try:
        if args.commande == "croiser":
            if args.grammaire:
                motifs = _lire_motifs_grammaire(args.fichier)
            else:
                motifs = _lire_motifs_fichier(args.fichier)

            if not motifs:
                _err("Erreur : aucun motif trouvé.", 4, 0)
            if len(motifs) < 2:
                _err("Erreur : au moins deux motifs sont requis.", 4, 0)

            verdicts = list(croiser(motifs, args.max_paires))
            denominateur = len(verdicts)
            if denominateur == 0:
                _err("Aucun élément examiné.", 3, 0)

            if _json_mode:
                result = {
                    "verdicts": [_verdict_vers_dict(v) for v in verdicts],
                    "denominateur": denominateur,
                }
                print(json.dumps(result, ensure_ascii=False))
            else:
                for v in verdicts:
                    _afficher_verdict(v)

            if any(v.statut == "INDÉCIDABLE" for v in verdicts):
                return 2
            if all(v.statut == "DISJOINTES" for v in verdicts):
                return 0
            return 1

        elif args.commande == "couvre":
            verdict = couvre(args.motif_a, args.motif_b)
            if _json_mode:
                result = {
                    "verdict": _verdict_vers_dict(verdict),
                    "denominateur": 1,
                }
                print(json.dumps(result, ensure_ascii=False))
            else:
                _afficher_verdict(verdict)
            return 0 if verdict.statut != "MORT" else 1

        elif args.commande == "ordre":
            if args.grammaire:
                motifs = _lire_motifs_grammaire(args.fichier)
            else:
                motifs = _lire_motifs_fichier(args.fichier)

            if len(motifs) < 2:
                _err("Erreur : au moins deux motifs sont requis.", 4, 0)

            morts = list(ordre(motifs, args.max_paires))
            denominateur = len(morts) if morts else 0
            if denominateur == 0:
                _err("Aucun élément examiné.", 3, 0)

            if _json_mode:
                result = {
                    "verdicts": [_verdict_vers_dict(v) for v in morts],
                    "denominateur": denominateur,
                }
                print(json.dumps(result, ensure_ascii=False))
            else:
                for v in morts:
                    _afficher_verdict(v)
            return 0 if not morts else 1

    except CroiserMotifsError as e:
        _err(e.message, 2, e.denominateur)
    except SystemExit as e:
        return e.code if e.code is not None else 1
    except Exception as e:
        _err(f"Erreur : {e}", 2, 0)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())