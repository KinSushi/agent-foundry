"""QUESTION       les exemples de cette documentation sont-ils vrais ?
MESURE         doctest exécute chaque exemple ; l'AST compte ceux qui manquent
HYPOTHÈSES     le module s'importe sans effet de bord ; les exemples sont
               déterministes
LIMITES        un exemple qui dépend de l'heure, du hasard ou d'un chemin
               échoue sans être faux ; doctest compare la REPRÉSENTATION
               textuelle — 0.1+0.2 ne vaut pas 0.3 ; exemples non déterministes
               (aléatoire, heure, etc.) échouent sans être faux
CONTRE-EXEMPLE 0 exemple exécuté rend « 0 échec » — indiscernable du succès
               sans le compte `attempted`
DOMAINE        modules Python importables, exemples déterministes
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import ast
import doctest
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

RACINE = Path(__file__).resolve().parent.parent

_CONTRAT = {
    "QUESTION": "les exemples de cette documentation sont-ils vrais ?",
    "MESURE": "doctest exécute chaque exemple ; l'AST compte ceux qui manquent",
    "HYPOTHÈSES": (
        "le module s'importe sans effet de bord ; les exemples sont déterministes"
    ),
    "LIMITES": (
        "un exemple qui dépend de l'heure, du hasard ou d'un chemin échoue sans "
        "être faux ; doctest compare la REPRÉSENTATION textuelle — 0.1+0.2 ne "
        "vaut pas 0.3 ; exemples non déterministes (aléatoire, heure, etc.) échouent sans être faux"
    ),
    "CONTRE-EXEMPLE": (
        "0 exemple exécuté rend « 0 échec » — indiscernable du succès sans le "
        "compte `attempted`"
    ),
    "DOMAINE": "modules Python importables, exemples déterministes",
}

_MARQUEURS_NON_DETERMINISTE = (
    "random.",
    "time.time",
    "datetime.now",
    "datetime.today",
    "uuid.",
    "os.getpid",
    "os.getcwd",
    "tempfile.",
    "secrets.",
)

@dataclass
class ResultatFonction:
    nom: str
    etat: str  # "VÉRIFIÉ", "ÉCHEC", "NON VÉRIFIABLE", "NON DÉTERMINISTE", "ERREUR_PARSING"
    exemples: int = 0
    echecs: int = 0
    detail: str = ""

@dataclass
class Rapport:
    fichier: str
    fonctions: list[ResultatFonction] = field(default_factory=list)
    attempted: int = 0
    failed: int = 0
    sans_exemple: int = 0
    fonctions_avec_exemple_ast: int = 0
    code_sortie: int = 0
    message: str = ""

def _lire_source(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8")

def _compiler_source(source: str, nom: str) -> Any:
    return compile(source, nom, "exec")

def _compter_fonctions_publiques_avec_exemple(arbre: ast.AST) -> tuple[int, set[str]]:
    """Renvoie (nombre de fonctions publiques avec >>>, noms de ces fonctions)."""
    noms_avec: set[str] = set()
    parser = doctest.DocTestParser()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.FunctionDef) and not noeud.name.startswith("_"):
            doc = ast.get_docstring(noeud)
            if doc:
                try:
                    dt = parser.get_doctest(doc, {}, "tmp", "<string>", 0)
                    if dt.examples:
                        noms_avec.add(noeud.name)
                except Exception:
                    noms_avec.add(noeud.name)  # On considère qu'il y a un exemple mais parsing échoué
    return len(noms_avec), noms_avec

def _compter_fonctions_publiques(arbre: ast.AST) -> int:
    total = 0
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.FunctionDef) and not noeud.name.startswith("_"):
            total += 1
    return total

def _est_non_deterministe(source_exemple: str) -> bool:
    return any(marqueur in source_exemple for marqueur in _MARQUEURS_NON_DETERMINISTE)

def _construire_module(source: str, nom: str) -> ModuleType:
    """Compile et exécute la source dans un module fictif, en fixant __name__."""
    code = _compiler_source(source, nom)
    module = ModuleType(nom)
    module.__file__ = nom
    module.__dict__["__name__"] = nom
    exec(code, module.__dict__)
    return module

def _lancer_doctest(module: ModuleType, nom: str) -> tuple[int, int, list[ResultatFonction]]:
    """Exécute doctest sur le module, renvoie (attempted, failed, résultats)."""
    finder = doctest.DocTestFinder()
    globs: dict[str, Any] = {}
    tests = finder.find(module, nom, globs=globs, extraglobs={})

    runner = doctest.DocTestRunner(
        verbose=False,
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
    )

    resultats: list[ResultatFonction] = []

    for test in tests:
        nom_fonction = test.name if test.name != nom else "<module>"
        if test.examples:
            source_concat = "\n".join(ex.source for ex in test.examples)
            non_deterministe = _est_non_deterministe(source_concat)

            sortie_tampon: list[str] = []
            failed_local, attempted_local = runner.run(
                test, out=lambda s: sortie_tampon.append(s)
            )

            if non_deterministe:
                resultats.append(
                    ResultatFonction(
                        nom=nom_fonction,
                        etat="NON DÉTERMINISTE",
                        exemples=attempted_local,
                        detail="exemple dépendant du hasard ou du temps",
                    )
                )
            elif failed_local == 0:
                resultats.append(
                    ResultatFonction(
                        nom=nom_fonction,
                        etat="VÉRIFIÉ",
                        exemples=attempted_local,
                        echecs=0,
                    )
                )
            else:
                detail = "\n".join(sortie_tampon).strip()
                resultats.append(
                    ResultatFonction(
                        nom=nom_fonction,
                        etat="ÉCHEC",
                        exemples=attempted_local,
                        echecs=failed_local,
                        detail=detail,
                    )
                )
        else:
            resultats.append(
                ResultatFonction(
                    nom=nom_fonction,
                    etat="NON VÉRIFIABLE",
                    exemples=0,
                    detail="aucun exemple dans la docstring",
                )
            )

    # Ajout des fonctions avec erreur de parsing
    noms_executes = {r.nom for r in resultats if r.etat != "NON VÉRIFIABLE"}
    for test in tests:
        nom_fonction = test.name if test.name != nom else "<module>"
        if nom_fonction not in noms_executes and test.examples:
            resultats.append(
                ResultatFonction(
                    nom=nom_fonction,
                    etat="ERREUR_PARSING",
                    exemples=0,
                    detail="erreur lors du parsing de l'exemple",
                )
            )

    resume_final = runner.summarize()
    return int(resume_final.attempted), int(resume_final.failed), resultats

def verifier(chemin_fichier: str, racine: Path | None = None) -> Rapport:
    """Vérifie les exemples doctest d'un fichier Python.

    Cœur de l'outil — ne lit pas sys.argv, n'imprime rien.
    """
    base = racine if racine is not None else RACINE
    chemin = Path(chemin_fichier)
    if not chemin.is_absolute():
        chemin = base / chemin

    rapport = Rapport(fichier=str(chemin))

    if not chemin.exists():
        rapport.code_sortie = 2
        rapport.message = f"fichier introuvable : {chemin}"
        rapport.attempted = 0
        rapport.failed = 0
        return rapport

    source = _lire_source(chemin)

    # Validation par compile (règle 10)
    try:
        arbre = ast.parse(source, filename=str(chemin))
        _compiler_source(source, str(chemin))
    except SyntaxError as e:
        rapport.code_sortie = 2
        rapport.message = f"erreur de syntaxe : {e}"
        rapport.attempted = 0
        rapport.failed = 0
        return rapport

    # Comptage AST
    nb_fonctions_avec_exemple, _ = _compter_fonctions_publiques_avec_exemple(arbre)
    rapport.fonctions_avec_exemple_ast = nb_fonctions_avec_exemple

    # Construction du module et exécution doctest
    try:
        module = _construire_module(source, chemin.stem)
    except Exception as e:
        rapport.code_sortie = 2
        rapport.message = f"impossible d'exécuter le module : {e}"
        rapport.attempted = 0
        rapport.failed = 0
        return rapport

    attempted, failed, resultats = _lancer_doctest(module, chemin.stem)
    rapport.attempted = attempted
    rapport.failed = failed
    rapport.fonctions = resultats
    rapport.sans_exemple = sum(1 for r in resultats if r.etat == "NON VÉRIFIABLE")

    # Détermination du code de sortie
    if failed > 0:
        rapport.code_sortie = 1
    elif any(r.etat == "NON DÉTERMINISTE" for r in resultats):
        rapport.code_sortie = 1
    elif any(r.etat == "ERREUR_PARSING" for r in resultats):
        rapport.code_sortie = 1
    elif nb_fonctions_avec_exemple > 0 and attempted < nb_fonctions_avec_exemple:
        # Zéro silencieux partiel : l'instrument est partiellement cassé
        rapport.code_sortie = 3
        rapport.message = (
            f"zéro silencieux partiel : {nb_fonctions_avec_exemple} fonction(s) avec "
            f"exemple dans l'AST mais seulement {attempted} exemple(s) exécuté(s) par doctest"
        )
    else:
        rapport.code_sortie = 0

    return rapport

def couverture(chemin_fichier: str, racine: Path | None = None) -> dict[str, Any]:
    """Calcule le taux de fonctions publiques avec exemple exécutable."""
    base = racine if racine is not None else RACINE
    chemin = Path(chemin_fichier)
    if not chemin.is_absolute():
        chemin = base / chemin

    if not chemin.exists():
        return {"erreur": f"fichier introuvable : {chemin}"}

    source = _lire_source(chemin)
    try:
        arbre = ast.parse(source, filename=str(chemin))
    except SyntaxError as e:
        return {"erreur": f"erreur de syntaxe : {e}"}

    # Vérification de l'importabilité du module
    try:
        module = _construire_module(source, chemin.stem)
    except Exception as e:
        return {"erreur": f"impossible d'importer le module : {e}"}

    total_fonctions = _compter_fonctions_publiques(arbre)
    avec_exemple, _ = _compter_fonctions_publiques_avec_exemple(arbre)

    resultat = {
        "fichier": str(chemin),
        "fonctions_publiques": total_fonctions,
        "fonctions_avec_exemple": avec_exemple,
        "denominateur": total_fonctions,
    }

    if total_fonctions == 0:
        resultat["taux"] = None
        resultat["refus"] = True
    else:
        resultat["taux"] = avec_exemple / total_fonctions
        resultat["refus"] = False

    return resultat

def _extraire_echec(detail: str) -> tuple[str | None, str | None]:
    """Extrait les valeurs 'attendu' et 'obtenu' depuis le détail doctest.

    Le format doctest produit des lignes 'Expected:' et 'Got:' suivies
    de la valeur correspondante sur la ligne suivante.
    """
    lignes = detail.splitlines()
    attendu: str | None = None
    obtenu: str | None = None
    for i, ligne in enumerate(lignes):
        s = ligne.strip()
        if s == "Expected:" and i + 1 < len(lignes):
            attendu = lignes[i + 1].strip()
        elif s == "Got:" and i + 1 < len(lignes):
            obtenu = lignes[i + 1].strip()
    return attendu, obtenu

def _formater_humain(rapport: Rapport) -> str:
    lignes: list[str] = []
    lignes.append(
        f"{rapport.attempted} exemple(s) exécuté(s), "
        f"{rapport.failed} échec(s), "
        f"{rapport.sans_exemple} fonction(s) sans exemple"
    )

    if rapport.message and rapport.code_sortie in (2, 3):
        lignes.insert(0, rapport.message)
        return "\n".join(lignes)

    for r in rapport.fonctions:
        if r.etat == "VÉRIFIÉ":
            lignes.append(f"VÉRIFIÉ         {r.nom:<20} {r.exemples} exemple(s), 0 échec")
        elif r.etat == "ÉCHEC":
            attendu, obtenu = _extraire_echec(r.detail)
            if attendu is not None and obtenu is not None:
                lignes.append(f"ÉCHEC           {r.nom:<20} attendu {attendu}, obtenu {obtenu}")
            else:
                lignes.append(f"ÉCHEC           {r.nom:<20} {r.exemples} exemple(s), {r.echecs} échec(s)")
                if r.detail:
                    for dl in r.detail.splitlines():
                        lignes.append(f"                {dl}")
        elif r.etat == "NON VÉRIFIABLE":
            lignes.append(f"NON VÉRIFIABLE  {r.nom:<20} {r.detail}")
        elif r.etat == "NON DÉTERMINISTE":
            lignes.append(f"NON DÉTERMINISTE {r.nom:<19} {r.detail}")
        elif r.etat == "ERREUR_PARSING":
            lignes.append(f"ERREUR_PARSING  {r.nom:<19} {r.detail}")

    return "\n".join(lignes)

def _rapport_vers_dict(rapport: Rapport) -> dict[str, Any]:
    return {
        "fichier": rapport.fichier,
        "attempted": rapport.attempted,
        "failed": rapport.failed,
        "sans_exemple": rapport.sans_exemple,
        "fonctions_avec_exemple_ast": rapport.fonctions_avec_exemple_ast,
        "code_sortie": rapport.code_sortie,
        "message": rapport.message,
        "fonctions": [
            {
                "nom": r.nom,
                "etat": r.etat,
                "exemples": r.exemples,
                "echecs": r.echecs,
                "detail": r.detail,
            }
            for r in rapport.fonctions
        ],
        "contrat": _CONTRAT,
    }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vérifie les exemples doctest d'un fichier Python — exécute réellement chaque exemple.",
        epilog="Exemple : python outils/verifier_exemples.py verifier mon_module.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="racine du projet (par défaut : parent du dossier outils)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="sortie JSON unique sur stdout",
    )
    parser.add_argument(
        "--exiger-execute",
        action="store_true",
        help="refuse de conclure si l'AST voit des exemples mais doctest n'en exécute aucun",
    )

    sub = parser.add_subparsers(dest="commande", required=True)

    p_verifier = sub.add_parser("verifier", help="vérifier les exemples d'un fichier")
    p_verifier.add_argument("fichier", type=str, help="chemin du fichier Python à vérifier")

    p_couverture = sub.add_parser("couverture", help="taux de fonctions avec exemple")
    p_couverture.add_argument("fichier", type=str, help="chemin du fichier Python à analyser")

    args = parser.parse_args()
    racine = args.racine if args.racine is not None else RACINE

    if args.commande == "verifier":
        rapport = verifier(args.fichier, racine=racine)

        # --exiger-execute : forcer un code d'erreur si l'AST voit des exemples
        # mais doctest n'en a exécuté aucun (zéro silencieux)
        if args.exiger_execute and rapport.attempted == 0 and rapport.fonctions_avec_exemple_ast > 0:
            if rapport.code_sortie == 0:
                rapport.code_sortie = 3
                rapport.message = (
                    f"--exiger-execute : {rapport.fonctions_avec_exemple_ast} fonction(s) avec "
                    f"exemple dans l'AST mais 0 exemple exécuté par doctest"
                )

        if args.json:
            print(json.dumps(_rapport_vers_dict(rapport), ensure_ascii=False, indent=2))
        else:
            print(_formater_humain(rapport))
        return rapport.code_sortie

    elif args.commande == "couverture":
        resultat = couverture(args.fichier, racine=racine)

        if args.json:
            sortie = dict(resultat)
            sortie["contrat"] = _CONTRAT
            print(json.dumps(sortie, ensure_ascii=False, indent=2))
        else:
            if "erreur" in resultat:
                print(resultat["erreur"], file=sys.stderr)
                return 2
            denom = resultat["denominateur"]
            if denom == 0:
                print(
                    f"{resultat['fichier']}\n"
                    f"fonctions publiques : 0\n"
                    f"avec exemple : 0\n"
                    f"taux : REFUS — dénominateur = 0",
                    file=sys.stderr,
                )
                return 4
            taux = resultat["taux"]
            print(
                f"{resultat['fichier']}\n"
                f"fonctions publiques : {denom}\n"
                f"avec exemple : {resultat['fonctions_avec_exemple']}\n"
                f"taux : {taux:.1%} (dénominateur : {denom})"
            )
            return 0 if resultat["fonctions_avec_exemple"] > 0 else 1

    return 0

if __name__ == "__main__":
    raise SystemExit(main())