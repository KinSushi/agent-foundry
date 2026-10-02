"""Applique la sémantique gitignore complète à un arbre et dit, pour chaque entrée, quelle règle décide.

Pourquoi : un agent approxime gitignore par fnmatch, qui laisse « * » traverser les « / ».
Mesuré le 2026-10-02 : avec un .gitignore réduit à « doc/*.txt »,
``git check-ignore -v doc/notes.txt doc/server/arch.txt`` n'ignore qu'un fichier sur 2
(doc/notes.txt), alors que ``fnmatch.fnmatch('doc/server/arch.txt', 'doc/*.txt')`` rend True.

QUESTION
    Quels fichiers de cet arbre sont ignorés, et par quelle règle exactement (fichier:ligne) ?
MESURE
    Parcours de l'arbre comme le fait git : .gitignore imbriqués (le plus profond l'emporte),
    puis .git/info/exclude, puis un fichier d'exclusion explicite (--exclure-fichier) ;
    dernière règle correspondante gagnante ; négation « ! » ; « / » final réservé aux
    dossiers ; ancrage dès qu'un « / » figure au début ou au milieu ; « ** » ; échappements
    « \\ » ; espaces finaux ; un dossier exclu n'est pas parcouru et rien de ce qu'il contient
    ne peut être ré-inclus. Le moteur de motifs est un portage de wildmatch.c et de
    match_basename/match_pathname de git (comparaison octet par octet). Les négations
    rendues inopérantes par un dossier parent exclu sont recherchées et signalées.
    Si pathspec est installé, sa décision est calculée sur les mêmes fichiers et les écarts
    sont rapportés (pathspec ne fait pas foi).
HYPOTHÈSES
    Le dossier donné est la racine du dépôt (ses parents ne sont pas lus). Sensibilité à la
    casse comme core.ignorecase=false, sauf --insensible-casse. Les noms de fichiers sont
    comparés comme des octets (un « ? » vaut un octet, pas un caractère accentué).
LIMITES
    Ne lit pas l'index git : un fichier déjà suivi reste suivi même s'il correspond à une
    règle. Ne lit ni core.excludesFile de l'utilisateur ni ~/.config/git/ignore (passer le
    fichier par --exclure-fichier). Un .git qui est un fichier (sous-module, worktree) n'est
    pas suivi vers son vrai dossier. Les sous-dépôts (dossier contenant .git) ne sont pas
    parcourus. Parcours borné par --max-entrees.
CONTRE-EXEMPLES
    Un fichier suivi par git et correspondant à « *.log » est déclaré « ignoré » alors que
    git continue de le suivre (git check-ignore --no-index dit pareil, git status non).
    Constaté sur un dépôt témoin : « logs/ » puis « !logs/garde.txt » ; pathspec 1.1.1
    déclare logs/garde.txt ré-inclus, git et cet outil le déclarent ignoré.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Dépôts git sur système de fichiers sensible à la casse ; revue de .gitignore avant
    commit, fuite de secrets (.env non ignoré), règles « ! » sans effet.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import pathspec
except ImportError:
    pathspec = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
             INTITULE_INVOCATION, "DOMAINE")

MAX_EXAMINES = 200
NOM_GITIGNORE = b".gitignore"
NOM_GIT = b".git"

WM_CASEFOLD = 1
WM_PATHNAME = 2
WM_MATCH = 0
WM_NOMATCH = 1
WM_ABORT_ALL = -1
WM_ABORT_TO_STARSTAR = -2

ETOILE, INTERRO, CROCHET, FERME, ANTISLASH, SLASH = (ord(c) for c in "*?[]\\/")
SPECIAUX_GLOB = frozenset(b"*?[\\")
PONCTUATION = frozenset(b"!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


class EntreeInvalide(Exception):
    """Dossier absent, fichier à la place d'un dossier, option incohérente (code 2)."""


@dataclass(frozen=True)
class Motif:
    """Une ligne de motif analysée comme le fait parse_path_pattern() de git."""

    source: str
    ligne: int
    texte: str
    motif: bytes
    base: bytes
    negatif: bool
    dossier_seul: bool
    sans_slash: bool
    prefixe: int


@dataclass
class FichierMotifs:
    """Un fichier de règles lu (.gitignore, info/exclude ou fichier explicite)."""

    source: str
    base: bytes
    motifs: list[Motif]
    lignes: list[str]
    spec: Any = None


@dataclass
class Bilan:
    """Accumulateur du parcours : décisions, négations sans effet, compteurs par règle."""

    entrees: list[dict[str, Any]] = field(default_factory=list)
    sans_effet: list[dict[str, Any]] = field(default_factory=list)
    fichiers_regles: list[FichierMotifs] = field(default_factory=list)
    usages: dict[tuple[str, int], int] = field(default_factory=dict)
    alertes: list[str] = field(default_factory=list)
    ecarts: list[dict[str, Any]] = field(default_factory=list)
    compares: int = 0
    visites: int = 0
    tronque: bool = False


@dataclass(frozen=True)
class Options:
    """Réglages du parcours, passés partout plutôt qu'en état global."""

    casse: bool
    deplier: bool
    max_entrees: int
    comparer: bool


# --------------------------------------------------------------------------- contrat


def extraire_contrat(doc: str) -> dict[str, str]:
    """Découpe la docstring du module en sections selon les intitulés du socle."""
    contrat: dict[str, str] = {}
    courant = "POURQUOI"
    lignes: list[str] = []
    for ligne in doc.splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            contrat[courant] = " ".join(x.strip() for x in lignes if x.strip())
            courant, lignes = ligne.strip(), []
        else:
            lignes.append(ligne)
    contrat[courant] = " ".join(x.strip() for x in lignes if x.strip())
    return contrat


# --------------------------------------------------------------------------- wildmatch


def _octet(chaine: bytes, i: int) -> int:
    """Octet à la position i, ou 0 au-delà de la fin (le NUL terminal du C)."""
    return chaine[i] if i < len(chaine) else 0


def _minuscule(c: int, drapeaux: int) -> int:
    """tolower() ASCII si WM_CASEFOLD."""
    return c + 32 if drapeaux & WM_CASEFOLD and 65 <= c <= 90 else c


def _classe_posix(nom: bytes, c: int, drapeaux: int) -> bool | None:
    """[:classe:] de wildmatch (ASCII seul) ; None si la classe est inconnue."""
    tests = {
        b"alnum": lambda x: 48 <= x <= 57 or 65 <= x <= 90 or 97 <= x <= 122,
        b"alpha": lambda x: 65 <= x <= 90 or 97 <= x <= 122,
        b"blank": lambda x: x in (32, 9),
        b"cntrl": lambda x: x < 32 or x == 127,
        b"digit": lambda x: 48 <= x <= 57,
        b"graph": lambda x: 33 <= x <= 126,
        b"lower": lambda x: 97 <= x <= 122,
        b"print": lambda x: 32 <= x <= 126,
        b"punct": lambda x: x in PONCTUATION,
        b"space": lambda x: x in (32, 9, 10, 13),
        b"upper": lambda x: 65 <= x <= 90 or bool(drapeaux & WM_CASEFOLD and 97 <= x <= 122),
        b"xdigit": lambda x: 48 <= x <= 57 or 65 <= x <= 70 or 97 <= x <= 102,
    }
    test = tests.get(nom)
    return None if test is None else test(c)


def _crochet(p: bytes, pi: int, t_ch: int, drapeaux: int) -> tuple[int, int]:
    """Classe [..] : rend (résultat, index du « ] » fermant) ; port fidèle de wildmatch.c."""
    pi += 1
    p_ch = _octet(p, pi)
    if p_ch == ord("^"):
        p_ch = ord("!")
    negation = p_ch == ord("!")
    if negation:
        pi += 1
        p_ch = _octet(p, pi)
    precedent, trouve = 0, False
    while True:
        if not p_ch:
            return WM_ABORT_ALL, pi
        if p_ch == ANTISLASH:
            pi += 1
            p_ch = _octet(p, pi)
            if not p_ch:
                return WM_ABORT_ALL, pi
            trouve = trouve or t_ch == p_ch
        elif p_ch == ord("-") and precedent and _octet(p, pi + 1) and _octet(p, pi + 1) != FERME:
            pi += 1
            p_ch = _octet(p, pi)
            if p_ch == ANTISLASH:
                pi += 1
                p_ch = _octet(p, pi)
                if not p_ch:
                    return WM_ABORT_ALL, pi
            haut = t_ch - 32 if drapeaux & WM_CASEFOLD and 97 <= t_ch <= 122 else t_ch
            trouve = trouve or precedent <= t_ch <= p_ch or precedent <= haut <= p_ch
            p_ch = 0
        elif p_ch == CROCHET and _octet(p, pi + 1) == ord(":"):
            debut = pi + 2
            fin = debut
            while _octet(p, fin) and _octet(p, fin) != FERME:
                fin += 1
            if not _octet(p, fin):
                return WM_ABORT_ALL, fin
            if fin - debut - 1 < 0 or _octet(p, fin - 1) != ord(":"):
                pi, p_ch = debut - 2, CROCHET
                trouve = trouve or t_ch == CROCHET
            else:
                verdict = _classe_posix(p[debut:fin - 1], t_ch, drapeaux)
                if verdict is None:
                    return WM_ABORT_ALL, fin
                trouve, pi, p_ch = trouve or verdict, fin, 0
        elif t_ch == p_ch:
            trouve = True
        precedent = p_ch
        pi += 1
        p_ch = _octet(p, pi)
        if p_ch == FERME:
            break
    if trouve == negation or (drapeaux & WM_PATHNAME and t_ch == SLASH):
        return WM_NOMATCH, pi
    return WM_MATCH, pi


def _etoiles(p: bytes, pi: int, t: bytes, ti: int, debut: int, drapeaux: int) -> int | tuple[int, int]:
    """Cas « * » et « ** » de dowild() ; rend un verdict, ou (pi, ti) pour continuer."""
    pi += 1
    if _octet(p, pi) == ETOILE:
        precedent = pi - 2
        while _octet(p, pi) == ETOILE:
            pi += 1
        frontiere = (precedent < debut or p[precedent] == SLASH) and (
            _octet(p, pi) in (0, SLASH) or (_octet(p, pi) == ANTISLASH and _octet(p, pi + 1) == SLASH))
        if frontiere and _octet(p, pi) == SLASH and _dowild(p, pi + 1, t, ti, drapeaux) == WM_MATCH:
            return WM_MATCH
        traverse = frontiere
    else:
        traverse = not drapeaux & WM_PATHNAME
    if not _octet(p, pi):
        return WM_NOMATCH if not traverse and SLASH in t[ti:] else WM_MATCH
    if not traverse and _octet(p, pi) == SLASH:
        barre = t.find(b"/", ti)
        return WM_NOMATCH if barre < 0 else (pi + 1, barre + 1)
    return _etoile_boucle(p, pi, t, ti, drapeaux, traverse)


def _etoile_boucle(p: bytes, pi: int, t: bytes, ti: int, drapeaux: int, traverse: bool) -> int:
    """Boucle d'essais successifs de « * » (avance rapide sur un littéral, comme git)."""
    t_ch = _minuscule(_octet(t, ti), drapeaux)
    while t_ch:
        if _octet(p, pi) not in SPECIAUX_GLOB:
            p_ch = _minuscule(_octet(p, pi), drapeaux)
            while True:
                t_ch = _minuscule(_octet(t, ti), drapeaux)
                if not t_ch or (not traverse and t_ch == SLASH) or t_ch == p_ch:
                    break
                ti += 1
            if t_ch != p_ch:
                return WM_NOMATCH
        resultat = _dowild(p, pi, t, ti, drapeaux)
        if resultat != WM_NOMATCH:
            if not traverse or resultat != WM_ABORT_TO_STARSTAR:
                return resultat
        elif not traverse and t_ch == SLASH:
            return WM_ABORT_TO_STARSTAR
        ti += 1
        t_ch = _minuscule(_octet(t, ti), drapeaux)
    return WM_ABORT_ALL


def _dowild(p: bytes, pi: int, t: bytes, ti: int, drapeaux: int) -> int:
    """Port de dowild() (git, wildmatch.c) sur des octets ; debut = pi pour « ** »."""
    debut = pi
    while _octet(p, pi):
        p_ch = _minuscule(_octet(p, pi), drapeaux)
        t_ch = _minuscule(_octet(t, ti), drapeaux)
        if not t_ch and p_ch != ETOILE:
            return WM_ABORT_ALL
        if p_ch == ANTISLASH:
            pi += 1
            if t_ch != _octet(p, pi):
                return WM_NOMATCH
        elif p_ch == INTERRO:
            if drapeaux & WM_PATHNAME and t_ch == SLASH:
                return WM_NOMATCH
        elif p_ch == ETOILE:
            suite = _etoiles(p, pi, t, ti, debut, drapeaux)
            if isinstance(suite, int):
                return suite
            pi, ti = suite
            continue
        elif p_ch == CROCHET:
            resultat, pi = _crochet(p, pi, t_ch, drapeaux)
            if resultat != WM_MATCH:
                return resultat
        elif t_ch != p_ch:
            return WM_NOMATCH
        pi += 1
        ti += 1
    return WM_NOMATCH if _octet(t, ti) else WM_MATCH


def wildmatch(motif: bytes, texte: bytes, drapeaux: int) -> bool:
    """Vrai si le motif correspond au texte entier (sémantique git)."""
    return _dowild(motif, 0, texte, 0, drapeaux) == WM_MATCH


# --------------------------------------------------------------------------- motifs


def longueur_simple(motif: bytes) -> int:
    """simple_length() de git : longueur avant le premier caractère spécial de glob."""
    for i, c in enumerate(motif):
        if c in SPECIAUX_GLOB:
            return i
    return len(motif)


def rogner_espaces(ligne: bytes) -> bytes:
    """trim_trailing_spaces() de git : espaces finaux retirés sauf s'ils sont échappés."""
    dernier, i = None, 0
    while i < len(ligne):
        if ligne[i] == 32:
            dernier = i if dernier is None else dernier
        elif ligne[i] == ANTISLASH:
            i += 1
            if i >= len(ligne):
                return ligne
            dernier = None
        else:
            dernier = None
        i += 1
    return ligne if dernier is None else ligne[:dernier]


def analyser_motif(brut: bytes, source: str, ligne: int, base: bytes) -> Motif:
    """parse_path_pattern() de git : négation, « / » final, présence d'un « / »."""
    texte = brut.decode("utf-8", "replace")
    negatif = brut.startswith(b"!")
    corps = brut[1:] if negatif else brut
    longueur = len(corps)
    dossier_seul = longueur > 0 and corps[-1] == SLASH
    if dossier_seul:
        longueur -= 1
    return Motif(source, ligne, texte, corps[:longueur], base, negatif, dossier_seul,
                 SLASH not in corps[:longueur], min(longueur_simple(corps), longueur))


def lire_regles(donnees: bytes, source: str, base: bytes) -> FichierMotifs:
    """add_patterns_from_buffer() de git : BOM, commentaires, CRLF, NUL, numéros de ligne."""
    if donnees.startswith(b"\xef\xbb\xbf"):
        donnees = donnees[3:]
    motifs, lignes = [], []
    for numero, brute in enumerate(donnees.split(b"\n"), start=1):
        lignes.append(brute.decode("utf-8", "replace").rstrip("\r"))
        if not brute or brute.startswith(b"#"):
            continue
        if brute.endswith(b"\r"):
            brute = brute[:-1]
        brute = rogner_espaces(brute.split(b"\x00", 1)[0])
        motifs.append(analyser_motif(brute, source, numero, base))
    if lignes and lignes[-1] == "" and donnees.endswith(b"\n"):
        lignes.pop()
    return FichierMotifs(source, base, motifs, lignes)


def egal(a: bytes, b: bytes, casse: bool) -> bool:
    """fspathncmp() : égalité d'octets, insensible à la casse ASCII si demandé."""
    return a.lower() == b.lower() if casse else a == b


def correspond_nom(m: Motif, nom: bytes, casse: bool) -> bool:
    """match_basename() de git (motif sans « / »)."""
    if m.prefixe == len(m.motif):
        return egal(m.motif, nom, casse)
    return wildmatch(m.motif, nom, WM_CASEFOLD if casse else 0)


def correspond_chemin(m: Motif, chemin: bytes, casse: bool) -> bool:
    """match_pathname() de git : ancré sur le dossier du fichier de règles."""
    motif, prefixe = m.motif, m.prefixe
    if motif.startswith(b"/"):
        motif, prefixe = motif[1:], prefixe - 1
    base = m.base[:-1] if m.base else b""
    if len(chemin) < len(base) + 1 or (base and chemin[len(base)] != SLASH):
        return False
    if not egal(chemin[:len(base)], base, casse):
        return False
    nom = chemin[len(base) + 1:] if base else chemin
    if prefixe > 0:
        if prefixe > len(nom) or not egal(motif[:prefixe], nom[:prefixe], casse):
            return False
        motif, nom = motif[prefixe:], nom[prefixe:]
        if not motif and not nom:
            return True
    return wildmatch(motif, nom, WM_PATHNAME | (WM_CASEFOLD if casse else 0))


def correspond(m: Motif, chemin: bytes, est_dossier: bool, casse: bool) -> bool:
    """last_matching_pattern_from_list(), pour un seul motif."""
    if m.dossier_seul and not est_dossier:
        return False
    if m.sans_slash:
        return correspond_nom(m, chemin.rsplit(b"/", 1)[-1], casse)
    return correspond_chemin(m, chemin, casse)


def derniere_regle(chemin: bytes, est_dossier: bool, piles: Sequence[FichierMotifs],
                   casse: bool) -> Motif | None:
    """La règle qui décide : fichier le plus prioritaire d'abord, dernière ligne d'abord."""
    for fichier in reversed(piles):
        for m in reversed(fichier.motifs):
            if correspond(m, chemin, est_dossier, casse):
                return m
    return None


# --------------------------------------------------------------------------- lecture


def lire_fichier_regles(chemin: Path, source: str, base: bytes, bilan: Bilan) -> FichierMotifs | None:
    """Lit un fichier de règles régulier ; un lien symbolique est refusé comme le fait git."""
    try:
        etat = chemin.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(etat.st_mode):
        bilan.alertes.append(f"{source} est un lien symbolique : ignoré (git ne le suit pas)")
        return None
    if not stat.S_ISREG(etat.st_mode):
        return None
    try:
        fichier = lire_regles(chemin.read_bytes(), source, base)
    except OSError as exc:
        bilan.alertes.append(f"{source} illisible : {exc.strerror or exc}")
        return None
    bilan.fichiers_regles.append(fichier)
    return fichier


def piles_initiales(racine: Path, exclusions: Sequence[Path], bilan: Bilan) -> list[FichierMotifs]:
    """Ordre de priorité croissante : --exclure-fichier, .git/info/exclude, .gitignore racine."""
    piles = []
    for chemin in exclusions:
        fichier = lire_fichier_regles(chemin, str(chemin), b"", bilan)
        if fichier is None:
            raise EntreeInvalide(f"--exclure-fichier « {chemin} » absent ou illisible")
        piles.append(fichier)
    if (racine / ".git").is_dir():
        fichier = lire_fichier_regles(racine / ".git" / "info" / "exclude", ".git/info/exclude",
                                      b"", bilan)
        piles.extend([fichier] if fichier else [])
    racine_gi = lire_fichier_regles(racine / ".gitignore", ".gitignore", b"", bilan)
    return piles + ([racine_gi] if racine_gi else [])


# --------------------------------------------------------------------------- parcours


def lister(dossier: bytes, bilan: Bilan) -> list[tuple[bytes, bool]]:
    """Entrées d'un dossier triées, (nom, est_dossier) sans suivre les liens."""
    try:
        with os.scandir(dossier) as it:
            return sorted((e.name, e.is_dir(follow_symlinks=False)) for e in it)
    except OSError as exc:
        bilan.alertes.append(f"{os.fsdecode(dossier)} illisible : {exc.strerror or exc}")
        return []


def afficher_chemin(chemin: bytes, est_dossier: bool) -> str:
    """Chemin relatif lisible, « / » final pour un dossier."""
    return os.fsdecode(chemin) + ("/" if est_dossier else "")


def decrire_regle(m: Motif | None) -> dict[str, Any] | None:
    """Règle sous forme JSON : fichier, ligne, texte, négation."""
    if m is None:
        return None
    return {"fichier": m.source, "ligne": m.ligne, "motif": m.texte, "negation": m.negatif}


def consigner(bilan: Bilan, chemin: bytes, est_dossier: bool, regle: Motif | None,
              via: Motif | None = None) -> None:
    """Enregistre une décision et compte l'usage de la règle."""
    decisive = via or regle
    if decisive is not None:
        cle = (decisive.source, decisive.ligne)
        bilan.usages[cle] = bilan.usages.get(cle, 0) + 1
    ignore = via is not None or (regle is not None and not regle.negatif)
    bilan.entrees.append({
        "chemin": afficher_chemin(chemin, est_dossier),
        "type": "dossier" if est_dossier else "fichier",
        "ignore": ignore,
        "decision": "ignoré" if ignore else ("ré-inclus" if regle else "aucune règle"),
        "regle": decrire_regle(decisive),
        "via_dossier": via is not None,
    })


def parcourir(racine: Path, piles: list[FichierMotifs], opts: Options, bilan: Bilan) -> None:
    """Parcours en profondeur, itératif, à la manière de read_directory_recursive()."""
    pile: list[tuple[bytes, list[FichierMotifs]]] = [(b"", piles)]
    base_abs = os.fsencode(racine)
    while pile:
        relatif, actives = pile.pop()
        absolu = os.path.join(base_abs, relatif) if relatif else base_abs
        enfants = []
        for nom, est_dossier in lister(absolu, bilan):
            if len(bilan.entrees) + bilan.visites >= opts.max_entrees:
                bilan.tronque = True
                return
            if nom == NOM_GIT:
                continue
            chemin = relatif + nom
            suivre = traiter_entree(racine, chemin, est_dossier, actives, opts, bilan)
            if suivre is not None:
                enfants.append((chemin + b"/", suivre))
        pile.extend(reversed(enfants))


def traiter_entree(racine: Path, chemin: bytes, est_dossier: bool, actives: list[FichierMotifs],
                   opts: Options, bilan: Bilan) -> list[FichierMotifs] | None:
    """Décide une entrée ; rend les règles à appliquer dans le dossier s'il faut y descendre."""
    regle = derniere_regle(chemin, est_dossier, actives, opts.casse)
    consigner(bilan, chemin, est_dossier, regle)
    if not est_dossier and opts.comparer:
        comparer_pathspec(chemin, regle, actives, bilan)
    if not est_dossier:
        return None
    if regle is not None and not regle.negatif:
        explorer_exclu(racine, chemin, regle, actives, opts, bilan)
        return None
    dossier = racine / os.fsdecode(chemin)
    if (dossier / ".git").exists():
        bilan.alertes.append(f"{afficher_chemin(chemin, True)} contient .git : sous-dépôt non parcouru")
        return None
    source = os.fsdecode(chemin) + "/.gitignore"
    propre = lire_fichier_regles(dossier / ".gitignore", source, chemin + b"/", bilan)
    return actives + [propre] if propre else actives


def explorer_exclu(racine: Path, dossier: bytes, regle_dossier: Motif,
                   actives: list[FichierMotifs], opts: Options, bilan: Bilan) -> None:
    """Dans un dossier exclu : liste (--deplier), compare à pathspec, traque les négations."""
    negations = any(m.negatif for f in actives for m in f.motifs)
    if not (opts.deplier or negations or opts.comparer):
        return
    for chemin, est_dossier in sous_arbre(racine, dossier, bilan):
        bilan.visites += 1
        if bilan.visites + len(bilan.entrees) >= opts.max_entrees:
            bilan.tronque = True
            return
        if opts.deplier:
            consigner(bilan, chemin, est_dossier, None, via=regle_dossier)
        if opts.comparer and not est_dossier:
            comparer_pathspec(chemin, regle_dossier, actives, bilan)
        visee = derniere_regle(chemin, est_dossier, actives, opts.casse) if negations else None
        if visee is not None and visee.negatif:
            bilan.sans_effet.append({
                "chemin": afficher_chemin(chemin, est_dossier),
                "negation": decrire_regle(visee),
                "dossier_exclu": afficher_chemin(dossier, True),
                "exclu_par": decrire_regle(regle_dossier),
            })


def sous_arbre(racine: Path, dossier: bytes, bilan: Bilan) -> Iterator[tuple[bytes, bool]]:
    """Toutes les entrées sous un dossier (sans .git), en profondeur, dans l'ordre trié."""
    pile = [dossier + b"/"]
    base_abs = os.fsencode(racine)
    while pile:
        relatif = pile.pop()
        enfants = []
        for nom, est_dossier in lister(os.path.join(base_abs, relatif), bilan):
            if nom == NOM_GIT:
                continue
            yield relatif + nom, est_dossier
            if est_dossier:
                enfants.append(relatif + nom + b"/")
        pile.extend(reversed(enfants))


# --------------------------------------------------------------------------- vérification ciblée


def verifier_chemin(racine: Path, demande: str, piles: list[FichierMotifs], opts: Options,
                    bilan: Bilan) -> dict[str, Any]:
    """Comme git check-ignore --no-index -v : remonte les ancêtres, puis décide le chemin."""
    propre = demande.strip("/")
    if not propre or ".." in propre.split("/"):
        raise EntreeInvalide(f"--verifier « {demande} » : chemin relatif à la racine attendu")
    parties = os.fsencode(propre).split(b"/")
    actives, cumul = list(piles), b""
    for partie in parties[:-1]:
        cumul += partie
        regle = derniere_regle(cumul, True, actives, opts.casse)
        if regle is not None and not regle.negatif:
            return resultat_verification(demande, parties, regle, actives, opts, cumul)
        source = os.fsdecode(cumul) + "/.gitignore"
        fichier = lire_fichier_regles(racine / os.fsdecode(cumul) / ".gitignore", source,
                                      cumul + b"/", bilan)
        actives += [fichier] if fichier else []
        cumul += b"/"
    chemin = os.fsencode(propre)
    est_dossier = demande.endswith("/") or (racine / propre).is_dir()
    regle = derniere_regle(chemin, est_dossier, actives, opts.casse)
    return {"chemin": demande, "ignore": regle is not None and not regle.negatif,
            "regle": decrire_regle(regle), "existe": (racine / propre).exists(),
            "negation_sans_effet": None}


def resultat_verification(demande: str, parties: list[bytes], regle: Motif,
                          actives: list[FichierMotifs], opts: Options, dossier: bytes) -> dict[str, Any]:
    """Chemin dont un ancêtre est exclu : ignoré par la règle de l'ancêtre."""
    chemin = b"/".join(parties)
    visee = derniere_regle(chemin, demande.endswith("/"), actives, opts.casse)
    sans_effet = decrire_regle(visee) if visee is not None and visee.negatif else None
    return {"chemin": demande, "ignore": True, "regle": decrire_regle(regle),
            "via_dossier": afficher_chemin(dossier, True), "negation_sans_effet": sans_effet}


# --------------------------------------------------------------------------- pathspec


def comparer_pathspec(chemin: bytes, regle: Motif | None, actives: Sequence[FichierMotifs],
                      bilan: Bilan) -> None:
    """Décision de pathspec.GitIgnoreSpec, fichier de règles le plus profond d'abord."""
    bilan.compares += 1
    decision_ps, source_ps = None, None
    for fichier in reversed(actives):
        relatif = os.fsdecode(chemin[len(fichier.base):])
        resultat = specification(fichier).check_file(relatif)
        if resultat.include is not None:
            decision_ps, source_ps = resultat.include, f"{fichier.source}:{resultat.index + 1}"
            break
    decision = regle is not None and not regle.negatif
    if bool(decision_ps) != decision and len(bilan.ecarts) < 500:
        bilan.ecarts.append({"chemin": os.fsdecode(chemin), "outil_ignore": decision,
                             "regle_outil": decrire_regle(regle),
                             "pathspec_ignore": bool(decision_ps), "regle_pathspec": source_ps})


def specification(fichier: FichierMotifs) -> Any:
    """GitIgnoreSpec construit une fois par fichier de règles (mémorisé sur l'objet)."""
    if fichier.spec is None:
        fichier.spec = pathspec.GitIgnoreSpec.from_lines(fichier.lignes)
    return fichier.spec


# --------------------------------------------------------------------------- rapport


def regles_jamais_decisives(bilan: Bilan) -> list[dict[str, Any]]:
    """Règles qui n'ont décidé d'aucune entrée parcourue (vide en mode --verifier)."""
    if not bilan.entrees:
        return []
    return [decrire_regle(m) for f in bilan.fichiers_regles for m in f.motifs
            if (m.source, m.ligne) not in bilan.usages and m.motif]


def construire_rapport(racine: Path, bilan: Bilan, verifications: list[dict[str, Any]],
                       moteur: str, tous: bool, max_liste: int) -> dict[str, Any]:
    """Assemble le rapport JSON."""
    ignores = [e for e in bilan.entrees if e["ignore"]]
    reinclus = [e for e in bilan.entrees if e["decision"] == "ré-inclus"]
    rapport: dict[str, Any] = {
        "outil": Path(__file__).stem,
        "python": platform.python_version(),
        "moteur": moteur,
        "racine": str(racine),
        "denominateur": len(bilan.entrees) + len(verifications),
        "examines": ([e["chemin"] for e in bilan.entrees] + [v["chemin"] for v in verifications])[:MAX_EXAMINES],
        "examines_tronques": len(bilan.entrees) + len(verifications) > MAX_EXAMINES,
        "parcours_tronque": bilan.tronque,
        "fichiers_de_regles": [{"fichier": f.source, "motifs": len(f.motifs)} for f in bilan.fichiers_regles],
        "bilan": {"entrees": len(bilan.entrees), "ignorees": len(ignores), "reincluses": len(reinclus),
                  "negations_sans_effet": len(bilan.sans_effet)},
        "ignores": ignores[:max_liste],
        "reinclus": reinclus[:max_liste],
        "negations_sans_effet": bilan.sans_effet[:max_liste],
        "regles_jamais_decisives": regles_jamais_decisives(bilan),
        "verifications": verifications,
        "alertes": bilan.alertes,
    }
    if tous:
        rapport["non_ignores"] = [e for e in bilan.entrees if not e["ignore"]][:max_liste]
    if moteur != "stdlib":
        rapport["comparaison_pathspec"] = {
            "version": getattr(pathspec, "__version__", "?"), "fichiers_compares": bilan.compares,
            "ecarts": bilan.ecarts, "note": "pathspec ne fait pas foi ; la référence est git"}
    return rapport


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Sortie lisible : ignorés avec leur règle, négations sans effet, bilan."""
    def regle(r: dict[str, Any] | None) -> str:
        return f"{r['fichier']}:{r['ligne']}  {r['motif']}" if r else "-"
    for titre, cle in (("Ignorés", "ignores"), ("Ré-inclus par négation", "reinclus")):
        print(f"{titre} ({len(rapport[cle])}) :")
        for e in rapport[cle]:
            print(f"  {e['chemin']:<50} {regle(e['regle'])}")
    for s in rapport["negations_sans_effet"]:
        print(f"NÉGATION SANS EFFET : {s['chemin']} visé par {regle(s['negation'])} ; "
              f"dossier {s['dossier_exclu']} exclu par {regle(s['exclu_par'])}")
    for v in rapport["verifications"]:
        print(f"vérifié {v['chemin']} : {'ignoré' if v['ignore'] else 'non ignoré'} ({regle(v['regle'])})")
    b = rapport["bilan"]
    print(f"Bilan : {b['entrees']} entrées examinées, {b['ignorees']} ignorées, "
          f"{b['reincluses']} ré-incluses, {b['negations_sans_effet']} négations sans effet")
    for e in rapport.get("comparaison_pathspec", {}).get("ecarts", []):
        print(f"écart pathspec : {e['chemin']} outil={e['outil_ignore']} pathspec={e['pathspec_ignore']}")


def afficher_json(objet: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(objet, ensure_ascii=False, indent=2))


def choisir_moteur(demande: str) -> str:
    """'stdlib+pathspec' si pathspec est disponible et demandé, sinon 'stdlib'."""
    if demande == "stdlib":
        return "stdlib"
    if pathspec is None or not hasattr(pathspec, "GitIgnoreSpec"):
        if demande == "pathspec":
            raise EntreeInvalide("--moteur pathspec demandé mais pathspec (>= 0.10) est absent")
        print("pathspec absent : pas de comparaison, moteur stdlib seul (portage de git)",
              file=sys.stderr)
        return "stdlib"
    return "stdlib+pathspec"


def valider_racine(texte: str, base: Path) -> Path:
    """Le positionnel doit être un dossier existant."""
    chemin = Path(texte) if Path(texte).is_absolute() else base / texte
    if not chemin.exists():
        raise EntreeInvalide(f"« {texte} » n'existe pas")
    if not chemin.is_dir():
        raise EntreeInvalide(f"« {texte} » n'est pas un dossier (attendu : la racine d'un dépôt)")
    return chemin


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande, aide en français."""
    parseur = argparse.ArgumentParser(
        prog=Path(__file__).name,
        description="Applique la sémantique gitignore (portage de git) à un arbre : pour chaque "
                    "entrée, ignorée ou non et par quelle règle (fichier:ligne). Code 1 si une "
                    "négation « ! » est rendue sans effet par un dossier parent exclu.",
        epilog=f"Exemple : python {RACINE.name}/{Path(__file__).name} . --verifier .env "
               "--deplier --json",
    )
    parseur.add_argument("dossier", help="racine du dépôt à examiner")
    parseur.add_argument("--verifier", action="append", default=[], metavar="CHEMIN",
                         help="chemin relatif à décider seul (comme git check-ignore) ; répétable")
    parseur.add_argument("--deplier", action="store_true",
                         help="lister aussi le contenu des dossiers ignorés")
    parseur.add_argument("--tous", action="store_true", help="lister aussi les entrées non ignorées")
    parseur.add_argument("--insensible-casse", action="store_true",
                         help="comme core.ignorecase=true")
    parseur.add_argument("--exclure-fichier", action="append", default=[], type=Path,
                         metavar="FICHIER", help="fichier de règles de priorité minimale "
                                                 "(équivalent explicite de core.excludesFile)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "pathspec"), default="auto",
                         help="auto : comparer avec pathspec s'il est installé")
    parseur.add_argument("--max-entrees", type=int, default=200_000,
                         help="entrées examinées au plus (défaut : 200 000)")
    parseur.add_argument("--max-liste", type=int, default=5000,
                         help="éléments listés au plus par catégorie dans le rapport")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def executer(args: argparse.Namespace) -> dict[str, Any]:
    """Lit les règles, parcourt (ou vérifie), assemble le rapport."""
    base = args.racine if args.racine is not None else Path.cwd()
    moteur = choisir_moteur(args.moteur)
    racine = valider_racine(args.dossier, base)
    opts = Options(args.insensible_casse, args.deplier, max(1, args.max_entrees),
                   moteur != "stdlib")
    bilan = Bilan()
    piles = piles_initiales(racine, [p if p.is_absolute() else base / p for p in args.exclure_fichier],
                            bilan)
    if not (racine / ".git").exists():
        bilan.alertes.append("aucun .git à la racine : dossier traité comme racine de dépôt, "
                             "les .gitignore de ses parents ne sont pas lus")
    verifications = [verifier_chemin(racine, v, piles, opts, bilan) for v in args.verifier]
    if not args.verifier:
        parcourir(racine, piles, opts, bilan)
    return construire_rapport(racine, bilan, verifications, moteur, args.tous, max(0, args.max_liste))


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : code 0, 1 (négation sans effet), 2 (entrée invalide), 3 (rien)."""
    args = construire_parseur().parse_args(argv)
    try:
        rapport = executer(args)
    except EntreeInvalide as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            afficher_json({"denominateur": 0, "examines": [], "erreur": str(exc)})
        return 2
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    for alerte in rapport["alertes"][:20]:
        print(f"note : {alerte}", file=sys.stderr)
    sans_effet = rapport["bilan"]["negations_sans_effet"] + sum(
        1 for v in rapport["verifications"] if v.get("negation_sans_effet"))
    code = 1 if sans_effet else 0
    if rapport["denominateur"] == 0:
        print("dénominateur nul : dossier vide, rien à examiner", file=sys.stderr)
        code = 3
    rapport["code_sortie"] = code
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
