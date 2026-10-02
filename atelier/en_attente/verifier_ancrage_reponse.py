"""Confronte chaque phrase d'une réponse générée aux sources fournies et isole les nombres, dates et noms inventés.

Mesuré le 2 octobre 2026 : sur une réponse de 8 phrases résumant un rapport, l'outil
juge NON APPUYÉE les 4 phrases altérées (« 45 % » au lieu de 40 %, « 5 avril 2025 »,
« Claire Martin », un plan d'intéressement inventé), FAIBLE celle qui ajoute
« Marseille », APPUYÉE les 3 fidèles avec fichier et ligne ; et 200 APPUYÉE sur 200
phrases recopiées de 63 fichiers de docs/.

Ce n'est qu'un INDICE LEXICAL, pas une preuve sémantique : une phrase APPUYÉE
partage ses mots et leur ordre avec un passage source, rien de plus.

QUESTION
    Chaque phrase de cette réponse est-elle appuyée par les sources, et
    quels nombres, dates ou noms propres n'y figurent nulle part ?
MESURE
    La réponse est découpée en phrases (abréviations, décimales et puces de
    liste gérées ; titres et blocs de code ignorés). Les sources (fichiers
    ou dossiers) sont découpées en passages de --fenetre phrases
    consécutives. Pour chaque phrase : les passages candidats sont classés
    par BM25 ; pour chacun, couverture = part de l'idf des mots pleins de
    la phrase présents dans le passage, séquence = plus longue
    sous-séquence commune de mots pleins / longueur de la phrase
    (difflib, ou rapidfuzz exact s'il est installé) ; score = 0,65
    couverture + 0,35 séquence ; le meilleur passage est retenu. Verdict :
    APPUYÉE si score ≥ --seuil-appuyee, FAIBLE si ≥ --seuil-faible, sinon
    NON APPUYÉE. Un nombre ou une date de la phrase absent de TOUTES les
    sources rend la phrase NON APPUYÉE ; un nombre absent du seul meilleur
    passage, un nom propre ou un identifiant absent des sources, ou une
    négation présente d'un seul côté la plafonnent à FAIBLE.
HYPOTHÈSES
    Une phrase fidèle aux sources en reprend les mots pleins ; les sources
    données sont celles sur lesquelles la réponse devait s'appuyer ;
    textes en utf-8, en français ou en anglais.
LIMITES
    INDICE LEXICAL seulement. Une paraphrase fidèle sans mots communs est
    jugée NON APPUYÉE ; une phrase qui recompose des mots des sources en
    affirmation fausse peut être APPUYÉE. Un mot ordinaire substitué (nom
    de bibliothèque en minuscules) n'abaisse que le score ; seuls les
    identifiants (entre accents graves, ou mêlant lettres et chiffres)
    absents des sources plafonnent à FAIBLE. La négation n'est qu'en partie
    vue (comparée à la phrase source la plus proche). Les noms propres sont
    repérés par la majuscule : un nom seul en tête de phrase est manqué.
    Les nombres en lettres ne sont reconnus que de deux à vingt, cent et
    mille. Sans rapidfuzz, difflib minore parfois la sous-séquence commune :
    mesuré sur 397 phrases, 1 verdict différent, écart maximal de 0,125 sur
    la composante séquence.
CONTRE-EXEMPLES
    Constaté : « torch — absentes, l'outil travaille en mode dégradé et le
    dit sur stderr » face à une source qui porte « rich — absentes, l'outil
    travaille en mode dégradé... » est jugée APPUYÉE (score 0,75) alors que
    la bibliothèque citée diffère ; à l'inverse « L'entreprise a recruté
    quinze personnes de plus en 2025. », vraie (effectif passé de 120 à
    135), est NON APPUYÉE (score 0,115).
INVOCATION
    {outil} {fichier} {dossier} --json
DOMAINE
    Relecture de réponses de RAG, de résumés ou de rapports générés avant
    diffusion : trier les phrases à vérifier à la main.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import importlib.util
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Callable, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
VERDICT_APPUYEE = "APPUYÉE"
VERDICT_FAIBLE = "FAIBLE"
VERDICT_NON = "NON APPUYÉE"
MOT_INDICE = "INDICE"
MOT_LEXICAL = "LEXICAL"
MOT_TOUTES = "TOUTES"
MOTEUR_STDLIB = "stdlib"
CLASSEMENT = "BM25"
AVERTISSEMENT = ("indice lexical, pas une preuve sémantique : une phrase APPUYÉE partage ses mots pleins "
                 "et leur ordre avec un passage source ; vérifier à la main avant de conclure")
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
POIDS_COUVERTURE = 0.65
POIDS_SEQUENCE = 0.35
CANDIDATS_BM25 = 12
K1, B = 1.2, 0.75
MAX_EXAMINES = 50
EXTENSIONS_DEFAUT = ".md,.markdown,.mdx,.txt,.rst,.adoc,.org,.py,.json,.yaml,.yml,.toml,.csv,.html,.tex"
DOSSIERS_IGNORES = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".tox"})
ABREVIATIONS = frozenset("""m mm mme mmes mlle dr pr me st ste p pp ex cf env fig vol chap art al
no n° vs e.g i.e mr mrs ms prof inc ltd jr sr approx dept av bd""".split())
MOTS_VIDES = frozenset("""
a ai aie aient ait as au aux avaient avais avait avec avez aviez avions avoir avons ayant
c ce ceci cela celle celles celui ces cet cette ceux chaque comme d dans de des du donc dont
elle elles en entre est et etaient etait etant ete etre eu eux fait il ils j je l la le les
leur leurs lui m ma mais me meme mes moi mon n ne nos notre nous on ont ou par peu peut plus
pour qu que quel quelle quelles quels qui s sa se ses si soi soit sont son sous sur t ta te
tes toi ton tous tout toute toutes tres tu un une y aussi ainsi egalement alors encore deja
toujours bien car puis selon chez vers depuis pendant apres avant lors ou cependant toutefois
donc enfin notamment certain certains certaine certaines autre autres plusieurs
about all also an and any are as at be been being both but by can could did do does each for
from had has have he her here his how i if in into is it its just may me more most my of on
once only or other our out over own same she should so some such than that the their them
then there these they this those through to too up very was we were what when where which
while who whom why will with would you your however thus therefore moreover also still yet
already such among within upon via per
""".split())
NEGATIONS = frozenset({"ne", "n", "pas", "jamais", "aucun", "aucune", "nul", "nulle", "sans", "ni",
                       "not", "no", "never", "none", "nor", "cannot", "without"})
NOMBRES_EN_LETTRES = MappingProxyType({
    "deux": "2", "trois": "3", "quatre": "4", "cinq": "5", "six": "6", "sept": "7", "huit": "8",
    "neuf": "9", "dix": "10", "onze": "11", "douze": "12", "treize": "13", "quatorze": "14",
    "quinze": "15", "seize": "16", "vingt": "20", "cent": "100", "mille": "1000",
    "two": "2", "three": "3", "four": "4", "five": "5", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20",
    "hundred": "100", "thousand": "1000",
})
MOIS = MappingProxyType({
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
})

RE_DIACRITIQUES = re.compile(r"[\u0300-\u036f]")
RE_MOT = re.compile(r"[^\W_]+")
RE_FIN_PHRASE = re.compile(r"([.!?…]+)[\"'»”)\]]*\s+(?=[«\"“(\[]?\s?[^\W\d_a-zß-ÿ]|\d)")
RE_PUCE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
RE_TITRE = re.compile(r"^\s{0,3}#{1,6}\s")
RE_CLOTURE = re.compile(r"^\s*(```|~~~)")
RE_NOMBRE = re.compile(r"(?<![\w.,])\d{1,3}(?:[ \xa0\u202f]\d{3})+(?:[.,]\d+)?(?![\w])"
                       r"|(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")
RE_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
RE_DATE_NUM = re.compile(r"(?<![\d/.])(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?![\d/.])")
RE_DATE_TEXTE = re.compile(r"(?<!\w)(?:(1er|\d{1,2})(?:st|nd|rd|th)?\s+)?([^\W\d_]+)\.?\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?(\d{4})(?!\d)")
RE_NOM_PROPRE = re.compile(r"(?<![\w])[A-ZÀ-ÖØ-Þ][\w'’-]*(?:\s+[A-ZÀ-ÖØ-Þ][\w'’-]*)*")
RE_CODE = re.compile(r"`([^`]+)`")
RE_IDENTIFIANT = re.compile(r"(?<!\w)\w*(?:\d[^\W\d]|[^\W\d]\d|_)\w*")
RE_SIGLE = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]{1,}(?![\w-])")


class ErreurEntree(Exception):
    """Entrée invalide : message pour stderr et code de sortie."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Phrase:
    """Phrase repérée par son texte et sa ligne de départ."""

    texte: str
    ligne: int


@dataclass(frozen=True)
class Passage:
    """Fenêtre de phrases consécutives d'une source."""

    source: str
    ligne: int
    texte: str
    sequence: tuple[str, ...]
    termes: frozenset[str]
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class Corpus:
    """Passages des sources et faits extraits pour les recherches globales."""

    passages: list[Passage]
    idf: dict[str, float]
    idf_absent: float
    longueur_moyenne: float
    postings: dict[str, list[int]]
    texte_replie: str
    nombres: frozenset[str]
    dates: frozenset[str]


# --------------------------------------------------------------------------
# Contrat et bibliothèque optionnelle


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


def charger_sequenceur() -> tuple[Callable[[tuple[str, ...], tuple[str, ...]], int], str]:
    """Longueur de sous-séquence commune : rapidfuzz (exacte) sinon difflib (blocs appariés)."""
    try:
        presente = importlib.util.find_spec("rapidfuzz") is not None
    except (ImportError, ValueError):
        presente = False
    module: ModuleType | None = None
    if presente:
        try:
            module = importlib.import_module("rapidfuzz.distance.LCSseq")
        except ImportError:
            module = None
    if module is None:
        print("verifier_ancrage_reponse : rapidfuzz absent — sous-séquence commune approchée par "
              "difflib (moteur stdlib).", file=sys.stderr)
        return _blocs_difflib, MOTEUR_STDLIB
    return (lambda a, b: int(module.similarity(a, b))), "rapidfuzz"


def _blocs_difflib(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """Somme des blocs appariés par difflib (minore la plus longue sous-séquence commune)."""
    return sum(bloc.size for bloc in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())


# --------------------------------------------------------------------------
# Normalisation et extraction de faits


def replier(texte: str) -> str:
    """Casse repliée et accents retirés."""
    return RE_DIACRITIQUES.sub("", unicodedata.normalize("NFKD", texte.casefold()))


def _raciner(mot: str) -> str:
    """Racinisation minimale : pluriels en s et x."""
    if len(mot) > 3 and mot[-1] in "sx" and not mot.endswith("ss"):
        return mot[:-1]
    return mot


def mots_pleins(texte: str) -> tuple[str, ...]:
    """Mots pleins repliés, dans l'ordre (mots vides retirés, pluriels réduits)."""
    return tuple(_raciner(m) for m in RE_MOT.findall(replier(texte)) if m not in MOTS_VIDES)


def interpretations_nombre(brut: str) -> set[str]:
    """Valeurs possibles d'un nombre écrit (séparateurs de milliers et décimaux FR/EN)."""
    compact = re.sub(r"[ \xa0\u202f]", "", brut)
    candidats: set[str] = set()
    points, virgules = compact.count("."), compact.count(",")
    if points and virgules:
        decimal = "." if compact.rfind(".") > compact.rfind(",") else ","
        autre = "," if decimal == "." else "."
        candidats.add(compact.replace(autre, "").replace(decimal, "."))
    elif points + virgules == 0:
        candidats.add(compact)
    else:
        sep = "." if points else ","
        morceaux = compact.split(sep)
        if len(morceaux) > 2 or len(morceaux[-1]) == 3:
            candidats.add(compact.replace(sep, ""))
        if len(morceaux) == 2:
            candidats.add(compact.replace(sep, "."))
    return {_canonique(c) for c in candidats if _canonique(c)}


def _canonique(valeur: str) -> str:
    try:
        return format(Decimal(valeur).normalize(), "f")
    except (InvalidOperation, ValueError):
        return ""


def extraire_dates(texte: str) -> list[tuple[str, set[str], tuple[int, int]]]:
    """Dates trouvées : (texte, formes AAAA-MM[-JJ] possibles, empan)."""
    dates = []
    for m in RE_DATE_ISO.finditer(texte):
        dates.append((m.group(0), _forme_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.span()))
    for m in RE_DATE_NUM.finditer(texte):
        a, b, an = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dates.append((m.group(0), _forme_date(an, b, a) | _forme_date(an, a, b), m.span()))
    for m in RE_DATE_TEXTE.finditer(texte):
        mois = MOIS.get(replier(m.group(2)))
        if mois is None:
            continue
        jour = m.group(1) or m.group(3)
        jour_n = 1 if jour == "1er" else (int(jour) if jour else None)
        dates.append((m.group(0), _forme_date(int(m.group(4)), mois, jour_n), m.span()))
    return [date for date in dates if date[1]]


def _forme_date(annee: int, mois: int, jour: int | None) -> set[str]:
    if not 1 <= mois <= 12 or (jour is not None and not 1 <= jour <= 31):
        return set()
    return {f"{annee:04d}-{mois:02d}" + ("" if jour is None else f"-{jour:02d}")}


def extraire_nombres(texte: str, exclure: list[tuple[int, int]]) -> list[tuple[str, set[str]]]:
    """Nombres hors des empans exclus (dates), avec leurs interprétations."""
    resultat = []
    for m in RE_NOMBRE.finditer(texte):
        if any(a <= m.start() < b for a, b in exclure):
            continue
        valeurs = interpretations_nombre(m.group(0))
        if valeurs:
            resultat.append((m.group(0), valeurs))
    return resultat


def extraire_noms(phrase: str) -> list[str]:
    """Noms propres probables : suites de mots capitalisés (hors tête de phrase isolée) et sigles."""
    noms: list[str] = []
    debut_texte = len(phrase) - len(phrase.lstrip(" \t«\"“(-*•"))
    for m in RE_NOM_PROPRE.finditer(phrase):
        mots = m.group(0).split()
        if m.start() <= debut_texte or replier(mots[0]) in MOTS_VIDES:
            mots = mots[1:]
        if mots and not (len(mots) == 1 and replier(mots[0]) in MOTS_VIDES):
            noms.append(" ".join(mots))
    noms.extend(m.group(0) for m in RE_SIGLE.finditer(phrase))
    return list(dict.fromkeys(n for n in noms if len(n) > 1))


def identifiants(texte: str) -> set[str]:
    """Mots pleins d'allure d'identifiant : entre accents graves, ou mêlant lettres et chiffres ou _."""
    bruts = [m.group(1) for m in RE_CODE.finditer(texte)] + [m.group(0) for m in RE_IDENTIFIANT.finditer(texte)]
    return {mot for brut in bruts for mot in mots_pleins(brut) if not mot.isdigit()}


def negatif(texte: str) -> bool:
    """Le texte porte-t-il une marque de négation (ne, pas, jamais, not, never...) ?"""
    return any(m in NEGATIONS for m in RE_MOT.findall(replier(texte)))


def nom_present(nom: str, texte_replie: str) -> bool:
    """Le nom (replié, blancs souples) figure-t-il dans le texte des sources ?"""
    motif = r"\s+".join(re.escape(replier(mot)) for mot in nom.split())
    return re.search(r"(?<!\w)" + motif + r"(?!\w)", texte_replie) is not None


# --------------------------------------------------------------------------
# Découpage en phrases


def _est_abreviation(texte: str, position: int) -> bool:
    """Le point en `position` termine-t-il une abréviation ou une initiale ?"""
    debut = position
    while debut > 0 and (texte[debut - 1].isalnum() or texte[debut - 1] in ".°"):
        debut -= 1
    mot = texte[debut:position].lower()
    return mot in ABREVIATIONS or (len(mot) == 1 and mot.isalpha())


def decouper_bloc(bloc: str, ligne: int) -> list[Phrase]:
    """Phrases d'un bloc de texte continu."""
    phrases, debut = [], 0
    for m in RE_FIN_PHRASE.finditer(bloc):
        if m.group(1) == "." and _est_abreviation(bloc, m.start(1)):
            continue
        morceau = bloc[debut:m.end(1)].strip()
        if morceau:
            phrases.append(Phrase(morceau, ligne + bloc.count("\n", 0, debut)))
        debut = m.end()
    reste = bloc[debut:].strip()
    if reste:
        phrases.append(Phrase(reste, ligne + bloc.count("\n", 0, debut)))
    return phrases


def _blocs(texte: str, reponse: bool) -> Iterator[tuple[str, int]]:
    """Blocs (paragraphes, éléments de liste) avec leur ligne ; titres et code sautés pour une réponse."""
    courant: list[str] = []
    depart, dans_code = 1, False
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        if reponse and RE_CLOTURE.match(ligne):
            if courant:
                yield "\n".join(courant), depart
                courant = []
            dans_code = not dans_code
            continue
        nouveau = not ligne.strip() or RE_PUCE.match(ligne) or RE_TITRE.match(ligne)
        if nouveau and courant:
            yield "\n".join(courant), depart
            courant = []
        if dans_code or not ligne.strip() or (reponse and RE_TITRE.match(ligne)):
            continue
        if not courant:
            depart = numero
        courant.append(RE_PUCE.sub("", ligne, count=1) if reponse else ligne)
    if courant:
        yield "\n".join(courant), depart


def decouper_phrases(texte: str, reponse: bool) -> list[Phrase]:
    """Toutes les phrases d'un texte."""
    return [p for bloc, ligne in _blocs(texte, reponse) for p in decouper_bloc(bloc, ligne)]


# --------------------------------------------------------------------------
# Lecture


def _nom_affiche(chemin: Path, racine: Path) -> str:
    try:
        return chemin.resolve().relative_to(racine.resolve()).as_posix()
    except ValueError:
        return chemin.as_posix()


def lire_texte(chemin: Path, taille_max: int) -> str:
    """Lecture bornée en utf-8 ; ErreurEntree si binaire, trop gros ou mal encodé."""
    if not chemin.is_file():
        raise ErreurEntree(f"pas un fichier : {chemin}")
    taille = chemin.stat().st_size
    if taille > taille_max:
        raise ErreurEntree(f"{chemin} : {taille} octets > --taille-max-fichier {taille_max}")
    brut = chemin.read_bytes()
    if b"\x00" in brut:
        raise ErreurEntree(f"{chemin} : fichier binaire (octet nul)")
    try:
        return brut.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} : pas de l'utf-8 valide (octet {exc.start})") from exc


def lister_sources(chemins: list[Path], extensions: tuple[str, ...], exclu: Path) -> list[tuple[Path, bool]]:
    """(fichier, explicite) des sources, réponse exclue."""
    trouves: list[tuple[Path, bool]] = []
    for chemin in chemins:
        if not chemin.exists():
            raise ErreurEntree(f"source introuvable : {chemin}")
        if chemin.is_file():
            trouves.append((chemin, True))
            continue
        for fichier in sorted(chemin.rglob("*")):
            if any(p in DOSSIERS_IGNORES for p in fichier.relative_to(chemin).parts[:-1]):
                continue
            if fichier.is_file() and fichier.suffix.lower() in extensions:
                trouves.append((fichier, False))
    return [(f, e) for f, e in trouves if f.resolve() != exclu.resolve()]


def construire_corpus(sources: list[tuple[Path, bool]], args: argparse.Namespace,
                      racine: Path) -> tuple[Corpus, list[str], list[dict[str, str]]]:
    """Passages, idf, texte replié, nombres et dates de toutes les sources lisibles."""
    passages: list[Passage] = []
    lus: list[str] = []
    ignores: list[dict[str, str]] = []
    textes: list[str] = []
    for chemin, explicite in sources:
        nom = _nom_affiche(chemin, racine)
        try:
            texte = lire_texte(chemin, args.taille_max_fichier)
        except (ErreurEntree, OSError) as exc:
            if explicite:
                raise ErreurEntree(str(exc)) from exc
            ignores.append({"chemin": nom, "raison": str(exc)})
            continue
        lus.append(nom)
        textes.append(texte)
        passages.extend(_passages(nom, decouper_phrases(texte, reponse=False), args.fenetre))
    return _indexer(passages, "\n".join(textes)), lus, ignores


def _passages(nom: str, phrases: list[Phrase], fenetre: int) -> Iterator[Passage]:
    for i in range(len(phrases)):
        groupe = phrases[i:i + fenetre]
        texte = " ".join(p.texte for p in groupe)
        sequence = mots_pleins(texte)
        if sequence:
            yield Passage(nom, groupe[0].ligne, texte, sequence, frozenset(sequence),
                          tuple(p.texte for p in groupe))
        if i + fenetre >= len(phrases):
            break


def _indexer(passages: list[Passage], texte_brut: str) -> Corpus:
    """Index BM25 des passages et ensembles de faits des sources."""
    postings: dict[str, list[int]] = {}
    for numero, passage in enumerate(passages):
        for terme in passage.termes:
            postings.setdefault(terme, []).append(numero)
    total = len(passages)
    idf = {t: math.log(1 + (total - len(p) + 0.5) / (len(p) + 0.5)) for t, p in postings.items()}
    moyenne = sum(len(p.sequence) for p in passages) / total if total else 0.0
    dates = extraire_dates(texte_brut)
    nombres = {v for _, valeurs in extraire_nombres(texte_brut, []) for v in valeurs}
    nombres |= {NOMBRES_EN_LETTRES[m] for m in RE_MOT.findall(replier(texte_brut)) if m in NOMBRES_EN_LETTRES}
    return Corpus(passages, idf, math.log(1 + (total + 0.5) / 0.5), moyenne, postings,
                  replier(texte_brut), frozenset(nombres), frozenset(f for _, fs, _ in dates for f in fs))


# --------------------------------------------------------------------------
# Évaluation d'une phrase


def _candidats(corpus: Corpus, termes: tuple[str, ...]) -> list[int]:
    """Passages les mieux classés par BM25 pour les mots de la phrase."""
    scores: dict[int, float] = {}
    for terme in set(termes):
        for numero in corpus.postings.get(terme, ()):
            passage = corpus.passages[numero]
            tf = passage.sequence.count(terme)
            norme = K1 * (1 - B + B * len(passage.sequence) / corpus.longueur_moyenne)
            scores[numero] = scores.get(numero, 0.0) + corpus.idf[terme] * tf / (tf + norme)
    return sorted(scores, key=lambda n: (-scores[n], n))[:CANDIDATS_BM25]


def _noter(termes: tuple[str, ...], passage: Passage, corpus: Corpus,
           sequenceur: Callable[[tuple[str, ...], tuple[str, ...]], int]) -> tuple[float, float, float]:
    """(score, couverture, séquence) d'une phrase face à un passage."""
    poids = {t: corpus.idf.get(t, corpus.idf_absent) for t in set(termes)}
    total = sum(poids.values())
    couverture = sum(v for t, v in poids.items() if t in passage.termes) / total
    sequence = sequenceur(termes, passage.sequence) / len(termes)
    return POIDS_COUVERTURE * couverture + POIDS_SEQUENCE * sequence, couverture, sequence


def evaluer_phrase(phrase: Phrase, corpus: Corpus, args: argparse.Namespace,
                   sequenceur: Callable[[tuple[str, ...], tuple[str, ...]], int]) -> dict[str, Any]:
    """Meilleur passage, score, faits absents et verdict d'une phrase."""
    termes = mots_pleins(phrase.texte)
    meilleur, notes = None, (0.0, 0.0, 0.0)
    for numero in _candidats(corpus, termes):
        evaluation = _noter(termes, corpus.passages[numero], corpus, sequenceur)
        if evaluation[0] > notes[0]:
            meilleur, notes = corpus.passages[numero], evaluation
    faits = _faits_absents(phrase.texte, meilleur, corpus)
    faits["mots_hors_sources"] = sorted({t for t in termes if t not in corpus.idf})
    faits["identifiants_hors_sources"] = sorted(identifiants(phrase.texte) - set(corpus.idf))
    discordance = meilleur is not None and negatif(phrase.texte) != negatif(_phrase_proche(termes, meilleur))
    verdict, raisons = _verdict(notes[0], faits, discordance, args)
    return {
        "ligne": phrase.ligne, "texte": phrase.texte, "verdict": verdict,
        "score": round(notes[0], 4), "couverture": round(notes[1], 4), "sequence": round(notes[2], 4),
        "source": meilleur.source if meilleur else None, "ligne_source": meilleur.ligne if meilleur else None,
        "extrait": (" ".join(meilleur.texte.split())[:300] if meilleur else None),
        "termes_absents": _termes_absents(termes, meilleur, corpus),
        "raisons": raisons, "faits_absents": faits,
    }


def _phrase_proche(termes: tuple[str, ...], passage: Passage) -> str:
    """Phrase du passage qui partage le plus de mots pleins avec la phrase évaluée."""
    cible = set(termes)
    return max(passage.phrases, key=lambda p: len(cible & set(mots_pleins(p))))


def _termes_absents(termes: tuple[str, ...], passage: Passage | None, corpus: Corpus) -> list[str]:
    absents = {t for t in termes if passage is None or t not in passage.termes}
    return sorted(absents, key=lambda t: (-corpus.idf.get(t, corpus.idf_absent), t))[:8]


def _faits_absents(texte: str, passage: Passage | None, corpus: Corpus) -> dict[str, list[str]]:
    """Nombres, dates et noms de la phrase absents des sources ou du meilleur passage."""
    dates = extraire_dates(texte)
    nombres = extraire_nombres(texte, [span for _, _, span in dates])
    local_nombres: set[str] = set()
    if passage is not None:
        local_nombres = {v for _, vs in extraire_nombres(passage.texte, []) for v in vs}
        local_nombres |= {NOMBRES_EN_LETTRES[m] for m in RE_MOT.findall(replier(passage.texte))
                          if m in NOMBRES_EN_LETTRES}
    return {
        "nombres_hors_sources": [b for b, vs in nombres if not vs & corpus.nombres],
        "nombres_hors_passage": [b for b, vs in nombres if vs & corpus.nombres and not vs & local_nombres],
        "dates_hors_sources": [b for b, fs, _ in dates if not fs & corpus.dates],
        "noms_hors_sources": [n for n in extraire_noms(texte) if not nom_present(n, corpus.texte_replie)],
    }


def _verdict(score: float, faits: dict[str, list[str]], discordance: bool,
             args: argparse.Namespace) -> tuple[str, list[str]]:
    """Verdict par seuils, puis plafonds dus aux faits absents et à la négation discordante."""
    raisons = []
    verdict = VERDICT_APPUYEE if score >= args.seuil_appuyee else (
        VERDICT_FAIBLE if score >= args.seuil_faible else VERDICT_NON)
    if score < args.seuil_faible:
        raisons.append(f"score {score:.3f} < {args.seuil_faible}")
    if faits["nombres_hors_sources"] or faits["dates_hors_sources"]:
        verdict = VERDICT_NON
        raisons.append("nombre ou date absent de toutes les sources : "
                       + ", ".join(faits["nombres_hors_sources"] + faits["dates_hors_sources"]))
        return verdict, raisons
    if verdict == VERDICT_APPUYEE and (faits["nombres_hors_passage"] or faits["noms_hors_sources"]):
        verdict = VERDICT_FAIBLE
        raisons.append("absent du meilleur passage ou des sources : "
                       + ", ".join(faits["nombres_hors_passage"] + faits["noms_hors_sources"]))
    if verdict == VERDICT_APPUYEE and faits["identifiants_hors_sources"]:
        verdict = VERDICT_FAIBLE
        raisons.append("identifiants absents de toutes les sources : "
                       + ", ".join(faits["identifiants_hors_sources"][:6]))
    if verdict == VERDICT_APPUYEE and discordance:
        verdict = VERDICT_FAIBLE
        raisons.append("négation présente d'un seul côté (phrase ou phrase source la plus proche)")
    return verdict, raisons


# --------------------------------------------------------------------------
# Orchestration et sorties


def _indices(phrases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Indices d'hallucination : faits de la réponse absents de toutes les sources."""
    indices: dict[str, list[dict[str, Any]]] = {"nombres": [], "dates": [], "noms_propres": []}
    for rang, phrase in enumerate(phrases, start=1):
        faits = phrase["faits_absents"]
        for cle, champ in (("nombres", "nombres_hors_sources"), ("dates", "dates_hors_sources"),
                           ("noms_propres", "noms_hors_sources")):
            indices[cle].extend({"valeur": v, "phrase": rang} for v in faits[champ])
    return indices


def presenter_humain(sortie: dict[str, Any]) -> None:
    """Affichage lisible."""
    print(f"{sortie['denominateur']} phrase(s) — {sortie['bilan']} — moteur {sortie['moteur']}")
    print(f"({sortie['avertissement']})")
    for rang, phrase in enumerate(sortie["phrases"], start=1):
        print(f"{rang:>3}. [{phrase['verdict']}] {phrase['score']:.3f}  {phrase['texte'][:110]}")
        if phrase["source"]:
            print(f"       ↳ {phrase['source']}:{phrase['ligne_source']}  {phrase['extrait'][:100]}")
        for raison in phrase["raisons"]:
            print(f"       ! {raison}")
    libelles = {"nombres": "nombres absents", "dates": "dates absentes", "noms_propres": "noms propres absents"}
    for cle, valeurs in sortie["indices_hallucination"].items():
        if valeurs:
            print(f"{libelles[cle]} des sources : "
                  + ", ".join(f"{v['valeur']} (phrase {v['phrase']})" for v in valeurs))


def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    parser = argparse.ArgumentParser(
        description="Vérifie, phrase par phrase, qu'une réponse générée s'appuie sur les sources "
                    "(indice lexical) et liste les nombres, dates et noms propres absents des sources.",
        epilog="Exemple : python3 verifier_ancrage_reponse.py reponse.md docs/ notes.txt --json")
    parser.add_argument("fichier_reponse", type=Path, help="fichier contenant la réponse à vérifier")
    parser.add_argument("sources", type=Path, nargs="+", help="fichiers ou dossiers sources")
    parser.add_argument("--seuil-appuyee", type=float, default=0.6, metavar="S",
                        help="score minimal d'une phrase APPUYÉE (défaut 0.6)")
    parser.add_argument("--seuil-faible", type=float, default=0.35, metavar="S",
                        help="score minimal d'une phrase FAIBLE (défaut 0.35)")
    parser.add_argument("--fenetre", type=int, default=2, help="phrases consécutives par passage source")
    parser.add_argument("--extensions", default=EXTENSIONS_DEFAUT, help="extensions lues dans un dossier source")
    parser.add_argument("--taille-max-fichier", type=int, default=20_000_000, metavar="OCTETS",
                        help="taille maximale lue par fichier")
    parser.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=Path,
                        help=f"base des chemins relatifs (défaut : répertoire courant ; outil dans {RACINE})")
    return parser


def valider(args: argparse.Namespace) -> None:
    if not 0 <= args.seuil_faible <= args.seuil_appuyee <= 1:
        raise ErreurEntree("il faut 0 ≤ --seuil-faible ≤ --seuil-appuyee ≤ 1")
    if args.fenetre < 1 or args.taille_max_fichier < 1:
        raise ErreurEntree("--fenetre et --taille-max-fichier doivent être ≥ 1")


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Lit, indexe, évalue ; renvoie la sortie et le code."""
    valider(args)
    racine = args.racine if args.racine is not None else Path.cwd()
    reponse = args.fichier_reponse if args.fichier_reponse.is_absolute() else racine / args.fichier_reponse
    if not reponse.exists():
        raise ErreurEntree(f"réponse introuvable : {reponse}")
    texte = lire_texte(reponse, args.taille_max_fichier)
    extensions = tuple(e.strip().lower() for e in args.extensions.split(",") if e.strip())
    chemins = [c if c.is_absolute() else racine / c for c in args.sources]
    corpus, lus, ignores = construire_corpus(lister_sources(chemins, extensions, reponse), args, racine)
    sequenceur, moteur = charger_sequenceur()
    phrases = [p for p in decouper_phrases(texte, reponse=True) if mots_pleins(p.texte)]
    evaluees = [evaluer_phrase(p, corpus, args, sequenceur) for p in phrases] if corpus.passages else []
    sortie = construire_sortie(evaluees, corpus, lus, ignores, moteur, args)
    if not evaluees:
        return sortie, 3
    return sortie, 1 if sortie["bilan"][VERDICT_NON] else 0


def construire_sortie(evaluees: list[dict[str, Any]], corpus: Corpus, lus: list[str],
                      ignores: list[dict[str, str]], moteur: str, args: argparse.Namespace) -> dict[str, Any]:
    """Objet JSON rendu."""
    bilan = Counter(e["verdict"] for e in evaluees)
    return {
        "outil": "verifier_ancrage_reponse", "moteur": moteur, "avertissement": AVERTISSEMENT,
        "denominateur": len(evaluees),
        "examines": [f"phrase {i}: {e['texte'][:60]}" for i, e in enumerate(evaluees[:MAX_EXAMINES], 1)],
        "examines_tronques": len(evaluees) > MAX_EXAMINES,
        "bilan": {v: bilan.get(v, 0) for v in (VERDICT_APPUYEE, VERDICT_FAIBLE, VERDICT_NON)},
        "phrases": evaluees, "indices_hallucination": _indices(evaluees),
        "sources": {"fichiers": len(lus), "passages": len(corpus.passages),
                    "fichiers_examines": lus[:MAX_EXAMINES], "ignores": ignores[:MAX_EXAMINES]},
        "parametres": {"seuil_appuyee": args.seuil_appuyee, "seuil_faible": args.seuil_faible,
                       "fenetre": args.fenetre, "poids": [POIDS_COUVERTURE, POIDS_SEQUENCE],
                       "classement_candidats": CLASSEMENT},
        "contrat": extraire_contrat(__doc__ or ""),
    }


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_analyseur().parse_args(argv)
    try:
        sortie, code = executer(args)
    except ErreurEntree as exc:
        print(f"verifier_ancrage_reponse : {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"verifier_ancrage_reponse : erreur d'entrée/sortie : {exc}", file=sys.stderr)
        return 2
    if code == 3:
        print("verifier_ancrage_reponse : dénominateur nul — rien à examiner (aucune phrase évaluable "
              "ou aucune source lisible)", file=sys.stderr)
    elif code == 1:
        print(f"verifier_ancrage_reponse : {sortie['bilan'][VERDICT_NON]} phrase(s) non appuyée(s) "
              "(indice lexical)", file=sys.stderr)
    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    elif code != 3:
        presenter_humain(sortie)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
