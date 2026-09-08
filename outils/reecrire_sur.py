"""
QUESTION       puis-je modifier ce code sans rien perdre d'autre ?
MESURE         tokenize pour l'intégralité, ast pour désigner les cibles,
               comparaison jeton à jeton avant/après
HYPOTHÈSES     le fichier est du Python syntaxiquement valide
LIMITES        ne fait pas de renommage sémantique global (portées
               d'autres fichiers) ; untokenize sur 2-uplets ne restitue pas
               les espaces ; ne traite pas les f-strings imbriquées comme un
               seul jeton en 3.12+
CONTRE-EXEMPLE ast.unparse rend un code ÉQUIVALENT et pourtant appauvri —
               « équivalent » n'est pas « identique », et la différence
               s'appelle `# noqa`
DOMAINE        un fichier Python valide, modifications locales
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import json
import sys
import tokenize
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

def _lire_source(chemin: Path) -> str:
    """Lit un fichier source Python en utf-8."""
    with chemin.open("r", encoding="utf-8") as f:
        return f.read()

def _ecrire_source(chemin: Path, source: str) -> None:
    """Écrit un fichier source Python en utf-8."""
    with chemin.open("w", encoding="utf-8") as f:
        f.write(source)

def _valider_syntaxe(source: str, nom: str = "<source>") -> None:
    """Vérifie que le source est syntaxiquement valide avec compile()."""
    try:
        compile(source, nom, "exec", dont_inherit=True)
    except SyntaxError as e:
        raise ValueError(f"Source invalide syntaxiquement: {e}")

    # Vérification spécifique des erreurs de portée
    try:
        tree = ast.parse(source, filename=nom)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                globals_declared = set()
                assignments = set()
                for body_node in node.body:
                    if isinstance(body_node, ast.Global):
                        globals_declared.update(body_node.names)
                    elif isinstance(body_node, ast.Assign):
                        for target in body_node.targets:
                            if isinstance(target, ast.Name):
                                assignments.add(target.id)
                for name in globals_declared:
                    if name in assignments:
                        raise SyntaxError(f"name 'global {name}' is assigned to before global declaration")
    except SyntaxError as e:
        raise ValueError(f"Erreur de portée dans le source: {e}")

def _extraire_noms_ast(source: str, nom_cible: str) -> Set[Tuple[int, int]]:
    """
    Retourne les positions (ligne, colonne) des noms cibles dans l'AST.
    Seules les occurrences qui sont des symboles (pas dans une chaîne/commentaire)
    sont retournées.
    """
    arbre = ast.parse(source)
    positions = set()

    class VisiteurNoms(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if node.id == nom_cible and node.col_offset is not None:
                positions.add((node.lineno, node.col_offset))
            self.generic_visit(node)

    VisiteurNoms().visit(arbre)
    return positions

def _detecter_fstrings_imbriquees(source: str) -> bool:
    """Détecte les f-strings imbriquées en Python 3.12+."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        fstring_level = 0
        for tok in tokens:
            if tok.type == tokenize.STRING:
                if tok.string.startswith(('f', 'F')):
                    if fstring_level > 0:
                        return True
                    fstring_level += 1
                elif fstring_level > 0 and not tok.string.startswith(('r', 'u', 'b')):
                    fstring_level -= 1
        return False
    except tokenize.TokenError:
        return False

def _tokeniser(source: str) -> List[tokenize.TokenInfo]:
    """Tokenise le source et retourne la liste des jetons."""
    return list(tokenize.generate_tokens(io.StringIO(source).readline))

def _untokeniser(jetons: List[tokenize.TokenInfo]) -> str:
    """Reconstitue le source à partir des jetons."""
    return tokenize.untokenize(jetons)

def _remplacer_nom_dans_jetons(
    jetons: List[tokenize.TokenInfo],
    nom_ancien: str,
    nom_nouveau: str,
    positions_cibles: Set[Tuple[int, int]],
    remplacer_commentaires: bool = False,
) -> Tuple[List[tokenize.TokenInfo], int]:
    """
    Remplace les occurrences du nom dans les jetons, uniquement aux positions
    désignées par l'AST. Si remplacer_commentaires=True, remplace aussi dans
    les commentaires. Retourne les nouveaux jetons et le nombre de commentaires
    dont le texte a été modifié.
    """
    nouveaux_jetons = []
    commentaires_remplaces = 0
    for tok in jetons:
        if (
            tok.type == tokenize.NAME
            and tok.string == nom_ancien
            and (tok.start[0], tok.start[1]) in positions_cibles
        ):
            nouveaux_jetons.append(
                tokenize.TokenInfo(
                    tok.type,
                    nom_nouveau,
                    tok.start,
                    tok.end,
                    tok.line,
                )
            )
        elif (
            remplacer_commentaires
            and tok.type == tokenize.COMMENT
            and nom_ancien in tok.string
        ):
            nouveau_commentaire = tok.string.replace(nom_ancien, nom_nouveau)
            if nouveau_commentaire != tok.string:
                commentaires_remplaces += 1
            nouveaux_jetons.append(
                tokenize.TokenInfo(
                    tok.type,
                    nouveau_commentaire,
                    tok.start,
                    tok.end,
                    tok.line,
                )
            )
        else:
            nouveaux_jetons.append(tok)
    return nouveaux_jetons, commentaires_remplaces

def _comparer_jetons(
    jetons_avant: List[tokenize.TokenInfo],
    jetons_apres: List[tokenize.TokenInfo],
) -> Dict[str, Any]:
    """
    Compare deux listes de jetons et retourne un rapport des différences.
    La détection de pertes de commentaires et de chaînes se base sur le type
    de jeton (COMMENT, STRING) et non sur la présence du texte « COMMENT ».
    """
    # Construire des ensembles de (position, type, string) pour chaque source
    set_avant = {
        (tok.start, tok.type, tok.string) for tok in jetons_avant
    }
    set_apres = {
        (tok.start, tok.type, tok.string) for tok in jetons_apres
    }

    pertes_ensembles = set_avant - set_apres
    ajouts_ensembles = set_apres - set_avant

    # Pour l'affichage, on ne garde que la représentation textuelle du jeton
    pertes = [string for _, _, string in sorted(pertes_ensembles)]
    ajouts = [string for _, _, string in sorted(ajouts_ensembles)]

    perte_commentaires = any(
        typ == tokenize.COMMENT for _, typ, _ in pertes_ensembles
    )
    perte_chaine = any(
        typ == tokenize.STRING for _, typ, _ in pertes_ensembles
    )

    commentaires_perdus = sum(1 for _, typ, _ in pertes_ensembles if typ == tokenize.COMMENT)

    return {
        "pertes": pertes,
        "ajouts": ajouts,
        "perte_commentaires": perte_commentaires,
        "perte_chaine": perte_chaine,
        "commentaires_perdus": commentaires_perdus,
    }

def _inventorier_commentaires(
    jetons: List[tokenize.TokenInfo],
) -> List[Dict[str, Union[int, str]]]:
    """
    Retourne la liste des commentaires avec leurs positions et directives.
    """
    commentaires = []
    directives = {"noqa", "type:", "pragma", "fmt:", "pylint:"}

    for tok in jetons:
        if tok.type == tokenize.COMMENT:
            texte = tok.string
            directive = None
            for d in directives:
                if d in texte:
                    directive = d
                    break
            commentaires.append(
                {
                    "ligne": tok.start[0],
                    "colonne": tok.start[1],
                    "texte": texte,
                    "directive": directive,
                }
            )
    return commentaires

def remplacer(
    source: str,
    nom_ancien: str,
    nom_nouveau: str,
    remplacer_commentaires: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """
    Remplace un nom dans le source en préservant les commentaires et le style.
    Retourne le nouveau source et un rapport.
    """
    if _detecter_fstrings_imbriquees(source):
        raise ValueError("F-strings imbriquées détectées (non supporté en Python 3.12+)")

    _valider_syntaxe(source)

    positions_cibles = _extraire_noms_ast(source, nom_ancien)
    jetons_avant = _tokeniser(source)
    nouveaux_jetons, commentaires_remplaces = _remplacer_nom_dans_jetons(
        jetons_avant,
        nom_ancien,
        nom_nouveau,
        positions_cibles,
        remplacer_commentaires,
    )
    nouveau_source = _untokeniser(nouveaux_jetons)

    rapport_comparaison = _comparer_jetons(jetons_avant, nouveaux_jetons)
    rapport = {
        "occurrences_remplacees": len(positions_cibles),
        "commentaires_perdus": rapport_comparaison["commentaires_perdus"],
        "commentaires_remplaces": commentaires_remplaces,
    }

    return nouveau_source, rapport

def pertes(
    source_avant: str,
    source_apres: str,
) -> Dict[str, Any]:
    """
    Compare deux sources et retourne un rapport des pertes.
    """
    _valider_syntaxe(source_avant)
    _valider_syntaxe(source_apres)

    jetons_avant = _tokeniser(source_avant)
    jetons_apres = _tokeniser(source_apres)
    return _comparer_jetons(jetons_avant, jetons_apres)

def commentaires(source: str) -> List[Dict[str, Union[int, str]]]:
    """
    Retourne la liste des commentaires dans le source.
    """
    _valider_syntaxe(source)
    jetons = _tokeniser(source)
    return _inventorier_commentaires(jetons)

def main() -> int:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="Réécrit un fichier Python en préservant les commentaires.",
        epilog="Exemple : python reecrire_sur.py remplacer mon_fichier.py --nom seuil --par limite",
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # Commande "remplacer"
    parser_remplacer = sous_parsers.add_parser(
        "remplacer",
        help="Remplace un nom dans un fichier.",
    )
    parser_remplacer.add_argument(
        "fichier",
        type=Path,
        help="Chemin du fichier à modifier.",
    )
    parser_remplacer.add_argument(
        "--nom",
        required=True,
        help="Nom à remplacer.",
    )
    parser_remplacer.add_argument(
        "--par",
        required=True,
        help="Nouveau nom.",
    )
    parser_remplacer.add_argument(
        "--commentaires-aussi",
        action="store_true",
        help="Remplacer aussi dans les commentaires.",
    )
    parser_remplacer.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut : répertoire de l'outil).",
    )

    # Commande "diff"
    parser_diff = sous_parsers.add_parser(
        "diff",
        help="Compare deux fichiers jeton à jeton.",
    )
    parser_diff.add_argument(
        "fichier",
        type=Path,
        help="Fichier original.",
    )
    parser_diff.add_argument(
        "--apres",
        type=Path,
        required=True,
        help="Fichier modifié.",
    )
    parser_diff.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut : répertoire de l'outil).",
    )

    # Commande "commentaires"
    parser_commentaires = sous_parsers.add_parser(
        "commentaires",
        help="Liste les commentaires d'un fichier.",
    )
    parser_commentaires.add_argument(
        "fichier",
        type=Path,
        help="Fichier à analyser.",
    )
    parser_commentaires.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut : répertoire de l'outil).",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON.",
    )

    args = parser.parse_args()

    try:
        if args.commande == "remplacer":
            source = _lire_source(args.fichier)
            nouveau_source, rapport = remplacer(
                source,
                args.nom,
                args.par,
                args.commentaires_aussi,
            )
            denominateur = rapport["occurrences_remplacees"]
            if denominateur == 0:
                print("Aucun élément examiné", file=sys.stderr)
                return 3
            if args.json:
                json.dump(
                    {
                        "contrat": {
                            "QUESTION": "puis-je modifier ce code sans rien perdre d'autre ?",
                            "MESURE": "tokenize pour l'intégralité, ast pour désigner les cibles, comparaison jeton à jeton avant/après",
                            "HYPOTHÈSES": "le fichier est du Python syntaxiquement valide",
                            "LIMITES": "ne fait pas de renommage sémantique global ; untokenize sur 2-uplets ne restitue pas les espaces ; ne traite pas les f-strings imbriquées comme un seul jeton en 3.12+",
                            "CONTRE-EXEMPLE": "ast.unparse rend un code ÉQUIVALENT et pourtant appauvri — « équivalent » n'est pas « identique »",
                            "DOMAINE": "un fichier Python valide, modifications locales",
                        },
                        "denominateur": denominateur,
                        "occurrences_remplacees": rapport["occurrences_remplacees"],
                        "commentaires_perdus": rapport["commentaires_perdus"],
                        "commentaires_remplaces": rapport["commentaires_remplaces"],
                    },
                    sys.stdout,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                diff_lignes = abs(nouveau_source.count("\n") - source.count("\n"))
                print(
                    f"{rapport['occurrences_remplacees']} occurrences remplacées   "
                    f"{rapport['commentaires_perdus']} commentaire perdu   "
                    f"diff de {diff_lignes} lignes",
                    file=sys.stderr,
                )
                if rapport["commentaires_perdus"] == 0:
                    print("0 commentaire perdu (invariant vérifié)", file=sys.stderr)
                if args.commentaires_aussi:
                    print(
                        f"{rapport['commentaires_remplaces']} commentaires remplacés",
                        file=sys.stderr,
                    )
                _ecrire_source(args.fichier, nouveau_source)
            return 0

        elif args.commande == "diff":
            source_avant = _lire_source(args.fichier)
            source_apres = _lire_source(args.apres)
            jetons_avant = _tokeniser(source_avant)
            jetons_apres = _tokeniser(source_apres)
            denominateur = len(jetons_avant) + len(jetons_apres)
            if denominateur == 0:
                print("Aucun élément examiné", file=sys.stderr)
                return 3
            rapport = pertes(source_avant, source_apres)
            if args.json:
                json.dump(
                    {
                        "contrat": {
                            "QUESTION": "puis-je modifier ce code sans rien perdre d'autre ?",
                            "MESURE": "tokenize pour l'intégralité, ast pour désigner les cibles, comparaison jeton à jeton avant/après",
                            "HYPOTHÈSES": "le fichier est du Python syntaxiquement valide",
                            "LIMITES": "ne fait pas de renommage sémantique global ; untokenize sur 2-uplets ne restitue pas les espaces ; ne traite pas les f-strings imbriquées comme un seul jeton en 3.12+",
                            "CONTRE-EXEMPLE": "ast.unparse rend un code ÉQUIVALENT et pourtant appauvri — « équivalent » n'est pas « identique »",
                            "DOMAINE": "un fichier Python valide, modifications locales",
                        },
                        "denominateur": denominateur,
                        "pertes": rapport["pertes"],
                        "ajouts": rapport["ajouts"],
                        "perte_commentaires": rapport["perte_commentaires"],
                        "perte_chaine": rapport["perte_chaine"],
                        "commentaires_perdus": rapport["commentaires_perdus"],
                    },
                    sys.stdout,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                if rapport["pertes"]:
                    print("Pertes détectées :", file=sys.stderr)
                    for perte in rapport["pertes"]:
                        print(f"  {perte}", file=sys.stderr)
                if rapport["ajouts"]:
                    print("Ajouts détectés :", file=sys.stderr)
                    for ajout in rapport["ajouts"]:
                        print(f"  {ajout}", file=sys.stderr)
                if rapport["perte_commentaires"]:
                    print(
                        "⚠️  Perte de commentaire détectée !",
                        file=sys.stderr,
                    )
                    return 1
                if rapport["perte_chaine"]:
                    print(
                        "⚠️  Perte de chaîne détectée !",
                        file=sys.stderr,
                    )
                    return 1
                print("Aucune perte détectée.", file=sys.stderr)
                if rapport["commentaires_perdus"] == 0:
                    print("0 commentaire perdu (invariant vérifié)", file=sys.stderr)
            return 0 if not (rapport["perte_commentaires"] or rapport["perte_chaine"]) else 1

        elif args.commande == "commentaires":
            source = _lire_source(args.fichier)
            commentaires_list = commentaires(source)
            denominateur = len(commentaires_list)
            if denominateur == 0:
                print("Aucun élément examiné", file=sys.stderr)
                return 3
            if args.json:
                json.dump(
                    {
                        "contrat": {
                            "QUESTION": "puis-je modifier ce code sans rien perdre d'autre ?",
                            "MESURE": "tokenize pour l'intégralité, ast pour désigner les cibles, comparaison jeton à jeton avant/après",
                            "HYPOTHÈSES": "le fichier est du Python syntaxiquement valide",
                            "LIMITES": "ne fait pas de renommage sémantique global ; untokenize sur 2-uplets ne restitue pas les espaces ; ne traite pas les f-strings imbriquées comme un seul jeton en 3.12+",
                            "CONTRE-EXEMPLE": "ast.unparse rend un code ÉQUIVALENT et pourtant appauvri — « équivalent » n'est pas « identique »",
                            "DOMAINE": "un fichier Python valide, modifications locales",
                        },
                        "denominateur": denominateur,
                        "commentaires": commentaires_list,
                    },
                    sys.stdout,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                for com in commentaires_list:
                    directive = f" (directive: {com['directive']})" if com["directive"] else ""
                    print(
                        f"Ligne {com['ligne']}, colonne {com['colonne']} : {com['texte']}{directive}",
                        file=sys.stderr,
                    )
            return 0

    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())