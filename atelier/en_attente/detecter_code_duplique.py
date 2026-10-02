"""Un agent qui génère du code recopie volontiers un bloc qui marche au lieu
d'en faire une fonction, et rien ne le lui signale. Mesuré dans cette session :
sur outils/ de ce dépôt (173 fichiers, 518 000 jetons, `--comparer-symilar`),
439 paires et 4,7 % des lignes de code dupliquées ; des 515 blocs de pylint
symilar, 469 ont leurs deux côtés marqués, 42 font moins de 50 jetons et 4
ne diffèrent que par l'indentation, que symilar ignore et que l'outil compte.

QUESTION
    Quels blocs de code sont copiés-collés dans ce dépôt, où, et quelle part du
    code ils représentent ?
MESURE
    Chaque fichier .py est découpé par tokenize ; commentaires, docstrings
    (chaîne seule sur sa ligne logique) et, par défaut, lignes d'import sont
    écartés ; fins de ligne logique et changements d'indentation restent comme
    jetons de structure.
    Option --normaliser : les identifiants deviennent ID, les littéraux LIT
    (clones « renommés »). Une empreinte roulante (Rabin-Karp, modulo 2**61-1)
    est calculée sur chaque fenêtre de --min-jetons jetons ; les fenêtres
    d'empreinte égale sont comparées jeton à jeton (pas de faux positif de
    collision), gardées si maximales à gauche, puis étendues à droite. Une paire
    est retenue si chacune des deux occurrences couvre au moins --min-lignes
    lignes de code. Taux de duplication = lignes de code couvertes par au moins
    une occurrence (original compris) / lignes de code examinées.
    Option --comparer-symilar : si pylint est installé, ses blocs (lignes
    identiques entre fichiers différents) sont recoupés avec ceux de l'outil.
HYPOTHÈSES
    Les fichiers sont du Python que tokenize de l'interpréteur courant découpe.
    Un bloc identique jeton à jeton (au renommage près avec --normaliser) est
    une duplication qui mérite d'être vue, même si elle est voulue.
LIMITES
    Ne voit pas les clones réordonnés ou réécrits (type 3 et 4) ; une séquence
    très répétitive dans un même fichier (tables, motifs) produit au plus une
    chaîne de paires par motif, pas toutes les paires ; la mémoire croît avec le
    nombre total de jetons (stdlib CPython 3.14.7 : 1057 fichiers, 2,0 millions
    de jetons, 7,0 s et 378 Mo mesurés) ; un bloc
    généré ou volontairement dupliqué (tests, migrations) est compté comme les
    autres. Avec --normaliser, deux suites d'affectations sans rapport peuvent
    sembler identiques.
CONTRE-EXEMPLES
    Une copie de 10 lignes où une seule ligne (journal.debug(ligne)) est
    insérée au milieu n'est rapportée que sur sa seconde moitié (5 lignes,
    53 jetons) : la première moitié fait moins de 50 jetons et disparaît.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Sources Python (fichiers ou arbres de .py) ; revue de code, CI (--taux-max),
    recherche de factorisations ; duplications syntaxiques exactes ou renommées.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import keyword
import os
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

SECTION_HYPOTHESES = "HYPOTHÈSES"
SECTION_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", SECTION_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", SECTION_INVOCATION, "DOMAINE")

JETON_IDENTIFIANT = "<ID>"
JETON_LITTERAL = "<LIT>"
JETON_FIN_LOGIQUE = "<NEWLINE>"
JETONS_BORNES = ("<NEWLINE>", "<INDENT>", "<DEDENT>")
MODULO = (1 << 61) - 1
BASE_HACHAGE = 1_000_003
TAILLE_MAX_DEFAUT = 5_000_000
MAX_FICHIERS_DEFAUT = 20_000
MAX_EXAMINES = 200
MAX_PAIRES_JSON = 500
GROUPE_MAX_TOUTES_PAIRES = 20
DOSSIERS_IGNORES = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".tox", ".nox", ".venv", "venv",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
NORMALISATIONS = ("aucune", "identifiants", "litteraux", "tout")


class EntreeInvalide(Exception):
    """Entrée refusée : chemin absent, fichier illisible ou non découpable."""


@dataclass
class Fichier:
    """Jetons normalisés d'un fichier, avec la ligne de chacun."""

    libelle: str
    valeurs: list[int]
    lignes: list[int]
    texte: list[str]


@dataclass(frozen=True)
class Paire:
    """Deux occurrences d'un même bloc (index de fichier, jeton de départ, longueur)."""

    fichier_a: int
    debut_a: int
    fichier_b: int
    debut_b: int
    longueur: int


@dataclass
class Options:
    """Réglages de la détection."""

    min_jetons: int
    min_lignes: int
    normalisation: str
    ignorer_imports: bool
    vocabulaire: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Lecture et découpage
# --------------------------------------------------------------------------- #

def lister_python(dossier: Path, maximum: int) -> list[Path]:
    """Liste les .py d'un dossier, récursivement, sans suivre les liens."""
    trouves: list[Path] = []
    for base, dossiers, fichiers in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if d not in DOSSIERS_IGNORES)
        for nom in sorted(fichiers):
            if nom.endswith(".py"):
                trouves.append(Path(base) / nom)
                if len(trouves) >= maximum:
                    print(f"avertissement : arrêt à {maximum} fichiers (--max-fichiers)", file=sys.stderr)
                    return trouves
    return trouves


def collecter_cibles(cibles: list[Path], maximum: int) -> tuple[list[Path], set[Path]]:
    """Fichiers à examiner et ensemble de ceux donnés explicitement."""
    fichiers: list[Path] = []
    explicites: set[Path] = set()
    for cible in cibles:
        if cible.is_dir():
            fichiers.extend(lister_python(cible, maximum - len(fichiers)))
        elif cible.is_file():
            fichiers.append(cible)
            explicites.add(cible)
        elif cible.exists():
            raise EntreeInvalide(f"ni fichier ni dossier : {cible}")
        else:
            raise EntreeInvalide(f"chemin introuvable : {cible}")
    return fichiers, explicites


def lire_source(chemin: Path, taille_max: int) -> str:
    """Lit un source Python selon son encodage déclaré ; refuse le binaire."""
    try:
        taille = chemin.stat().st_size
        if taille > taille_max:
            raise EntreeInvalide(f"{chemin} : {taille} octets, au-delà de --taille-max {taille_max}")
        donnees = chemin.read_bytes()
    except OSError as erreur:
        raise EntreeInvalide(f"{chemin} : lecture impossible ({erreur.strerror})") from erreur
    if b"\x00" in donnees:
        raise EntreeInvalide(f"{chemin} : contenu binaire (octet nul), pas un source Python")
    try:
        encodage, _ = tokenize.detect_encoding(io.BytesIO(donnees).readline)
        return donnees.decode(encodage)
    except (SyntaxError, UnicodeDecodeError, LookupError) as erreur:
        raise EntreeInvalide(f"{chemin} : décodage impossible ({erreur})") from erreur


def decouper(source: str, libelle: str) -> list[tokenize.TokenInfo]:
    """Jetons du source ; lève EntreeInvalide si tokenize échoue."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError) as erreur:
        raise EntreeInvalide(f"{libelle} : découpage impossible ({erreur})") from erreur


def debut_de_ligne(precedent: tokenize.TokenInfo | None) -> bool:
    """Vrai si le jeton suivant ouvre une ligne logique."""
    return precedent is None or precedent.type in (tokenize.NEWLINE, tokenize.INDENT,
                                                   tokenize.DEDENT, tokenize.NL)


def jetons_utiles(jetons: list[tokenize.TokenInfo], ignorer_imports: bool) -> list[tokenize.TokenInfo]:
    """Écarte commentaires, docstrings (chaîne seule sur sa ligne) et imports."""
    retenus: list[tokenize.TokenInfo] = []
    precedent: tokenize.TokenInfo | None = None
    a_sauter = False
    for rang, jeton in enumerate(jetons):
        if jeton.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        if a_sauter:
            a_sauter = jeton.type != tokenize.NEWLINE
            continue
        if debut_de_ligne(precedent) and est_chaine_seule(jetons, rang):
            a_sauter = True
            continue
        if ignorer_imports and debut_de_ligne(precedent) and jeton.type == tokenize.NAME \
                and jeton.string in ("import", "from"):
            a_sauter = True
            continue
        retenus.append(jeton)
        precedent = jeton
    return retenus


def est_chaine_seule(jetons: list[tokenize.TokenInfo], rang: int) -> bool:
    """Vrai si une chaîne simple occupe seule sa ligne logique (docstring)."""
    if jetons[rang].type != tokenize.STRING:
        return False
    for suivant in jetons[rang + 1:]:
        if suivant.type in (tokenize.COMMENT, tokenize.NL):
            continue
        return suivant.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
    return True


def valeur_normalisee(jeton: tokenize.TokenInfo, normalisation: str) -> str:
    """Forme du jeton utilisée pour la comparaison."""
    nom = tokenize.tok_name.get(jeton.type, "")
    if jeton.type == tokenize.NAME and not keyword.iskeyword(jeton.string):
        return JETON_IDENTIFIANT if normalisation in ("identifiants", "tout") else jeton.string
    if jeton.type in (tokenize.NUMBER, tokenize.STRING) or nom in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
        return JETON_LITTERAL if normalisation in ("litteraux", "tout") else jeton.string
    if jeton.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
        return f"<{nom}>"
    return jeton.string


def charger_fichier(chemin: Path, libelle: str, taille_max: int, options: Options) -> Fichier:
    """Lit, découpe et normalise un fichier."""
    source = lire_source(chemin, taille_max)
    utiles = jetons_utiles(decouper(source, libelle), options.ignorer_imports)
    valeurs = [options.vocabulaire.setdefault(valeur_normalisee(j, options.normalisation),
                                              len(options.vocabulaire)) for j in utiles]
    return Fichier(libelle, valeurs, [j.start[0] for j in utiles], source.split("\n"))


# --------------------------------------------------------------------------- #
# Empreintes et paires
# --------------------------------------------------------------------------- #

def empreintes(valeurs: list[int], taille: int) -> list[int]:
    """Empreinte roulante de chaque fenêtre de `taille` jetons."""
    if len(valeurs) < taille:
        return []
    puissance = pow(BASE_HACHAGE, taille - 1, MODULO)
    courante = 0
    for valeur in valeurs[:taille]:
        courante = (courante * BASE_HACHAGE + valeur + 1) % MODULO
    resultat = [courante]
    for rang in range(taille, len(valeurs)):
        sortant = (valeurs[rang - taille] + 1) * puissance % MODULO
        courante = ((courante - sortant) * BASE_HACHAGE + valeurs[rang] + 1) % MODULO
        resultat.append(courante)
    return resultat


def regrouper(fichiers: list[Fichier], taille: int) -> list[list[tuple[int, int]]]:
    """Groupes de fenêtres d'empreinte égale (au moins deux occurrences).

    Une fenêtre vue une seule fois est rangée comme un entier (fichier << 32 | rang),
    pas comme une liste : c'est le cas de presque toutes, et la mémoire en dépend.
    """
    seaux: dict[int, int | list[int]] = {}
    for rang_fichier, fichier in enumerate(fichiers):
        for position, empreinte in enumerate(empreintes(fichier.valeurs, taille)):
            code = (rang_fichier << 32) | position
            present = seaux.get(empreinte)
            if present is None:
                seaux[empreinte] = code
            elif isinstance(present, list):
                present.append(code)
            else:
                seaux[empreinte] = [present, code]
    return [[(c >> 32, c & 0xFFFFFFFF) for c in groupe]
            for groupe in seaux.values() if isinstance(groupe, list)]


def couples_du_groupe(groupe: list[tuple[int, int]]) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Toutes les paires d'un petit groupe ; une chaîne de voisins pour un grand."""
    if len(groupe) <= GROUPE_MAX_TOUTES_PAIRES:
        return [(groupe[i], groupe[j]) for i in range(len(groupe)) for j in range(i + 1, len(groupe))]
    return list(zip(groupe, groupe[1:]))


@dataclass(frozen=True)
class Bornes:
    """Identifiants des jetons qui délimitent les lignes logiques."""

    fin_logique: int | None
    separateurs: frozenset[int]


def debut_logique(valeurs: list[int], rang: int, bornes: Bornes) -> bool:
    """Vrai si le jeton de ce rang ouvre une ligne logique."""
    return rang == 0 or valeurs[rang - 1] in bornes.separateurs


def aligner(va: list[int], vb: list[int], i: int, j: int, longueur: int, bornes: Bornes) -> tuple[int, int]:
    """Recadre un bloc sur des lignes logiques entières : (décalage du début, nouvelle longueur)."""
    decalage = 0
    while decalage < longueur and not (debut_logique(va, i + decalage, bornes)
                                       and debut_logique(vb, j + decalage, bornes)):
        decalage += 1
    fin = longueur
    while fin > decalage and va[i + fin - 1] != bornes.fin_logique:
        fin -= 1
    return decalage, fin - decalage


def etendre(fichiers: list[Fichier], a: tuple[int, int], b: tuple[int, int], taille: int,
            bornes: Bornes) -> Paire | None:
    """Vérifie la fenêtre, exige la maximalité à gauche, étend à droite, recadre sur des lignes entières."""
    va, vb = fichiers[a[0]].valeurs, fichiers[b[0]].valeurs
    i, j = a[1], b[1]
    if va[i:i + taille] != vb[j:j + taille]:
        return None
    if i > 0 and j > 0 and va[i - 1] == vb[j - 1]:
        return None
    longueur = taille
    while i + longueur < len(va) and j + longueur < len(vb) and va[i + longueur] == vb[j + longueur]:
        longueur += 1
    if a[0] == b[0]:
        longueur = min(longueur, abs(j - i))
    decalage, longueur = aligner(va, vb, i, j, longueur, bornes)
    if longueur < taille:
        return None
    return Paire(a[0], i + decalage, b[0], j + decalage, longueur)


def lignes_couvertes(fichier: Fichier, debut: int, longueur: int) -> set[int]:
    """Lignes de code touchées par un bloc de jetons."""
    return set(fichier.lignes[debut:debut + longueur])


def trouver_paires(fichiers: list[Fichier], options: Options) -> list[Paire]:
    """Paires maximales retenues selon les seuils de jetons et de lignes."""
    paires: set[Paire] = set()
    vocabulaire = options.vocabulaire
    bornes = Bornes(vocabulaire.get(JETON_FIN_LOGIQUE),
                    frozenset(vocabulaire[j] for j in JETONS_BORNES if j in vocabulaire))
    for groupe in regrouper(fichiers, options.min_jetons):
        for a, b in couples_du_groupe(groupe):
            paire = etendre(fichiers, a, b, options.min_jetons, bornes)
            if paire is None:
                continue
            lignes_a = lignes_couvertes(fichiers[paire.fichier_a], paire.debut_a, paire.longueur)
            lignes_b = lignes_couvertes(fichiers[paire.fichier_b], paire.debut_b, paire.longueur)
            if min(len(lignes_a), len(lignes_b)) >= options.min_lignes:
                paires.add(paire)
    return sorted(paires, key=lambda p: (-p.longueur, p.fichier_a, p.debut_a, p.fichier_b, p.debut_b))


def taux_duplication(fichiers: list[Fichier], paires: list[Paire]) -> tuple[int, int]:
    """(lignes de code couvertes par une duplication, lignes de code examinées)."""
    couvertes: set[tuple[int, int]] = set()
    for paire in paires:
        for rang, debut in ((paire.fichier_a, paire.debut_a), (paire.fichier_b, paire.debut_b)):
            couvertes.update((rang, ligne) for ligne in lignes_couvertes(fichiers[rang], debut, paire.longueur))
    total = sum(len(set(f.lignes)) for f in fichiers)
    return len(couvertes), total


def occurrence(fichier: Fichier, debut: int, longueur: int) -> dict[str, object]:
    """Emplacement d'une occurrence."""
    lignes = fichier.lignes[debut:debut + longueur]
    return {"chemin": fichier.libelle, "ligne_debut": lignes[0], "ligne_fin": lignes[-1],
            "lignes_de_code": len(set(lignes))}


def paire_vers_dict(fichiers: list[Fichier], paire: Paire) -> dict[str, object]:
    """Sérialise une paire avec un extrait de la première ligne."""
    a = occurrence(fichiers[paire.fichier_a], paire.debut_a, paire.longueur)
    fichier = fichiers[paire.fichier_a]
    extrait = fichier.texte[int(a["ligne_debut"]) - 1].strip()[:100]
    return {"jetons": paire.longueur, "a": a,
            "b": occurrence(fichiers[paire.fichier_b], paire.debut_b, paire.longueur),
            "extrait": extrait}


# --------------------------------------------------------------------------- #
# Recoupement optionnel avec pylint (symilar)
# --------------------------------------------------------------------------- #

def blocs_symilar(fichiers: list[Fichier], min_lignes: int) -> list[tuple[str, int, int, str, int, int]] | None:
    """Blocs de symilar (pylint) entre fichiers différents, ou None si pylint manque."""
    try:
        from pylint.checkers.symilar import Symilar
    except ImportError:
        print("pylint absent : recoupement symilar omis, détection stdlib seule", file=sys.stderr)
        return None
    detecteur = Symilar(min_lines=min_lignes, ignore_comments=True, ignore_docstrings=True,
                        ignore_imports=True, ignore_signatures=False)
    for fichier in fichiers:
        detecteur.append_stream(fichier.libelle, io.StringIO("\n".join(fichier.texte)))
    blocs = []
    try:
        with contextlib.redirect_stdout(sys.stderr):
            for c in detecteur._iter_sims():
                blocs.append((c.fst_lset.name, c.fst_file_start + 1, c.fst_file_end,
                              c.snd_lset.name, c.snd_file_start + 1, c.snd_file_end))
    except AttributeError as erreur:
        print(f"symilar : API interne changée ({erreur}), recoupement omis", file=sys.stderr)
        return None
    return blocs


def recouvre(occ: dict[str, object], chemin: str, debut: int, fin: int) -> bool:
    """Vrai si une occurrence de l'outil chevauche l'intervalle de lignes donné."""
    return occ["chemin"] == chemin and int(occ["ligne_debut"]) <= fin and int(occ["ligne_fin"]) >= debut


def bloc_dans_paire(paires: list[dict[str, object]], bloc: tuple[str, int, int, str, int, int]) -> bool:
    """Vrai si une même paire de l'outil chevauche les deux côtés du bloc symilar."""
    na, da, fa, nb, db, fb = bloc
    return any((recouvre(p["a"], na, da, fa) and recouvre(p["b"], nb, db, fb))
               or (recouvre(p["a"], nb, db, fb) and recouvre(p["b"], na, da, fa)) for p in paires)


def recouper_symilar(paires: list[dict[str, object]], blocs: list[tuple[str, int, int, str, int, int]]
                     ) -> dict[str, int]:
    """Blocs symilar retrouvés : dans une même paire, ou côtés marqués dupliqués par l'outil."""
    occurrences = [p[cle] for p in paires for cle in ("a", "b")]
    meme_paire = cotes = 0
    for bloc in blocs:
        if bloc_dans_paire(paires, bloc):
            meme_paire += 1
        if all(any(recouvre(o, nom, debut, fin) for o in occurrences)
               for nom, debut, fin in (bloc[:3], bloc[3:])):
            cotes += 1
    return {"blocs_symilar": len(blocs), "retrouves_dans_une_meme_paire": meme_paire,
            "retrouves_les_deux_cotes_marques": cotes,
            "paires_outil_entre_fichiers": sum(1 for p in paires if p["a"]["chemin"] != p["b"]["chemin"])}


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #

def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait les intitulés du contrat de mesure depuis la docstring."""
    contrat: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if ligne[:1].strip() and tete in INTITULES:
            courant = tete
            contrat[courant] = []
        elif courant is not None and tete:
            contrat[courant].append(tete)
    return {cle: " ".join(valeur) for cle, valeur in contrat.items()}


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description=("Détecte les blocs de code Python copiés-collés (empreintes de fenêtres de "
                     "jetons) et mesure le taux de duplication."),
        epilog=(f"Exemple : python {RACINE / 'detecter_code_duplique.py'} src/ --min-lignes 6 "
                "--normaliser identifiants --json\n"
                "Codes : 0 aucune duplication (ou taux sous --taux-max), 1 duplication trouvée, "
                "2 entrée invalide, 3 rien à examiner."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="+", type=Path, help="fichiers .py ou dossiers")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="base des chemins relatifs et de l'affichage (défaut : dossier courant)")
    parseur.add_argument("--min-jetons", type=int, default=50, help="taille minimale d'un bloc en jetons (50)")
    parseur.add_argument("--min-lignes", type=int, default=5, help="lignes de code minimales par occurrence (5)")
    parseur.add_argument("--normaliser", choices=NORMALISATIONS, default="aucune",
                         help="remplacer identifiants et/ou littéraux avant comparaison")
    parseur.add_argument("--garder-imports", action="store_true", help="ne pas écarter les lignes d'import")
    parseur.add_argument("--taux-max", type=float, default=None,
                         help="code 1 seulement si le taux de lignes dupliquées (en %%) dépasse ce seuil")
    parseur.add_argument("--comparer-symilar", action="store_true",
                         help="recouper avec pylint symilar s'il est installé")
    parseur.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="octets maximum par fichier")
    parseur.add_argument("--max-fichiers", type=int, default=MAX_FICHIERS_DEFAUT, help="nombre maximum de fichiers")
    return parseur


def libelle_chemin(chemin: Path, base: Path) -> str:
    """Chemin affiché, relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def charger_tous(fichiers: list[Path], explicites: set[Path], base: Path, taille_max: int,
                 options: Options) -> tuple[list[Fichier], list[dict[str, str]]]:
    """Charge chaque fichier ; un fichier explicite illisible est une entrée invalide."""
    charges: list[Fichier] = []
    rejets: list[dict[str, str]] = []
    for chemin in fichiers:
        libelle = libelle_chemin(chemin, base)
        try:
            charges.append(charger_fichier(chemin, libelle, taille_max, options))
        except EntreeInvalide as erreur:
            if chemin in explicites:
                raise
            print(f"non examiné : {erreur}", file=sys.stderr)
            rejets.append({"chemin": libelle, "raison": str(erreur)})
    return charges, rejets


def verifier_seuils(args: argparse.Namespace) -> None:
    """Refuse des seuils absurdes."""
    if args.min_jetons < 5 or args.min_lignes < 1:
        raise EntreeInvalide("--min-jetons doit valoir au moins 5 et --min-lignes au moins 1")
    if args.taux_max is not None and not 0 <= args.taux_max <= 100:
        raise EntreeInvalide("--taux-max est un pourcentage entre 0 et 100")


def construire_sortie(options: Options, fichiers: list[Fichier],
                      rejets: list[dict[str, str]], paires: list[Paire]) -> dict[str, object]:
    """Objet résultat complet."""
    dupliquees, total = taux_duplication(fichiers, paires)
    taux = round(100.0 * dupliquees / total, 2) if total else 0.0
    examines = [f.libelle for f in fichiers]
    return {
        "outil": "detecter_code_duplique",
        "moteur": "stdlib",
        "denominateur": len(fichiers),
        "examines": examines[:MAX_EXAMINES],
        "examines_tronques": len(examines) > MAX_EXAMINES,
        "non_examines": rejets,
        "reglages": {"min_jetons": options.min_jetons, "min_lignes": options.min_lignes,
                     "normalisation": options.normalisation, "imports_ecartes": options.ignorer_imports},
        "jetons_examines": sum(len(f.valeurs) for f in fichiers),
        "lignes_de_code": total,
        "lignes_dupliquees": dupliquees,
        "taux_duplication_pourcent": taux,
        "nombre_paires": len(paires),
        "paires": [paire_vers_dict(fichiers, p) for p in paires[:MAX_PAIRES_JSON]],
        "paires_tronquees": len(paires) > MAX_PAIRES_JSON,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def afficher_humain(sortie: dict[str, object]) -> None:
    """Affichage lisible par un humain."""
    print(f"detecter_code_duplique — {sortie['denominateur']} fichier(s), "
          f"{sortie['lignes_de_code']} lignes de code, {sortie['nombre_paires']} paire(s) de blocs, "
          f"taux {sortie['taux_duplication_pourcent']} % (moteur {sortie['moteur']})")
    for paire in sortie["paires"][:50]:
        a, b = paire["a"], paire["b"]
        print(f"  {paire['jetons']} jetons : {a['chemin']}:{a['ligne_debut']}-{a['ligne_fin']}"
              f"  ==  {b['chemin']}:{b['ligne_debut']}-{b['ligne_fin']}")
        print(f"      {paire['extrait']}")
    if int(sortie["nombre_paires"]) > 50:
        print(f"  … {int(sortie['nombre_paires']) - 50} autre(s) paire(s), voir --json")
    if "symilar" in sortie:
        print(f"Recoupement symilar : {sortie['symilar']}")


def afficher_json(sortie: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    base = args.racine or Path.cwd()
    options = Options(args.min_jetons, args.min_lignes, args.normaliser, not args.garder_imports)
    try:
        verifier_seuils(args)
        chemins, explicites = collecter_cibles([c if c.is_absolute() else base / c for c in args.chemins],
                                               max(1, args.max_fichiers))
        fichiers, rejets = charger_tous(chemins, explicites, base, args.taille_max, options)
    except EntreeInvalide as erreur:
        print(f"entrée invalide : {erreur}", file=sys.stderr)
        return 2
    paires = trouver_paires(fichiers, options)
    sortie = construire_sortie(options, fichiers, rejets, paires)
    if args.comparer_symilar:
        blocs = blocs_symilar(fichiers, options.min_lignes)
        if blocs is not None:
            sortie["moteur"] = "stdlib+symilar"
            sortie["symilar"] = recouper_symilar([paire_vers_dict(fichiers, p) for p in paires], blocs)
    if args.json:
        afficher_json(sortie)
    if not fichiers:
        print("dénominateur nul : aucun fichier Python examinable, rien à examiner", file=sys.stderr)
        return 3
    if not args.json:
        afficher_humain(sortie)
    if args.taux_max is not None:
        return 1 if float(sortie["taux_duplication_pourcent"]) > args.taux_max else 0
    return 1 if paires else 0


if __name__ == "__main__":
    raise SystemExit(main())
