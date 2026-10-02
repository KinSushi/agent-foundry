"""Classe les passages d'un corpus local par pertinence lexicale BM25 pour une requête.

Mesuré le 2 octobre 2026 : grep -rliE "contrat|mesure" docs rend 102 fichiers sur
142, sans ordre ni passage ; cet outil classe 2752 passages de docs/ et ses scores
diffèrent au plus de 1,2e-07 de bm25s 0.3.11 (lucene) et de 8,9e-16 de rank-bm25
0.2.2 (okapi), top 10 identique (rechercher_bm25.py docs "contrat mesure" --comparer).

QUESTION
    Quels passages de ce corpus répondent le mieux à cette requête ?
MESURE
    Les fichiers texte du dossier sont segmentés en passages (paragraphes
    séparés par une ligne vide, coupés au-delà de --lignes-max lignes).
    Tokenisation français/anglais : casse repliée, accents retirés,
    identifiants camelCase et snake_case séparés, mots vides retirés,
    racinisation optionnelle. Score BM25 avec k1 et b paramétrables :
    variante lucene (idf = ln(1 + (N - n + 0,5) / (n + 0,5)), terme
    tf / (tf + k1 (1 - b + b dl / avgdl))) ou okapi (idf de Robertson,
    idf négatives remplacées par 0,25 fois l'idf moyenne, facteur k1 + 1).
    Rend les k meilleurs passages : score, fichier, lignes, extrait, termes
    trouvés.
HYPOTHÈSES
    La pertinence se lit dans le partage de mots avec la requête ; les
    fichiers sont en utf-8. Un passage est l'unité de réponse pertinente.
LIMITES
    Aucune sémantique : un synonyme ou une paraphrase sans mot commun
    obtient 0. Les scores ne se comparent qu'au sein d'un même corpus et
    d'une même requête. Sans PyStemmer ni snowballstemmer, --racinisation
    applique un retrait de suffixes léger (pluriels, quelques dérivations)
    bien moins complet que Snowball. Les fichiers non utf-8 sont ignorés
    (listés). Tout l'index tient en mémoire.
CONTRE-EXEMPLES
    Constaté : la requête « voiture » sur un fichier contenant « L'automobile
    électrique consomme peu. » ne trouve aucun passage (code 1), alors que
    ce passage répond à la question : aucun mot commun, aucun score.
INVOCATION
    {outil} {dossier} "contrat mesure" --json
DOMAINE
    Recherche dans des dépôts de code, de documentation ou de notes, en
    français ou en anglais, avant de citer ou de modifier un passage.
"""

from __future__ import annotations

import argparse
import contextlib
import heapq
import importlib
import importlib.util
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
MOTEUR_STDLIB = "stdlib"
MODELE = "BM25"
ENCODAGE = "utf-8"
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
VARIANTES = ("lucene", "okapi")
EPSILON_OKAPI = 0.25
MAX_EXAMINES = 50
MAX_FICHIERS = 50000
LONGUEUR_EXTRAIT = 240
EXTENSIONS_DEFAUT = (".md,.markdown,.mdx,.txt,.rst,.adoc,.org,.py,.pyi,.js,.ts,.tsx,.jsx,"
                     ".java,.kt,.go,.rs,.c,.h,.cpp,.hpp,.cs,.rb,.php,.sh,.sql,.json,.yaml,"
                     ".yml,.toml,.ini,.cfg,.html,.css,.tex,.csv")
DOSSIERS_IGNORES = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv",
                              ".tox", ".mypy_cache", ".pytest_cache", "dist", "build"})
MOTS_VIDES = frozenset("""
a ai aie aient aies ait as au aura aurai auraient aurais aurait auras aurez auriez aurions
aurons auront aux avaient avais avait avec avez aviez avions avoir avons ayant ayez ayons
c ce ceci cela celle celles celui ces cet cette ceux chaque comme comment d dans de des
du donc dont elle elles en entre est et etaient etais etait etant ete etes etiez etions
etre eu eue eues eurent eus eut eux fait fut il ils j je l la le les leur leurs lui m ma
mais me meme mes moi mon n ne ni nos notre nous on ont ou par pas peu peut plus pour
pourquoi qu quand que quel quelle quelles quels qui s sa sans se ses si sien soi soient
sois soit sommes son sont sous suis sur t ta te tes toi ton tous tout toute toutes tres
tu un une une unes uns vos votre vous y
about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from
further had has have having he her here hers herself him himself his how i if in into is
it its itself just me more most my myself no nor not of off on once only or other our
ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we
were what when where which while who whom why will with would you your yours yourself
""".split())
BIBLIOTHEQUES_OPTIONNELLES = (
    ("Stemmer", "PyStemmer", "racinisation légère stdlib si --racinisation"),
    ("snowballstemmer", "snowballstemmer", "racinisation légère stdlib si --racinisation"),
    ("rank_bm25", "rank-bm25", "pas de comparaison okapi"),
    ("bm25s", "bm25s", "pas de comparaison lucene"),
)

RE_DIACRITIQUES = re.compile(r"[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]")
RE_CAMEL = re.compile(r"(?<=[a-zà-ÿ0-9])(?=[A-ZÀ-Þ])")
RE_JETON = re.compile(r"[^\W_]+")
SUFFIXES_FR = ("issements", "issement", "ations", "ation", "ements", "ement", "ments", "ment",
               "euses", "euse", "eux", "ives", "ive", "ifs", "if", "ees", "ee", "es", "s", "x")
SUFFIXES_EN = ("ational", "ations", "ation", "ements", "ement", "ments", "ment", "ness",
               "ings", "ing", "ies", "ied", "edly", "ed", "ly", "es", "s")


class ErreurEntree(Exception):
    """Entrée invalide : message pour stderr et code de sortie."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Passage:
    """Passage indexé : fichier, lignes, texte brut et jetons."""

    fichier: str
    ligne_debut: int
    ligne_fin: int
    texte: str
    jetons: tuple[str, ...]


@dataclass
class Index:
    """Index inversé BM25 construit localement."""

    passages: list[Passage]
    longueurs: list[int]
    frequences: list[Counter[str]]
    postings: dict[str, list[int]] = field(default_factory=dict)
    longueur_moyenne: float = 0.0


# --------------------------------------------------------------------------
# Contrat, bibliothèques optionnelles


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring."""
    contrat: dict[str, str] = {}
    courant = ""
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES:
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def _bibliotheque_presente(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _signaler_absences() -> None:
    """UNE ligne sur stderr pour les bibliothèques optionnelles absentes."""
    absentes = [f"{nom} ({repli})" for module, nom, repli in BIBLIOTHEQUES_OPTIONNELLES
                if not _bibliotheque_presente(module)]
    if absentes:
        print("rechercher_bm25 : absent(s) : " + " ; ".join(absentes)
              + " — classement par le moteur stdlib.", file=sys.stderr)


def _importer(module: str) -> ModuleType | None:
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _version(nom_distribution: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return "?"
    try:
        return version(nom_distribution)
    except PackageNotFoundError:
        return "?"


# --------------------------------------------------------------------------
# Tokenisation


def _replier(texte: str) -> str:
    """Casse repliée, accents retirés, camelCase séparé."""
    texte = RE_CAMEL.sub(" ", texte)
    texte = unicodedata.normalize("NFKD", texte.casefold())
    return RE_DIACRITIQUES.sub("", texte)


def _raciner_leger(mot: str, suffixes: tuple[str, ...]) -> str:
    """Retrait du plus long suffixe connu, en gardant au moins trois lettres."""
    for suffixe in suffixes:
        if mot.endswith(suffixe) and len(mot) - len(suffixe) >= 3:
            return mot[: -len(suffixe)]
    return mot


def detecter_langue(textes: list[str]) -> str:
    """fr ou en selon les mots vides les plus fréquents d'un échantillon."""
    francais = frozenset({"le", "la", "les", "des", "est", "une", "dans", "pour", "que", "qui"})
    anglais = frozenset({"the", "is", "are", "and", "of", "to", "in", "that", "for", "with"})
    fr = en = 0
    for texte in textes[:2000]:
        for mot in RE_JETON.findall(texte.casefold()):
            fr += mot in francais
            en += mot in anglais
    return "fr" if fr >= en else "en"


def construire_racineur(actif: bool, langue: str) -> tuple[Callable[[str], str], str]:
    """Fonction de racinisation et nom du moteur qui la fournit."""
    if not actif:
        return (lambda mot: mot), "aucune"
    nom_snowball = "french" if langue == "fr" else "english"
    pystemmer = _importer("Stemmer")
    if pystemmer is not None:
        return pystemmer.Stemmer(nom_snowball).stemWord, f"PyStemmer {_version('PyStemmer')} ({nom_snowball})"
    snowball = _importer("snowballstemmer")
    if snowball is not None:
        return snowball.stemmer(nom_snowball).stemWord, f"snowballstemmer {_version('snowballstemmer')} ({nom_snowball})"
    suffixes = SUFFIXES_FR if langue == "fr" else SUFFIXES_EN
    return (lambda mot: _raciner_leger(mot, suffixes)), f"stdlib-légère ({langue})"


class Tokeniseur:
    """Découpe en jetons normalisés, avec cache local de racinisation."""

    def __init__(self, raciner: Callable[[str], str], mots_vides: frozenset[str]) -> None:
        self.raciner = raciner
        self.mots_vides = mots_vides
        self.cache: dict[str, str] = {}

    def __call__(self, texte: str) -> tuple[str, ...]:
        jetons = []
        for mot in RE_JETON.findall(_replier(texte)):
            if mot in self.mots_vides:
                continue
            racine = self.cache.get(mot)
            if racine is None:
                racine = self.cache.setdefault(mot, self.raciner(mot))
            jetons.append(racine)
        return tuple(jetons)


# --------------------------------------------------------------------------
# Lecture et segmentation


def _nom_affiche(chemin: Path, racine: Path) -> str:
    try:
        return chemin.resolve().relative_to(racine.resolve()).as_posix()
    except ValueError:
        return chemin.as_posix()


def _fichiers_du_dossier(dossier: Path, extensions: tuple[str, ...]) -> Iterator[Path]:
    for chemin in sorted(dossier.rglob("*")):
        if any(p in DOSSIERS_IGNORES for p in chemin.relative_to(dossier).parts[:-1]):
            continue
        if chemin.is_file() and chemin.suffix.lower() in extensions:
            yield chemin


def lister_fichiers(chemin: Path, extensions: tuple[str, ...]) -> tuple[list[Path], bool]:
    """Fichiers à indexer et indicateur « fichier donné explicitement »."""
    if not chemin.exists():
        raise ErreurEntree(f"chemin introuvable : {chemin}")
    if chemin.is_file():
        return [chemin], True
    if not chemin.is_dir():
        raise ErreurEntree(f"ni fichier ni dossier : {chemin}")
    fichiers = []
    for fichier in _fichiers_du_dossier(chemin, extensions):
        fichiers.append(fichier)
        if len(fichiers) > MAX_FICHIERS:
            raise ErreurEntree(f"plus de {MAX_FICHIERS} fichiers : restreindre le dossier")
    return fichiers, False


def lire_texte(chemin: Path, taille_max: int) -> str:
    """Lecture bornée en utf-8 ; ErreurEntree si binaire, trop gros ou mal encodé."""
    taille = chemin.stat().st_size
    if taille > taille_max:
        raise ErreurEntree(f"{chemin} : {taille} octets > --taille-max-fichier {taille_max}")
    with chemin.open("rb") as flux:
        brut = flux.read(taille_max + 1)
    if b"\x00" in brut:
        raise ErreurEntree(f"{chemin} : fichier binaire (octet nul)")
    try:
        return brut.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} : pas de l'utf-8 valide (octet {exc.start})") from exc


def segmenter(texte: str, lignes_max: int) -> Iterator[tuple[int, int, str]]:
    """Passages (ligne_debut, ligne_fin, texte) : paragraphes coupés à lignes_max lignes."""
    bloc: list[str] = []
    debut = 1
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        if not ligne.strip():
            if bloc:
                yield debut, numero - 1, "\n".join(bloc)
            bloc = []
            continue
        if not bloc:
            debut = numero
        bloc.append(ligne)
        if len(bloc) >= lignes_max:
            yield debut, numero, "\n".join(bloc)
            bloc = []
    if bloc:
        yield debut, debut + len(bloc) - 1, "\n".join(bloc)


def charger_passages(fichiers: list[Path], explicite: bool, args: argparse.Namespace,
                     racine: Path, tokeniser: Tokeniseur) -> tuple[list[Passage], list[dict[str, str]]]:
    """Passages non vides de tous les fichiers lisibles ; fichiers ignorés avec raison."""
    passages: list[Passage] = []
    ignores: list[dict[str, str]] = []
    for chemin in fichiers:
        nom = _nom_affiche(chemin, racine)
        try:
            texte = lire_texte(chemin, args.taille_max_fichier)
        except (ErreurEntree, OSError) as exc:
            if explicite:
                raise ErreurEntree(str(exc)) from exc
            ignores.append({"chemin": nom, "raison": str(exc)})
            continue
        for debut, fin, contenu in segmenter(texte, args.lignes_max):
            jetons = tokeniser(contenu)
            if jetons:
                passages.append(Passage(nom, debut, fin, contenu, jetons))
    return passages, ignores


# --------------------------------------------------------------------------
# BM25


def construire_index(passages: list[Passage]) -> Index:
    """Index inversé : fréquences par passage, listes de passages par terme."""
    frequences = [Counter(p.jetons) for p in passages]
    longueurs = [len(p.jetons) for p in passages]
    index = Index(passages, longueurs, frequences)
    for numero, compteur in enumerate(frequences):
        for terme in compteur:
            index.postings.setdefault(terme, []).append(numero)
    index.longueur_moyenne = sum(longueurs) / len(longueurs) if longueurs else 0.0
    return index


def calculer_idf(index: Index, variante: str) -> dict[str, float]:
    """idf de chaque terme du vocabulaire selon la variante."""
    total = len(index.passages)
    if variante == "lucene":
        return {t: math.log(1 + (total - len(p) + 0.5) / (len(p) + 0.5))
                for t, p in index.postings.items()}
    idf = {t: math.log(total - len(p) + 0.5) - math.log(len(p) + 0.5)
           for t, p in index.postings.items()}
    plancher = EPSILON_OKAPI * (sum(idf.values()) / len(idf)) if idf else 0.0
    return {t: (plancher if v < 0 else v) for t, v in idf.items()}


def scorer(index: Index, requete: tuple[str, ...], k1: float, b: float,
           variante: str) -> dict[int, float]:
    """Score BM25 de chaque passage contenant au moins un terme de la requête."""
    idf = calculer_idf(index, variante)
    facteur = (k1 + 1) if variante == "okapi" else 1.0
    scores: dict[int, float] = {}
    for terme in requete:
        for numero in index.postings.get(terme, ()):
            tf = index.frequences[numero][terme]
            norme = k1 * (1 - b + b * index.longueurs[numero] / index.longueur_moyenne)
            scores[numero] = scores.get(numero, 0.0) + idf[terme] * tf * facteur / (tf + norme)
    return scores


def meilleurs(scores: dict[int, float], k: int) -> list[tuple[int, float]]:
    """k meilleurs passages, départage stable par ordre d'indexation."""
    return heapq.nsmallest(k, scores.items(), key=lambda paire: (-paire[1], paire[0]))


def _extrait(passage: Passage, requete: set[str], tokeniser: Tokeniseur) -> tuple[int, str]:
    """Ligne du passage la plus riche en termes de la requête, tronquée."""
    lignes = passage.texte.splitlines()
    meilleure = max(range(len(lignes)),
                    key=lambda i: (len(requete & set(tokeniser(lignes[i]))), -i))
    texte = " ".join(lignes[meilleure].split())
    if len(texte) > LONGUEUR_EXTRAIT:
        texte = texte[:LONGUEUR_EXTRAIT - 1] + "…"
    return passage.ligne_debut + meilleure, texte


def decrire_resultats(index: Index, classement: list[tuple[int, float]], requete: tuple[str, ...],
                      tokeniser: Tokeniseur) -> list[dict[str, Any]]:
    """Résultats lisibles : rang, score, fichier, lignes, extrait, termes trouvés."""
    termes = set(requete)
    resultats = []
    for rang, (numero, score) in enumerate(classement, start=1):
        passage = index.passages[numero]
        ligne, extrait = _extrait(passage, termes, tokeniser)
        resultats.append({
            "rang": rang, "score": round(score, 6), "fichier": passage.fichier,
            "ligne": ligne, "ligne_debut": passage.ligne_debut, "ligne_fin": passage.ligne_fin,
            "extrait": extrait, "termes_trouves": sorted(termes & set(passage.jetons)),
        })
    return resultats


# --------------------------------------------------------------------------
# Comparaison avec rank-bm25 / bm25s


def _scores_tiers(index: Index, requete: tuple[str, ...], args: argparse.Namespace) -> tuple[list[float], str] | None:
    """Scores de tous les passages par la bibliothèque correspondant à la variante."""
    corpus = [list(p.jetons) for p in index.passages]
    with contextlib.redirect_stdout(sys.stderr):
        if args.variante == "okapi":
            module = _importer("rank_bm25")
            if module is None:
                return None
            moteur = module.BM25Okapi(corpus, k1=args.k1, b=args.b, epsilon=EPSILON_OKAPI)
            return [float(x) for x in moteur.get_scores(list(requete))], f"rank-bm25 {_version('rank-bm25')}"
        module = _importer("bm25s")
        if module is None:
            return None
        moteur = module.BM25(method="lucene", k1=args.k1, b=args.b)
        moteur.index(corpus, show_progress=False)
        connus = [t for t in requete if t in index.postings]
        if not connus:
            return [0.0] * len(corpus), f"bm25s {_version('bm25s')}"
        return [float(x) for x in moteur.get_scores(connus)], f"bm25s {_version('bm25s')}"


def comparer(index: Index, requete: tuple[str, ...], scores: dict[int, float],
             args: argparse.Namespace) -> dict[str, Any]:
    """Écart maximal des scores et accord des classements avec la bibliothèque tierce."""
    tiers = _scores_tiers(index, requete, args)
    if tiers is None:
        attendu = "rank-bm25" if args.variante == "okapi" else "bm25s"
        return {"absent": attendu}
    valeurs, moteur = tiers
    ecart = max((abs(scores.get(i, 0.0) - v) for i, v in enumerate(valeurs)), default=0.0)
    notres = [i for i, _ in meilleurs(scores, args.k)]
    leurs = [i for i, _ in meilleurs({i: v for i, v in enumerate(valeurs) if v > 0}, args.k)]
    return {
        "moteur": moteur,
        "ecart_score_max": ecart,
        "top_k_identique": notres == leurs,
        "recouvrement_top_k": len(set(notres) & set(leurs)) / max(1, len(notres)),
    }


# --------------------------------------------------------------------------
# Orchestration et sorties


def presenter_humain(sortie: dict[str, Any]) -> None:
    """Affichage lisible des résultats."""
    print(f"requête : {sortie['requete']} → jetons {sortie['jetons_requete']} ; "
          f"{sortie['denominateur']} passage(s) dans {sortie['fichiers']} fichier(s) ; "
          f"variante {sortie['variante']} (k1={sortie['k1']}, b={sortie['b']}), "
          f"racinisation {sortie['racinisation']}")
    if not sortie["resultats"]:
        print("aucun passage ne contient un terme de la requête")
    for r in sortie["resultats"]:
        print(f"{r['rang']:>3}. {r['score']:.4f}  {r['fichier']}:{r['ligne']}  {r['extrait']}")
    if "comparaison" in sortie:
        print(f"comparaison : {sortie['comparaison']}")


def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    parser = argparse.ArgumentParser(
        description="Recherche BM25 (Okapi ou Lucene) dans les fichiers texte d'un dossier : "
                    "passages classés avec score, fichier, ligne et extrait.",
        epilog='Exemple : python3 rechercher_bm25.py docs "contrat de mesure" --k 5 --racinisation --json')
    parser.add_argument("chemin", type=Path, help="dossier (ou fichier) à indexer")
    parser.add_argument("requete", help="requête en langage naturel ou mots-clés")
    parser.add_argument("--k", type=int, default=10, help="nombre de passages rendus (défaut 10)")
    parser.add_argument("--k1", type=float, default=1.5, help="saturation de la fréquence (défaut 1.5)")
    parser.add_argument("--b", type=float, default=0.75, help="normalisation par la longueur (défaut 0.75)")
    parser.add_argument("--variante", choices=VARIANTES, default="lucene", metavar="VARIANTE",
                        help="lucene (défaut, idf toujours positive) ou okapi (rank-bm25)")
    parser.add_argument("--lignes-max", type=int, default=12, metavar="LIGNES",
                        help="lignes maximales par passage (défaut 12)")
    parser.add_argument("--racinisation", action="store_true",
                        help="raciniser (PyStemmer, snowballstemmer, sinon repli léger stdlib)")
    parser.add_argument("--langue", choices=("auto", "fr", "en"), default="auto", metavar="LANGUE",
                        help="langue de racinisation : auto, fr ou en")
    parser.add_argument("--garder-mots-vides", action="store_true", help="ne pas retirer les mots vides")
    parser.add_argument("--extensions", default=EXTENSIONS_DEFAUT, help="extensions indexées dans un dossier")
    parser.add_argument("--taille-max-fichier", type=int, default=20_000_000, metavar="OCTETS",
                        help="taille maximale lue par fichier")
    parser.add_argument("--comparer", action="store_true",
                        help="mesurer l'écart avec rank-bm25 (okapi) ou bm25s (lucene)")
    parser.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=Path,
                        help=f"base des chemins relatifs (défaut : répertoire courant ; outil dans {RACINE})")
    return parser


def valider(args: argparse.Namespace) -> None:
    """Contrôle des paramètres numériques."""
    if args.k < 1:
        raise ErreurEntree("--k doit être ≥ 1")
    if args.k1 < 0 or not 0 <= args.b <= 1:
        raise ErreurEntree("--k1 doit être ≥ 0 et --b compris entre 0 et 1")
    if args.lignes_max < 1 or args.taille_max_fichier < 1:
        raise ErreurEntree("--lignes-max et --taille-max-fichier doivent être ≥ 1")


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Indexe, score et classe ; renvoie la sortie et le code."""
    valider(args)
    racine = args.racine if args.racine is not None else Path.cwd()
    chemin = args.chemin if args.chemin.is_absolute() else racine / args.chemin
    extensions = tuple(e.strip().lower() for e in args.extensions.split(",") if e.strip())
    fichiers, explicite = lister_fichiers(chemin, extensions)
    langue = args.langue
    if args.racinisation and langue == "auto":
        langue = detecter_langue([args.requete] + [_lire_sans_erreur(f, args) for f in fichiers[:50]])
    raciner, moteur_racines = construire_racineur(args.racinisation, langue)
    tokeniser = Tokeniseur(raciner, frozenset() if args.garder_mots_vides else MOTS_VIDES)
    requete = tokeniser(args.requete)
    if not requete:
        raise ErreurEntree("requête vide après tokenisation (mots vides retirés) : préciser la requête")
    passages, ignores = charger_passages(fichiers, explicite, args, racine, tokeniser)
    sortie = _squelette(args, requete, passages, ignores, moteur_racines)
    if not passages:
        return sortie, 3
    index = construire_index(passages)
    scores = scorer(index, requete, args.k1, args.b, args.variante)
    sortie["resultats"] = decrire_resultats(index, meilleurs(scores, args.k), requete, tokeniser)
    sortie["passages_avec_correspondance"] = len(scores)
    if args.comparer:
        sortie["comparaison"] = comparer(index, requete, scores, args)
    return sortie, 0 if sortie["resultats"] else 1


def _lire_sans_erreur(chemin: Path, args: argparse.Namespace) -> str:
    """Texte d'un fichier pour l'échantillon de langue ; vide s'il est illisible."""
    try:
        return lire_texte(chemin, args.taille_max_fichier)[:20000]
    except (ErreurEntree, OSError):
        return ""


def _squelette(args: argparse.Namespace, requete: tuple[str, ...], passages: list[Passage],
               ignores: list[dict[str, str]], moteur_racines: str) -> dict[str, Any]:
    """Champs communs de la sortie JSON."""
    noms = [f"{p.fichier}:{p.ligne_debut}-{p.ligne_fin}" for p in passages]
    fichiers = sorted({p.fichier for p in passages})
    return {
        "outil": "rechercher_bm25",
        "moteur": MOTEUR_STDLIB,
        "modele": MODELE,
        "requete": args.requete,
        "jetons_requete": list(requete),
        "variante": args.variante, "k1": args.k1, "b": args.b, "k": args.k,
        "racinisation": moteur_racines,
        "denominateur": len(passages),
        "examines": noms[:MAX_EXAMINES],
        "examines_tronques": len(noms) > MAX_EXAMINES,
        "fichiers": len(fichiers),
        "fichiers_examines": fichiers[:MAX_EXAMINES],
        "ignores": ignores[:MAX_EXAMINES],
        "resultats": [],
        "passages_avec_correspondance": 0,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_analyseur().parse_args(argv)
    _signaler_absences()
    try:
        sortie, code = executer(args)
    except ErreurEntree as exc:
        print(f"rechercher_bm25 : {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"rechercher_bm25 : erreur d'entrée/sortie : {exc}", file=sys.stderr)
        return 2
    if code == 3:
        print("rechercher_bm25 : dénominateur nul — rien à examiner (aucun passage texte indexable)",
              file=sys.stderr)
    elif code == 1:
        print("rechercher_bm25 : aucun passage ne contient un terme de la requête", file=sys.stderr)
    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    elif code != 3:
        presenter_humain(sortie)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
