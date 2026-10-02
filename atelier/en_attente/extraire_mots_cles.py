"""Extrait les termes-clés d'un texte (RAKE + fréquence) ou d'un corpus (TF-IDF entre fichiers),
avec score, fréquence et position de première occurrence de chaque terme.

Mesuré dans cette session (python eval_mots_cles.py, bloc-notes de session) : sur les 142
fiches docs/*.md du dépôt, écrites sur un même gabarit, le nom de l'outil décrit figure dans
les 3 premiers termes TF-IDF du corpus pour 120 fiches, mais pour 0 fiche quand chacune est
traitée seule (RAKE) et pour 1 seule avec yake 0.7.3 : isolé, le gabarit écrase le sujet.

QUESTION
    Quels sont les termes-clés de ce texte ou de ce corpus ?
MESURE
    Le texte est coupé en phrases candidates aux ponctuations, chiffres, symboles et
    mots vides (listes FR et EN embarquées, plus mots-clés de langages pour le code).
    Document seul : chaque mot reçoit le score RAKE degré/fréquence ; chaque n-gramme
    de 1 à 3 mots d'une phrase candidate reçoit la somme des scores de ses mots
    multipliée par sa fréquence ; un n-gramme toujours inclus dans un plus long de même
    fréquence est retiré. Dossier : chaque fichier texte est un document ;
    score TF-IDF = (occurrences / n-grammes du document) x (ln((1+N)/(1+df)) + 1).
    Pour chaque terme : score, fréquence, nombre de mots, position et ligne de sa
    première occurrence.
HYPOTHÈSES
    Le texte est en français ou en anglais (ou du code) ; ailleurs les mots vides ne
    sont pas reconnus et les phrases candidates s'allongent. Les termes importants sont
    répétés ou portés par des groupes nominaux.
LIMITES
    Pas de lemmatisation : « fichier » et « fichiers » sont deux termes. Pas de sens :
    un terme fréquent mais banal du domaine peut passer devant un terme rare et décisif.
    En TF-IDF, avec un seul fichier l'idf est constant et le classement se réduit à la
    fréquence. yake (facultatif) ne sert qu'au document seul.
CONTRE-EXEMPLES
    Constaté en mode corpus : pour docs/analyser_portees.md, « portées », « portée » et
    « portees » sortent comme trois termes distincts ; pour docs/publier_vitrine.md, le
    nom de l'outil n'est pas dans les 6 premiers termes (titre, branche, licence, auteur
    passent devant) ; pour docs/afficher_progression.md, « description » (7 fois, venu
    d'un tableau d'options) passe devant le nom de l'outil (4 fois).
INVOCATION
    {outil} {fichier} --json
    {outil} {dossier} --json
DOMAINE
    Prose technique, documentation, réponses de LLM et code source, en français ou en
    anglais ; fichiers texte UTF-8, de quelques lignes à quelques mégaoctets.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import yake as _YAKE
except ImportError:
    _YAKE = None

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
INTITULES = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
             TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)

MOTEUR_STDLIB = "stdlib"
MOTEUR_TIERS = "yake"
METHODE_RAKE = "RAKE"
METHODE_TFIDF = "TF-IDF"
ENCODAGE_ATTENDU = "UTF-8"

CODE_RIEN = 0
CODE_USAGE = 2
CODE_VIDE = 3

NGRAMME_MAX = 3
TERMES_DEFAUT = 15
OCTETS_MAX_DEFAUT = 16 * 1024 * 1024
FICHIERS_MAX = 2000
EXAMINES_MAX = 50

EXTENSIONS_TEXTE = frozenset(
    ".txt .md .markdown .rst .tex .org .adoc .html .htm .xml .csv .json .yaml .yml .toml .ini "
    ".py .pyi .js .ts .java .go .rs .c .h .cpp .hpp .cs .rb .php .sh .sql .r .kt .swift".split())
EXTENSIONS_CODE = frozenset(".py .pyi .js .ts .java .go .rs .c .h .cpp .hpp .cs .rb .php .sh .sql .r .kt .swift".split())
DOSSIERS_IGNORES = frozenset({"__pycache__", "node_modules", "site-packages", "venv", "dist", "build"})

MOTS_VIDES_FR = frozenset("""
a à afin ai aie aient aies ait alors as au aucun aucune aupres auquel aura aurai auraient aurais
aurait auras aurez auriez aurions aurons auront aussi autre autres aux auxquelles auxquels avaient
avais avait avant avec avez aviez avions avoir avons ayant ayez ayons bon c ça car ce ceci cela celle
celles celui cependant certain certaine certaines certains ces cet cette ceux chaque chez ci comme
comment contre d dans de depuis des dès desquelles desquels deux devant doit donc dont du duquel
durant elle elles en encore entre es est et étaient étais était étant été êtes étiez étions être eu
eue eues eurent eus eut eux fait faire fais faut fois font furent fut ici il ils j je jusqu l la là
laquelle le lequel les lesquelles lesquels leur leurs lors lorsque lui m ma mais me même mêmes mes
moi moins mon n ne ni non nos notre nous on ont or ou où par parce pas peu peut peuvent plus plutôt
pour pourquoi qu quand que quel quelle quelles quels qui quoi s sa sans se sera seront ses si sien
sinon soi soit sommes son sont sous suis sur t ta tandis tant te tel telle telles tels tes toi ton
tous tout toute toutes très tu un une unes uns vers via voici voilà vos votre vous y
""".split())
MOTS_VIDES_EN = frozenset("""
a about above after again against all also am an and any are aren as at be because been before being
below between both but by can cannot could couldn did didn do does doesn doing don down during each
either else even ever every few for from further get gets got had hadn has hasn have haven having he
her here hers herself him himself his how however i if in into is isn it its itself just let ll may
me might more most must mustn my myself neither no nor not now of off often on once one only or other
ought our ours ourselves out over own per rather re same shall shan she should shouldn since so some
such than that the their theirs them themselves then there these they this those though through thus
to too under until up upon us use used uses using very via was wasn we were weren what when where
whether which while who whom whose why will with within without won would wouldn yet you your yours
yourself yourselves don't doesn't didn't isn't aren't wasn't weren't can't couldn't won't
wouldn't shouldn't haven't hasn't hadn't i'm i've i'd i'll you're you've you'll we're we've they're
they've let's
""".split())
MOTS_VIDES_CODE = frozenset("""
and as assert async await break case catch class const continue def default del elif else enum
except export extends false final finally for from func function if impl import in int interface
is lambda let list dict str bool float none not null or pass print private protected public raise
return self static struct super switch this throw true try type typeof var void while with yield
""".split())

RE_MOT = re.compile(r"[^\W\d_]\w*(?:[-'’]\w+)*")
RE_ELISION = re.compile(r"^(?:l|d|j|m|n|s|t|c|qu|jusqu|lorsqu|puisqu)['’](?=\w)")
RE_POSSESSIF = re.compile(r"['’]s$")
RE_COUPURE = re.compile(r"\S|\n[ \t]*\n")
RE_BALISE = re.compile(r"<[^>]{0,500}>")
RE_URL = re.compile(r"(?:https?|ftp)://\S+|www\.\S+")


class ErreurEntree(Exception):
    """Entrée illisible ou invalide : porte le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


# -- découpage ---------------------------------------------------------------

def choisir_langue(texte: str, langue: str) -> str:
    """Langue dominante des mots vides (« fr+en » à égalité) : sert à yake ; les mots vides
    appliqués en mode auto sont l'union FR+EN, car un texte technique mêle les deux."""
    if langue != "auto":
        return langue
    mots = [m.lower() for m in RE_MOT.findall(texte[:200_000])]
    fr = sum(m in MOTS_VIDES_FR for m in mots)
    en = sum(m in MOTS_VIDES_EN for m in mots)
    return "fr" if fr > en else "en" if en > fr else "fr+en"


def mots_vides(langue: str, code: bool) -> frozenset[str]:
    vides = {"fr": MOTS_VIDES_FR, "en": MOTS_VIDES_EN}.get(langue, MOTS_VIDES_FR | MOTS_VIDES_EN)
    return vides | MOTS_VIDES_CODE if code else vides


def normaliser(mot: str) -> str:
    """Minuscules, apostrophe droite, sans élision française ni possessif anglais."""
    return RE_POSSESSIF.sub("", RE_ELISION.sub("", mot.lower().replace(chr(0x2019), "'")))


def phrases_candidates(texte: str, vides: frozenset[str]) -> tuple[list[list[tuple[str, int]]], dict[str, Counter[str]]]:
    """Suites de mots pleins (mot normalisé, position) ; et formes écrites de chaque mot."""
    phrases: list[list[tuple[str, int]]] = []
    courante: list[tuple[str, int]] = []
    surfaces: dict[str, Counter[str]] = {}
    fin_precedente = 0
    texte = RE_URL.sub(lambda m: " " * len(m.group()), texte)
    for trouve in RE_MOT.finditer(texte):
        if RE_COUPURE.search(texte, fin_precedente, trouve.start()):
            courante = clore(phrases, courante)
        fin_precedente = trouve.end()
        mot = normaliser(trouve.group())
        suffixe = trouve.start() >= 2 and texte[trouve.start() - 1] == "." and texte[trouve.start() - 2].isalnum()
        if len(mot) < 2 or mot in vides or suffixe:
            courante = clore(phrases, courante)
            continue
        sans_elision = RE_ELISION.sub("", trouve.group())
        courante.append((mot, trouve.end() - len(sans_elision)))
        surfaces.setdefault(mot, Counter())[RE_POSSESSIF.sub("", sans_elision)] += 1
    clore(phrases, courante)
    return phrases, surfaces


def clore(phrases: list[list[tuple[str, int]]], courante: list[tuple[str, int]]) -> list[tuple[str, int]]:
    if courante:
        phrases.append(courante)
    return []


def ngrammes(phrases: list[list[tuple[str, int]]]) -> tuple[Counter[tuple[str, ...]], dict[tuple[str, ...], int]]:
    """Occurrences et première position de chaque n-gramme (1 à NGRAMME_MAX) des phrases."""
    compte: Counter[tuple[str, ...]] = Counter()
    premiere: dict[tuple[str, ...], int] = {}
    for phrase in phrases:
        mots = [m for m, _ in phrase]
        for n in range(1, NGRAMME_MAX + 1):
            for i in range(len(mots) - n + 1):
                cle = tuple(mots[i:i + n])
                compte[cle] += 1
                premiere.setdefault(cle, phrase[i][1])
    return compte, premiere


# -- document seul : RAKE + fréquence -------------------------------------------

def scores_rake(phrases: list[list[tuple[str, int]]]) -> dict[str, float]:
    frequence: Counter[str] = Counter()
    degre: Counter[str] = Counter()
    for phrase in phrases:
        for mot, _ in phrase:
            frequence[mot] += 1
            degre[mot] += len(phrase)
    return {mot: degre[mot] / frequence[mot] for mot in frequence}


def classer_rake(phrases: list[list[tuple[str, int]]]) -> list[tuple[tuple[str, ...], float, int, int]]:
    rake = scores_rake(phrases)
    compte, premiere = ngrammes(phrases)
    classes = [(cle, sum(rake[m] for m in cle) * n, n, premiere[cle]) for cle, n in compte.items()]
    return elaguer(sorted(classes, key=lambda t: (-t[1], t[3])), compte)


def elaguer(classes: list[tuple[tuple[str, ...], float, int, int]],
            compte: Counter[tuple[str, ...]]) -> list[tuple[tuple[str, ...], float, int, int]]:
    """Retire un n-gramme toujours inclus dans un n-gramme plus long de même fréquence."""
    absorbes: set[tuple[str, ...]] = set()
    for cle, n in compte.items():
        if len(cle) < 2:
            continue
        for k in range(1, len(cle)):
            for i in range(len(cle) - k + 1):
                partie = cle[i:i + k]
                if compte[partie] == n:
                    absorbes.add(partie)
    return [c for c in classes if c[0] not in absorbes]


# -- corpus : TF-IDF ------------------------------------------------------------

def classer_tfidf(documents: list[dict[str, Any]]) -> None:
    """Ajoute à chaque document ses termes classés par TF-IDF."""
    nombre = len(documents)
    df: Counter[tuple[str, ...]] = Counter()
    for doc in documents:
        df.update(doc["_compte"].keys())
    for doc in documents:
        total = sum(doc["_compte"].values()) or 1
        classes = [(cle, (n / total) * (math.log((1 + nombre) / (1 + df[cle])) + 1), n, doc["_premiere"][cle])
                   for cle, n in doc["_compte"].items()]
        doc["_classes"] = elaguer(sorted(classes, key=lambda t: (-t[1], t[3])), doc["_compte"])
        doc["_df"] = df


# -- moteur tiers ------------------------------------------------------------------

def classer_yake(texte: str, langue: str, nombre: int) -> list[tuple[tuple[str, ...], float, int, int]]:
    extracteur = _YAKE.KeywordExtractor(lan=langue if langue in ("fr", "en") else "en", n=NGRAMME_MAX,
                                        top=nombre)
    resultat = []
    for terme, score in extracteur.extract_keywords(texte):
        motif = re.compile(r"(?<!\w)" + r"\W+".join(map(re.escape, terme.split())) + r"(?!\w)",
                           re.IGNORECASE)
        occurrences = [m.start() for m in motif.finditer(texte)]
        cle = tuple(normaliser(m) for m in terme.split())
        resultat.append((cle, score, len(occurrences), occurrences[0] if occurrences else -1))
    return resultat


# -- documents ------------------------------------------------------------------

def preparer_document(nom: str, texte: str, args: argparse.Namespace) -> dict[str, Any]:
    extension = Path(nom).suffix.lower()
    if extension in (".html", ".htm", ".xml"):
        texte = RE_BALISE.sub(lambda m: " " * len(m.group()), texte)
    langue = choisir_langue(texte, args.langue)
    vides = mots_vides(args.langue if args.langue != "auto" else "fr+en", extension in EXTENSIONS_CODE)
    phrases, surfaces = phrases_candidates(texte, vides)
    compte, premiere = ngrammes(phrases)
    return {"nom": nom, "_texte": texte, "_phrases": phrases, "_surfaces": surfaces,
            "_compte": compte, "_premiere": premiere, "langue_mots_vides": langue,
            "code_source": extension in EXTENSIONS_CODE,
            "mots_pleins": sum(len(p) for p in phrases), "phrases_candidates": len(phrases)}


def surface(cle: tuple[str, ...], surfaces: dict[str, Counter[str]]) -> str:
    """Forme écrite la plus fréquente de chaque mot (garde « Python », « JSON »)."""
    formes = []
    for mot in cle:
        candidates = surfaces.get(mot)
        formes.append(max(candidates.items(), key=lambda kv: (kv[1], kv[0]))[0] if candidates else mot)
    return " ".join(formes)


def ligne_de(texte: str, position: int) -> int:
    return texte.count("\n", 0, max(position, 0)) + 1


def finaliser(doc: dict[str, Any], nombre: int, moteur: str) -> dict[str, Any]:
    termes = []
    for cle, score, frequence, position in doc["_classes"][:nombre]:
        terme = {"terme": surface(cle, doc["_surfaces"]), "score": round(score, 6), "frequence": frequence,
                 "mots": len(cle), "premiere_position": position,
                 "ligne": ligne_de(doc["_texte"], position) if position >= 0 else None}
        if "_df" in doc:
            terme["documents_contenant"] = doc["_df"][cle]
        termes.append(terme)
    publics = {k: v for k, v in doc.items() if not k.startswith("_")}
    return publics | {"moteur": moteur, "termes": termes}


def comparer(doc: dict[str, Any], langue: str, nombre: int) -> dict[str, Any]:
    """Recouvrement des listes stdlib (RAKE) et yake, sans tenir compte de la casse."""
    rake = [" ".join(c[0]) for c in classer_rake(doc["_phrases"])[:nombre]]
    tiers = [" ".join(c[0]) for c in classer_yake(doc["_texte"], langue, nombre)]
    communs = [t for t in rake if t in tiers]
    union = set(rake) | set(tiers)
    return {"moteur": MOTEUR_TIERS, "communs": communs,
            "jaccard": round(len(communs) / len(union), 4) if union else 0.0,
            "seulement_stdlib": [t for t in rake if t not in tiers],
            "seulement_yake": [t for t in tiers if t not in rake]}


def analyser(entrees: list[tuple[str, str]], args: argparse.Namespace) -> list[dict[str, Any]]:
    documents = [preparer_document(nom, texte, args) for nom, texte in entrees]
    documents = [d for d in documents if d["_compte"]]
    if args.mode == "corpus":
        classer_tfidf(documents)
        return [finaliser(d, args.nombre, MOTEUR_STDLIB) for d in documents]
    resultats = []
    for doc in documents:
        langue = doc["langue_mots_vides"].split("+")[0]
        if args.moteur_effectif == MOTEUR_TIERS:
            doc["_classes"] = classer_yake(doc["_texte"], langue, args.nombre)
        else:
            doc["_classes"] = classer_rake(doc["_phrases"])
        resultat = finaliser(doc, args.nombre, args.moteur_effectif)
        if args.comparer and _YAKE is not None:
            resultat["comparaison"] = comparer(doc, langue, args.nombre)
        resultats.append(resultat)
    return resultats


# -- entrées -------------------------------------------------------------------

def decoder(octets: bytes, nom: str) -> str:
    if b"\x00" in octets[:65536]:
        raise ErreurEntree(f"{nom} : fichier binaire (octet NUL), pas du texte")
    try:
        return octets.decode("utf-8-sig")
    except UnicodeDecodeError:
        print(f"{nom} : pas de l'{ENCODAGE_ATTENDU} valide, relu en cp1252", file=sys.stderr)
        return octets.decode("cp1252", "replace")


def lire_borne(chemin: Path, octets_max: int) -> bytes:
    with chemin.open("rb") as flux:
        octets = flux.read(octets_max + 1)
    if len(octets) > octets_max:
        raise ErreurEntree(f"{chemin} dépasse {octets_max} octets (--max-octets)")
    return octets


def lister_corpus(dossier: Path) -> list[Path]:
    fichiers = []
    for chemin in sorted(dossier.rglob("*")):
        parties = chemin.relative_to(dossier).parts
        if any(p.startswith(".") or p in DOSSIERS_IGNORES for p in parties):
            continue
        if chemin.is_file() and chemin.suffix.lower() in EXTENSIONS_TEXTE:
            fichiers.append(chemin)
    if len(fichiers) > FICHIERS_MAX:
        print(f"{len(fichiers)} fichiers : seuls les {FICHIERS_MAX} premiers sont examinés", file=sys.stderr)
    return fichiers[:FICHIERS_MAX]


def lire_corpus(dossier: Path, octets_max: int) -> list[tuple[str, str]]:
    entrees = []
    for chemin in lister_corpus(dossier):
        try:
            entrees.append((str(chemin.relative_to(dossier)), decoder(lire_borne(chemin, octets_max), str(chemin))))
        except (ErreurEntree, OSError) as erreur:
            print(f"ignoré : {erreur}", file=sys.stderr)
    return entrees


def collecter_entrees(args: argparse.Namespace, base: Path) -> list[tuple[str, str]]:
    if args.texte is not None:
        return [("<texte>", decoder(os.fsencode(args.texte), "<texte>"))]
    if args.chemin is None:
        raise ErreurEntree("rien à lire : donner un CHEMIN (fichier ou dossier), '-' ou --texte")
    if args.chemin == "-":
        octets = sys.stdin.buffer.read(args.max_octets + 1)
        if len(octets) > args.max_octets:
            raise ErreurEntree(f"entrée standard plus longue que {args.max_octets} octets (--max-octets)")
        return [("<stdin>", decoder(octets, "<stdin>"))]
    chemin = Path(args.chemin)
    chemin = chemin if chemin.is_absolute() else base / chemin
    if not chemin.exists():
        raise ErreurEntree(f"{chemin} : chemin inexistant")
    if chemin.is_dir():
        args.mode = "corpus"
        return lire_corpus(chemin, args.max_octets)
    return [(str(chemin), decoder(lire_borne(chemin, args.max_octets), str(chemin)))]


def choisir_moteur(demande: str) -> str:
    if demande == MOTEUR_TIERS and _YAKE is None:
        raise ErreurEntree("--moteur yake demandé mais le paquet yake est absent")
    if demande == "auto":
        if _YAKE is None:
            print(f"yake absent : mode dégradé, {METHODE_RAKE} + fréquence (stdlib) pour un document, "
                  f"{METHODE_TFIDF} pour un dossier", file=sys.stderr)
            return MOTEUR_STDLIB
        return MOTEUR_TIERS
    return demande


# -- sorties -----------------------------------------------------------------

def extraire_contrat(doc: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith((" ", "\t")):
            courant = tete
            sections[courant] = []
        elif courant is not None and tete:
            sections[courant].append(tete)
    return {cle.lower().replace("è", "e").replace("-", "_"): " ".join(v) for cle, v in sections.items()}


def termes_du_corpus(documents: list[dict[str, Any]], nombre: int) -> list[dict[str, Any]]:
    """Termes les plus répandus du corpus (présents dans le plus de documents)."""
    cumul: dict[str, list[float]] = {}
    for doc in documents:
        for t in doc["termes"]:
            cumul.setdefault(t["terme"], []).append(t["score"])
    tries = sorted(cumul.items(), key=lambda kv: (-len(kv[1]), -sum(kv[1])))
    return [{"terme": t, "documents_ou_il_est_cle": len(v), "score_total": round(sum(v), 6)} for t, v in tries[:nombre]]


def construire_rapport(documents: list[dict[str, Any]], args: argparse.Namespace, base: Path) -> dict[str, Any]:
    noms = [d["nom"] for d in documents]
    rapport = {
        "denominateur": len(documents),
        "examines": noms[:EXAMINES_MAX],
        "examines_tronques": len(noms) > EXAMINES_MAX,
        "moteur": MOTEUR_STDLIB if args.mode == "corpus" else args.moteur_effectif,
        "methode": METHODE_TFIDF if args.mode == "corpus" else
        (METHODE_RAKE if args.moteur_effectif == MOTEUR_STDLIB else MOTEUR_TIERS),
        "sens_du_score": "plus bas = plus pertinent" if args.moteur_effectif == MOTEUR_TIERS
        and args.mode != "corpus" else "plus haut = plus pertinent",
        "mode": args.mode,
        "racine": str(base),
        "documents": documents,
        "contrat": extraire_contrat(__doc__ or ""),
    }
    if args.mode == "corpus":
        rapport["termes_les_plus_repandus"] = termes_du_corpus(documents, args.nombre)
    return rapport


def afficher_humain(rapport: dict[str, Any]) -> None:
    sys.stdout.write(f"{rapport['denominateur']} document(s), méthode {rapport['methode']} "
                     f"({rapport['sens_du_score']})\n")
    for doc in rapport["documents"]:
        sys.stdout.write(f"\n{doc['nom']} — {doc['mots_pleins']} mots pleins, mots vides "
                         f"{doc['langue_mots_vides']}\n")
        for t in doc["termes"]:
            sys.stdout.write(f"  {t['score']:>10.4f}  x{t['frequence']:<4} l.{t['ligne'] or '?':<6} {t['terme']}\n")
        if "comparaison" in doc:
            c = doc["comparaison"]
            sys.stdout.write(f"  yake : {len(c['communs'])} terme(s) en commun, Jaccard {c['jaccard']}\n")


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Extrait les termes-clés d'un texte (RAKE + fréquence, n-grammes de 1 à 3, mots "
                    "vides FR/EN) ou d'un dossier de fichiers (TF-IDF entre fichiers), avec score, "
                    "fréquence et première occurrence.",
        epilog="Exemple : python extraire_mots_cles.py rapport.md --nombre 10\n"
               "          python extraire_mots_cles.py docs/ --json\n"
               "Codes : 0 termes extraits ; 2 usage ; 3 rien à examiner (aucun mot plein).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemin", nargs="?", help="fichier texte, dossier (corpus TF-IDF) ou '-'")
    parseur.add_argument("--texte", help="texte donné en ligne (au lieu d'un chemin)")
    parseur.add_argument("--nombre", type=int, default=TERMES_DEFAUT, help="termes rendus par document")
    parseur.add_argument("--langue", choices=("auto", "fr", "en"), default="auto",
                         help="mots vides à utiliser (auto : selon le texte, ou les deux)")
    parseur.add_argument("--moteur", choices=("auto", MOTEUR_STDLIB, MOTEUR_TIERS), default="auto",
                         help="document seul : auto = yake s'il est installé, sinon RAKE stdlib")
    parseur.add_argument("--comparer", action="store_true", help="recouvrement RAKE / yake (yake requis)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale par fichier")
    parseur.add_argument("--racine", type=Path,
                         help=f"base des chemins relatifs (défaut : dossier courant ; outil : {RACINE.name})")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    base = (args.racine if args.racine is not None else Path.cwd()).resolve()
    args.mode = "document"
    if args.nombre < 1:
        print("erreur : --nombre doit valoir au moins 1", file=sys.stderr)
        return CODE_USAGE
    try:
        args.moteur_effectif = choisir_moteur(args.moteur)
        entrees = collecter_entrees(args, base)
    except ErreurEntree as erreur:
        print(f"erreur : {erreur}", file=sys.stderr)
        return erreur.code
    except OSError as erreur:
        print(f"erreur de lecture : {erreur}", file=sys.stderr)
        return CODE_USAGE
    rapport = construire_rapport(analyser(entrees, args), args, base)
    if args.json:
        sys.stdout.write(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n")
    else:
        afficher_humain(rapport)
    if not rapport["denominateur"]:
        print(f"dénominateur nul : rien à examiner ({len(entrees)} entrée(s) lue(s), aucun mot plein)",
              file=sys.stderr)
        return CODE_VIDE
    return CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
