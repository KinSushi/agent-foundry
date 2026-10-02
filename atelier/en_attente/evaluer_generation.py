"""Note des générations contre leurs références avec les métriques standard, recalculées sans dépendance.

Mesuré le 2 octobre 2026 sur 1509 paires (lignes de docs/ bruitées : mots remplacés,
supprimés, insérés, majuscules) : écart maximal de 1,4e-14 avec sacrebleu 2.6.0,
rouge-score 0.1.2 et jiwer 4.0.0 sur les 7 métriques (--comparer), et la moyenne
des BLEU par phrase (61,09) n'est pas le BLEU corpus (64,98) qu'on publie.

QUESTION
    Cette génération est-elle proche des références, selon quelles
    métriques standard ?
MESURE
    Sur des paires (hypothèse, référence) alignées par ligne (deux
    fichiers) ou lues dans un JSONL : BLEU corpus façon sacrebleu
    (tokenisation 13a, n-grammes 1 à 4, lissage exp, pénalité de
    brièveté, statistiques sommées sur le corpus) ; chrF façon sacrebleu
    (n-grammes de caractères 1 à 6 sans blancs, bêta 2, statistiques
    sommées) ; ROUGE-1, ROUGE-2 et ROUGE-L façon rouge-score (F-mesure,
    précision, rappel par segment puis moyenne) ; WER et CER façon jiwer
    (distance de Levenshtein sommée sur le corpus divisée par le nombre de
    mots ou de caractères des références). Option --comparer : recalcul
    par sacrebleu, rouge-score et jiwer s'ils sont installés, écart
    maximal MESURÉ et rendu.
HYPOTHÈSES
    Une hypothèse et une référence par segment ; les textes sont
    détokenisés (sacrebleu applique sa propre tokenisation) ; une seule
    référence par segment.
LIMITES
    Métriques de surface : une paraphrase correcte est pénalisée, une
    phrase fausse qui reprend les mots est récompensée. BLEU corpus n'est
    pas la moyenne des BLEU par phrase. En tokenisation rouge « ascii »
    (celle de rouge-score), les lettres accentuées sont des séparateurs ;
    le défaut « unicode » les garde, ce qui donne d'autres chiffres que
    rouge-score sur du texte non anglais. ROUGE-L, WER et CER sont en
    temps quadratique par segment. Pas de références multiples.
CONTRE-EXEMPLES
    Constaté : l'hypothèse « La réunion est maintenue. » contre la
    référence « La réunion est annulée. » (sens contraire) obtient BLEU
    42,7, chrF 59,3, ROUGE-L 0,750 ; la paraphrase exacte « La réunion est
    annulée. » contre « La réunion n'aura pas lieu. » n'obtient que BLEU
    19,4, chrF 38,1, ROUGE-L 0,400 (--par-segment).
INVOCATION
    {outil} {fichier} {fichier} --json
DOMAINE
    Évaluation automatique de traduction, de résumé, de transcription ou
    de génération contre des références écrites ; jamais seule pour juger
    la justesse factuelle.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
METRIQUE_BLEU = "BLEU"
METRIQUE_ROUGE = "ROUGE"
METRIQUE_ROUGE_1 = "ROUGE-1"
METRIQUE_ROUGE_2 = "ROUGE-2"
METRIQUE_ROUGE_L = "ROUGE-L"
FORMAT_JSONL = "JSONL"
MOT_MESURE = "MESURÉ"
MOTEUR_STDLIB = "stdlib"
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
ORDRE_BLEU = 4
ORDRE_CHRF = 6
BETA_CHRF = 2
LOG_ZERO = -9999999999
TOLERANCE = 1e-9
MAX_EXAMINES = 50
TAILLE_MAX = 200_000_000
CHAMPS_HYP = ("hypothese", "hypothesis", "prediction", "hyp", "pred", "sortie", "output")
CHAMPS_REF = ("reference", "ref", "target", "cible", "attendu", "expected")
METRIQUES = ("bleu", "chrf", "rouge", "wer", "cer")
BIBLIOTHEQUES_OPTIONNELLES = (("sacrebleu", "sacrebleu"), ("rouge_score", "rouge-score"), ("jiwer", "jiwer"))
RE_13A = (
    (re.compile(r"([\{-\~\[-\` -\&\(-\+\:-\@\/])"), r" \1 "),
    (re.compile(r"([^0-9])([\.,])"), r"\1 \2 "),
    (re.compile(r"([\.,])([^0-9])"), r" \1 \2"),
    (re.compile(r"([0-9])(-)"), r"\1 \2 "),
)
RE_ROUGE_ASCII = re.compile(r"[^a-z0-9]+")
RE_ROUGE_VALIDE = re.compile(r"^[a-z0-9]+$")
RE_ROUGE_UNICODE = re.compile(r"[^\W_]+")
RE_BLANCS_MULTIPLES = re.compile(r"\s\s+")


class ErreurEntree(Exception):
    """Entrée invalide : message pour stderr et code de sortie."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Segment:
    """Une paire alignée et son origine lisible."""

    nom: str
    hypothese: str
    reference: str


# --------------------------------------------------------------------------
# Contrat et bibliothèques optionnelles


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
    """UNE ligne sur stderr pour les bibliothèques de référence absentes."""
    absentes = [nom for module, nom in BIBLIOTHEQUES_OPTIONNELLES if not _bibliotheque_presente(module)]
    if absentes:
        print("evaluer_generation : absent(s) : " + ", ".join(absentes) + " — métriques calculées "
              "par le moteur stdlib, pas de comparaison avec ces implémentations.", file=sys.stderr)


def _importer(module: str) -> ModuleType | None:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return importlib.import_module(module)
    except ImportError:
        return None


def _version(distribution: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return "?"
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "?"


# --------------------------------------------------------------------------
# Lecture des entrées


def lire_texte(chemin: Path) -> str:
    """Fichier utf-8 borné ; ErreurEntree s'il manque, est un dossier, binaire ou mal encodé."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if not chemin.is_file():
        raise ErreurEntree(f"pas un fichier : {chemin}")
    if chemin.stat().st_size > TAILLE_MAX:
        raise ErreurEntree(f"{chemin} : plus de {TAILLE_MAX} octets")
    brut = chemin.read_bytes()
    if b"\x00" in brut:
        raise ErreurEntree(f"{chemin} : fichier binaire (octet nul)")
    try:
        return brut.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} : pas de l'utf-8 valide (octet {exc.start})") from exc


def _lignes(texte: str) -> list[str]:
    """Une ligne par segment (fins de ligne \\n, \\r\\n ou \\r), sans segment fantôme final."""
    lignes = texte.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lignes and lignes[-1] == "":
        lignes.pop()
    return lignes


def segments_alignes(texte_hyp: str, texte_ref: str, nom_hyp: str) -> list[Segment]:
    """Paires depuis deux textes alignés ligne à ligne."""
    hypotheses, references = _lignes(texte_hyp), _lignes(texte_ref)
    if len(hypotheses) != len(references):
        raise ErreurEntree(f"désalignement : {len(hypotheses)} hypothèse(s) pour "
                           f"{len(references)} référence(s)")
    return [Segment(f"{nom_hyp}:{i}", h, r) for i, (h, r) in enumerate(zip(hypotheses, references), start=1)]


def _choisir_champ(objet: dict[str, Any], impose: str | None, candidats: tuple[str, ...]) -> str:
    if impose:
        return impose
    for nom in candidats:
        if nom in objet:
            return nom
    raise ErreurEntree(f"aucun champ parmi {', '.join(candidats)} : préciser --champ-hyp/--champ-ref")


def segments_jsonl(chemin: Path, nom: str, champ_hyp: str | None, champ_ref: str | None) -> list[Segment]:
    """Paires depuis un JSONL ; toute ligne invalide est une erreur d'entrée."""
    segments: list[Segment] = []
    for numero, ligne in enumerate(lire_texte(chemin).splitlines(), start=1):
        if not ligne.strip():
            continue
        try:
            objet = json.loads(ligne)
        except json.JSONDecodeError as exc:
            raise ErreurEntree(f"{nom}:{numero} : JSON invalide ({exc.msg})") from exc
        if not isinstance(objet, dict):
            raise ErreurEntree(f"{nom}:{numero} : objet JSON attendu")
        cle_h = _choisir_champ(objet, champ_hyp, CHAMPS_HYP)
        cle_r = _choisir_champ(objet, champ_ref, CHAMPS_REF)
        hyp, ref = objet.get(cle_h), objet.get(cle_r)
        if not isinstance(hyp, str) or not isinstance(ref, str):
            raise ErreurEntree(f"{nom}:{numero} : « {cle_h} » et « {cle_r} » doivent être des chaînes "
                               "(une seule référence par segment)")
        segments.append(Segment(f"{nom}:{numero}", hyp, ref))
    return segments


# --------------------------------------------------------------------------
# BLEU (compatible sacrebleu)


def tokeniser_13a(ligne: str) -> str:
    """Tokenisation 13a de sacrebleu (mteval-v13a)."""
    ligne = ligne.replace("<skipped>", "").replace("-\n", "").replace("\n", " ")
    if "&" in ligne:
        ligne = ligne.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    ligne = f" {ligne} "
    for motif, remplacement in RE_13A:
        ligne = motif.sub(remplacement, ligne)
    return " ".join(ligne.split())


def _ngrammes_mots(jetons: list[str], ordre_max: int) -> Counter[tuple[str, ...]]:
    return Counter(tuple(jetons[i:i + n]) for n in range(1, ordre_max + 1)
                   for i in range(len(jetons) - n + 1))


def stats_bleu(hyp: str, ref: str, minuscules: bool) -> list[int]:
    """[long_hyp, long_ref, justes_1..4, totaux_1..4] d'un segment."""
    if minuscules:
        hyp, ref = hyp.lower(), ref.lower()
    jetons_h = tokeniser_13a(hyp.rstrip()).split()
    jetons_r = tokeniser_13a(ref.rstrip()).split()
    ng_h, ng_r = _ngrammes_mots(jetons_h, ORDRE_BLEU), _ngrammes_mots(jetons_r, ORDRE_BLEU)
    justes, totaux = [0] * ORDRE_BLEU, [0] * ORDRE_BLEU
    for ngramme, compte in ng_h.items():
        totaux[len(ngramme) - 1] += compte
        if ngramme in ng_r:
            justes[len(ngramme) - 1] += min(compte, ng_r[ngramme])
    return [len(jetons_h), len(jetons_r), *justes, *totaux]


def score_bleu(stats: list[int], ordre_effectif: bool = False) -> dict[str, Any]:
    """BLEU à partir des statistiques sommées (lissage exp, comme sacrebleu)."""
    long_h, long_r = stats[0], stats[1]
    justes, totaux = stats[2:2 + ORDRE_BLEU], stats[2 + ORDRE_BLEU:]
    bp = 1.0
    if long_h < long_r:
        bp = math.exp(1 - long_r / long_h) if long_h > 0 else 0.0
    precisions = [0.0] * ORDRE_BLEU
    base = {"bp": bp, "longueur_hyp": long_h, "longueur_ref": long_r,
            "ratio": long_h / long_r if long_r else 0.0}
    if not any(justes):
        return {"score": 0.0, "precisions": precisions, **base}
    lissage, ordre = 1.0, ORDRE_BLEU
    for n in range(1, ORDRE_BLEU + 1):
        if totaux[n - 1] == 0:
            break
        if ordre_effectif:
            ordre = n
        if justes[n - 1] == 0:
            lissage *= 2
            precisions[n - 1] = 100.0 / (lissage * totaux[n - 1])
        else:
            precisions[n - 1] = 100.0 * justes[n - 1] / totaux[n - 1]
    somme = sum(math.log(p) if p != 0.0 else LOG_ZERO for p in precisions[:ordre])
    return {"score": bp * math.exp(somme / ordre), "precisions": precisions, **base}


# --------------------------------------------------------------------------
# chrF (compatible sacrebleu)


def _ngrammes_caracteres(texte: str) -> list[Counter[str]]:
    texte = "".join(texte.split())
    return [Counter(texte[i:i + n] for i in range(len(texte) - n + 1)) for n in range(1, ORDRE_CHRF + 1)]


def stats_chrf(hyp: str, ref: str, minuscules: bool) -> list[int]:
    """[n_hyp, n_ref, n_communs] pour chaque ordre 1..6."""
    if minuscules:
        hyp, ref = hyp.lower(), ref.lower()
    stats: list[int] = []
    for ng_h, ng_r in zip(_ngrammes_caracteres(hyp), _ngrammes_caracteres(ref)):
        communs = sum(min(c, ng_r[g]) for g, c in ng_h.items() if g in ng_r)
        stats.extend([sum(ng_h.values()) if ng_r else 0, sum(ng_r.values()), communs])
    return stats


def score_chrf(stats: list[int]) -> float:
    """F-bêta moyen sur les ordres effectifs (chrF2 de sacrebleu, sans lissage epsilon)."""
    facteur = BETA_CHRF ** 2
    precision_moy = rappel_moy = 0.0
    effectifs = 0
    for i in range(ORDRE_CHRF):
        n_hyp, n_ref, n_communs = stats[3 * i:3 * i + 3]
        if n_hyp > 0 and n_ref > 0:
            precision_moy += n_communs / n_hyp
            rappel_moy += n_communs / n_ref
            effectifs += 1
    if effectifs == 0:
        return 0.0
    precision_moy, rappel_moy = precision_moy / effectifs, rappel_moy / effectifs
    if precision_moy + rappel_moy == 0:
        return 0.0
    return 100 * (1 + facteur) * precision_moy * rappel_moy / (facteur * precision_moy + rappel_moy)


# --------------------------------------------------------------------------
# ROUGE (compatible rouge-score)


def tokeniser_rouge(texte: str, mode: str) -> list[str]:
    """Tokenisation rouge-score (ascii) ou variante qui garde les lettres accentuées (unicode)."""
    texte = texte.lower()
    if mode == "unicode":
        return RE_ROUGE_UNICODE.findall(texte)
    return [j for j in re.split(r"\s+", RE_ROUGE_ASCII.sub(" ", texte)) if RE_ROUGE_VALIDE.match(j)]


def _f_mesure(precision: float, rappel: float) -> float:
    return 2 * precision * rappel / (precision + rappel) if precision + rappel > 0 else 0.0


def rouge_n(cible: list[str], prediction: list[str], n: int) -> tuple[float, float, float]:
    """(précision, rappel, F) des n-grammes communs."""
    ng_c = Counter(tuple(cible[i:i + n]) for i in range(len(cible) - n + 1))
    ng_p = Counter(tuple(prediction[i:i + n]) for i in range(len(prediction) - n + 1))
    communs = sum(min(c, ng_p[g]) for g, c in ng_c.items())
    precision = communs / max(sum(ng_p.values()), 1)
    rappel = communs / max(sum(ng_c.values()), 1)
    return precision, rappel, _f_mesure(precision, rappel)


def longueur_pslc(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Longueur de la plus longue sous-séquence commune (programmation dynamique, deux lignes)."""
    if len(b) > len(a):
        a, b = b, a
    precedente = [0] * (len(b) + 1)
    for x in a:
        courante = [0]
        for j, y in enumerate(b, start=1):
            courante.append(precedente[j - 1] + 1 if x == y else max(precedente[j], courante[j - 1]))
        precedente = courante
    return precedente[-1]


def rouge_l(cible: list[str], prediction: list[str]) -> tuple[float, float, float]:
    """(précision, rappel, F) fondés sur la plus longue sous-séquence commune."""
    if not cible or not prediction:
        return 0.0, 0.0, 0.0
    lcs = longueur_pslc(cible, prediction)
    precision, rappel = lcs / len(prediction), lcs / len(cible)
    return precision, rappel, _f_mesure(precision, rappel)


# --------------------------------------------------------------------------
# WER / CER (compatibles jiwer)


def distance_edition(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Distance de Levenshtein (préfixe et suffixe communs retirés d'abord)."""
    debut = 0
    while debut < min(len(a), len(b)) and a[debut] == b[debut]:
        debut += 1
    fin_a, fin_b = len(a), len(b)
    while fin_a > debut and fin_b > debut and a[fin_a - 1] == b[fin_b - 1]:
        fin_a, fin_b = fin_a - 1, fin_b - 1
    a, b = a[debut:fin_a], b[debut:fin_b]
    if not a or not b:
        return max(len(a), len(b))
    precedente = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        courante = [i]
        for j, y in enumerate(b, start=1):
            courante.append(min(precedente[j] + 1, courante[j - 1] + 1, precedente[j - 1] + (x != y)))
        precedente = courante
    return precedente[-1]


def mots_jiwer(texte: str) -> list[str]:
    """Transformation wer_default de jiwer : blancs multiples réduits, bords retirés, coupe sur l'espace."""
    return [m for m in RE_BLANCS_MULTIPLES.sub(" ", texte).strip().split(" ") if m]


def caracteres_jiwer(texte: str) -> list[str]:
    """Transformation cer_default de jiwer : bords retirés, liste des caractères."""
    return list(texte.strip())


# --------------------------------------------------------------------------
# Calcul global


def _taux(editions: int, reference: int) -> float | None:
    return editions / reference if reference else None


def evaluer(segments: list[Segment], args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Métriques corpus et détail par segment."""
    stats_b = [0] * (2 + 2 * ORDRE_BLEU)
    stats_c = [0] * (3 * ORDRE_CHRF)
    rouges = {"rouge1": [0.0, 0.0, 0.0], "rouge2": [0.0, 0.0, 0.0], "rougeL": [0.0, 0.0, 0.0]}
    editions = {"wer": [0, 0], "cer": [0, 0]}
    detail = []
    for segment in segments:
        ligne = _evaluer_segment(segment, args)
        stats_b = [x + y for x, y in zip(stats_b, ligne.pop("_stats_bleu"))]
        stats_c = [x + y for x, y in zip(stats_c, ligne.pop("_stats_chrf"))]
        for cle in rouges:
            rouges[cle] = [x + y for x, y in zip(rouges[cle], ligne[cle])]
        for cle in editions:
            editions[cle] = [x + y for x, y in zip(editions[cle], ligne.pop(f"_{cle}"))]
        detail.append(ligne)
    total = len(segments)
    metriques = {
        "bleu": score_bleu(stats_b),
        "chrf": {"score": score_chrf(stats_c)},
        **{cle: {"precision": v[0] / total, "rappel": v[1] / total, "f": v[2] / total}
           for cle, v in rouges.items()},
        "wer": {"taux": _taux(*editions["wer"]), "editions": editions["wer"][0], "mots_ref": editions["wer"][1]},
        "cer": {"taux": _taux(*editions["cer"]), "editions": editions["cer"][0],
                "caracteres_ref": editions["cer"][1]},
    }
    return metriques, detail


def _evaluer_segment(segment: Segment, args: argparse.Namespace) -> dict[str, Any]:
    """Statistiques et scores d'un segment."""
    hyp, ref = segment.hypothese, segment.reference
    sb, sc = stats_bleu(hyp, ref, args.minuscules), stats_chrf(hyp, ref, args.minuscules)
    tok_h, tok_r = tokeniser_rouge(hyp, args.rouge_tokenisation), tokeniser_rouge(ref, args.rouge_tokenisation)
    cas_h, cas_r = (hyp.lower(), ref.lower()) if args.minuscules else (hyp, ref)
    mots_h, mots_r = mots_jiwer(cas_h), mots_jiwer(cas_r)
    car_h, car_r = caracteres_jiwer(cas_h), caracteres_jiwer(cas_r)
    ed_mots, ed_car = distance_edition(mots_r, mots_h), distance_edition(car_r, car_h)
    return {
        "segment": segment.nom,
        "bleu_phrase": score_bleu(sb, ordre_effectif=True)["score"],
        "chrf": score_chrf(sc),
        "rouge1": list(rouge_n(tok_r, tok_h, 1)), "rouge2": list(rouge_n(tok_r, tok_h, 2)),
        "rougeL": list(rouge_l(tok_r, tok_h)),
        "wer": _taux(ed_mots, len(mots_r)), "cer": _taux(ed_car, len(car_r)),
        "_stats_bleu": sb, "_stats_chrf": sc, "_wer": [ed_mots, len(mots_r)], "_cer": [ed_car, len(car_r)],
    }


# --------------------------------------------------------------------------
# Comparaison avec les implémentations de référence


def _comparer_sacrebleu(segments: list[Segment], metriques: dict[str, Any],
                        args: argparse.Namespace) -> dict[str, Any]:
    module = _importer("sacrebleu")
    if module is None:
        return {"absent": True}
    hyps, refs = [s.hypothese for s in segments], [s.reference for s in segments]
    with contextlib.redirect_stdout(sys.stderr):
        bleu = module.metrics.BLEU(lowercase=args.minuscules).corpus_score(hyps, [refs]).score
        chrf = module.metrics.CHRF(lowercase=args.minuscules).corpus_score(hyps, [refs]).score
    return {"version": _version("sacrebleu"), "bleu": bleu, "chrf": chrf,
            "ecart_bleu": abs(bleu - metriques["bleu"]["score"]),
            "ecart_chrf": abs(chrf - metriques["chrf"]["score"])}


class _TokeniseurRouge:
    """Adaptateur passé à rouge-score pour comparer à tokenisation égale (mode unicode)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def tokenize(self, texte: str) -> list[str]:
        return tokeniser_rouge(texte, self.mode)


def _comparer_rouge(segments: list[Segment], metriques: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    module = _importer("rouge_score.rouge_scorer")
    if module is None:
        return {"absent": True}
    tokeniseur = None if args.rouge_tokenisation == "ascii" else _TokeniseurRouge(args.rouge_tokenisation)
    with contextlib.redirect_stdout(sys.stderr):
        evaluateur = module.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=False, tokenizer=tokeniseur)
        scores = [evaluateur.score(s.reference, s.hypothese) for s in segments]
    resultat: dict[str, Any] = {"version": _version("rouge-score")}
    for cle in ("rouge1", "rouge2", "rougeL"):
        moyenne = sum(sc[cle].fmeasure for sc in scores) / len(scores)
        resultat[cle] = moyenne
        resultat[f"ecart_{cle}"] = abs(moyenne - metriques[cle]["f"])
    return resultat


def _comparer_jiwer(segments: list[Segment], metriques: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    module = _importer("jiwer")
    if module is None:
        return {"absent": True}
    hyps = [s.hypothese.lower() if args.minuscules else s.hypothese for s in segments]
    refs = [s.reference.lower() if args.minuscules else s.reference for s in segments]
    resultat: dict[str, Any] = {"version": _version("jiwer")}
    for cle, fonction in (("wer", module.wer), ("cer", module.cer)):
        try:
            with contextlib.redirect_stdout(sys.stderr):
                valeur = float(fonction(refs, hyps))
        except ValueError as exc:
            resultat[cle] = f"non comparable : {exc}"
            continue
        resultat[cle] = valeur
        if metriques[cle]["taux"] is not None:
            resultat[f"ecart_{cle}"] = abs(valeur - metriques[cle]["taux"])
    return resultat


def comparer(segments: list[Segment], metriques: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Recalcul par sacrebleu, rouge-score et jiwer ; écart maximal mesuré."""
    resultats = {"sacrebleu": _comparer_sacrebleu(segments, metriques, args),
                 "rouge-score": _comparer_rouge(segments, metriques, args),
                 "jiwer": _comparer_jiwer(segments, metriques, args)}
    ecarts = [v for r in resultats.values() for k, v in r.items() if k.startswith("ecart_")]
    resultats["ecart_max"] = max(ecarts) if ecarts else None
    resultats["valeurs_comparees"] = len(ecarts)
    return resultats


# --------------------------------------------------------------------------
# Seuils, sortie


def verifier_seuils(metriques: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """Liste des seuils demandés qui ne sont pas tenus."""
    violes = []
    controles = (("--min-bleu", args.min_bleu, metriques["bleu"]["score"], True),
                 ("--min-chrf", args.min_chrf, metriques["chrf"]["score"], True),
                 ("--min-rouge-l", args.min_rouge_l, metriques["rougeL"]["f"], True),
                 ("--max-wer", args.max_wer, metriques["wer"]["taux"], False),
                 ("--max-cer", args.max_cer, metriques["cer"]["taux"], False))
    for option, seuil, valeur, minimum in controles:
        if seuil is None or valeur is None:
            continue
        if (minimum and valeur < seuil) or (not minimum and valeur > seuil):
            violes.append(f"{option} {seuil} : obtenu {valeur:.6g}")
    return violes


def signature(args: argparse.Namespace) -> str:
    """Signature façon sacrebleu des réglages BLEU, chrF et ROUGE."""
    casse = "lc" if args.minuscules else "mixed"
    return (f"nrefs:1|case:{casse}|eff:no|tok:13a|smooth:exp|chrF:c{ORDRE_CHRF}w0b{BETA_CHRF}"
            f"|rouge-tok:{args.rouge_tokenisation}|moteur:{MOTEUR_STDLIB}")


def presenter_humain(sortie: dict[str, Any]) -> None:
    """Affichage lisible."""
    m = sortie["metriques"]
    print(f"{sortie['denominateur']} segment(s) — {sortie['signature']}")
    print(f"BLEU   {m['bleu']['score']:.2f}  (BP {m['bleu']['bp']:.3f}, ratio {m['bleu']['ratio']:.3f}, "
          f"précisions {' / '.join(f'{p:.1f}' for p in m['bleu']['precisions'])})")
    print(f"chrF2  {m['chrf']['score']:.2f}")
    for cle, nom in (("rouge1", "ROUGE-1"), ("rouge2", "ROUGE-2"), ("rougeL", "ROUGE-L")):
        print(f"{nom:<8}F {m[cle]['f']:.4f}  P {m[cle]['precision']:.4f}  R {m[cle]['rappel']:.4f}")
    for cle in ("wer", "cer"):
        taux = m[cle]["taux"]
        print(f"{cle.upper():<7}{'indéfini (références vides)' if taux is None else f'{taux:.4f}'}")
    for viole in sortie["seuils_violes"]:
        print(f"SEUIL NON TENU : {viole}")
    if "comparaison" in sortie:
        print(f"comparaison : écart max {sortie['comparaison']['ecart_max']}")


def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    parser = argparse.ArgumentParser(
        description="Évalue des générations contre des références : BLEU et chrF (façon sacrebleu), "
                    "ROUGE-1/2/L (façon rouge-score), WER et CER (façon jiwer).",
        epilog="Exemple : python3 evaluer_generation.py sorties.txt references.txt --min-chrf 50 --json ; "
               "ou : python3 evaluer_generation.py paires.jsonl --champ-hyp prediction --champ-ref reference")
    parser.add_argument("fichier_hyp", type=Path, help="hypothèses (une par ligne) ou JSONL de paires")
    parser.add_argument("fichier_ref", type=Path, nargs="?", help="références alignées ligne à ligne")
    parser.add_argument("--champ-hyp", help="champ JSONL de l'hypothèse (défaut : détecté)")
    parser.add_argument("--champ-ref", help="champ JSONL de la référence (défaut : détecté)")
    parser.add_argument("--minuscules", action="store_true", help="BLEU, chrF, WER et CER insensibles à la casse")
    parser.add_argument("--rouge-tokenisation", choices=("unicode", "ascii"), default="unicode", metavar="MODE",
                        help="unicode (défaut, garde les accents) ou ascii (identique à rouge-score)")
    parser.add_argument("--par-segment", action="store_true", help="joindre le détail de chaque segment")
    parser.add_argument("--min-bleu", type=float, help="code 1 si BLEU < seuil")
    parser.add_argument("--min-chrf", type=float, help="code 1 si chrF < seuil")
    parser.add_argument("--min-rouge-l", type=float, help="code 1 si ROUGE-L F < seuil")
    parser.add_argument("--max-wer", type=float, help="code 1 si WER > seuil")
    parser.add_argument("--max-cer", type=float, help="code 1 si CER > seuil")
    parser.add_argument("--comparer", action="store_true",
                        help="recalculer avec sacrebleu, rouge-score et jiwer et rendre l'écart maximal")
    parser.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=Path,
                        help=f"base des chemins relatifs (défaut : répertoire courant ; outil dans {RACINE})")
    return parser


def charger_segments(args: argparse.Namespace) -> list[Segment]:
    """Paires à évaluer selon la forme des entrées."""
    racine = args.racine if args.racine is not None else Path.cwd()
    hyp = args.fichier_hyp if args.fichier_hyp.is_absolute() else racine / args.fichier_hyp
    nom = args.fichier_hyp.name
    if args.fichier_ref is None:
        return segments_jsonl(hyp, nom, args.champ_hyp, args.champ_ref)
    ref = args.fichier_ref if args.fichier_ref.is_absolute() else racine / args.fichier_ref
    return segments_alignes(lire_texte(hyp), lire_texte(ref), nom)


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Charge, évalue, compare ; renvoie la sortie et le code."""
    segments = charger_segments(args)
    sortie: dict[str, Any] = {
        "outil": "evaluer_generation", "moteur": MOTEUR_STDLIB, "signature": signature(args),
        "denominateur": len(segments), "examines": [s.nom for s in segments[:MAX_EXAMINES]],
        "examines_tronques": len(segments) > MAX_EXAMINES, "contrat": extraire_contrat(__doc__ or ""),
    }
    if not segments:
        return sortie, 3
    metriques, detail = evaluer(segments, args)
    sortie["metriques"] = metriques
    sortie["seuils_violes"] = verifier_seuils(metriques, args)
    if args.par_segment:
        sortie["segments"] = detail
    code = 1 if sortie["seuils_violes"] else 0
    if args.comparer:
        sortie["comparaison"] = comparer(segments, metriques, args)
        ecart = sortie["comparaison"]["ecart_max"]
        if ecart is not None and ecart > TOLERANCE:
            code = 1
    return sortie, code


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_analyseur().parse_args(argv)
    _signaler_absences()
    try:
        sortie, code = executer(args)
    except ErreurEntree as exc:
        print(f"evaluer_generation : {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"evaluer_generation : erreur d'entrée/sortie : {exc}", file=sys.stderr)
        return 2
    if code == 3:
        print("evaluer_generation : dénominateur nul — rien à examiner (aucun segment)", file=sys.stderr)
    elif code == 1 and sortie.get("comparaison", {}).get("ecart_max") and not sortie["seuils_violes"]:
        print(f"evaluer_generation : écart avec les implémentations de référence "
              f"{sortie['comparaison']['ecart_max']:.3g} > {TOLERANCE}", file=sys.stderr)
    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    elif code != 3:
        presenter_humain(sortie)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
