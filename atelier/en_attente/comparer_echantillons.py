"""Compare deux échantillons numériques : test de Welch, Mann-Whitney, bootstrap et tailles d'effet, en stdlib.

Un agent qui « regarde les moyennes » conclut sans mesurer le bruit, et l'interpréteur de
référence n'a pas scipy (mesuré : `import scipy` y lève ModuleNotFoundError). Tout est donc
recalculé en stdlib ; mesuré contre scipy 1.18.1 sur 300 paires tirées au hasard (graine 42,
n de 2 à 200) : écart maximal 1,8e-13 sur la p-valeur de Welch, 2,2e-16 sur celle de
Mann-Whitney, 1,6e-14 sur les bornes de l'intervalle de Welch.

QUESTION
    La différence entre ces deux échantillons est-elle statistiquement significative,
    et de quelle taille ?
MESURE
    Statistiques descriptives (module statistics) ; test t de Welch (variances
    inégales), p-valeur bilatérale par la fonction de répartition de Student
    calculée via la fonction bêta incomplète régularisée (fraction continue de
    Lentz) ; test U de Mann-Whitney, approximation normale avec correction de
    continuité et correction des ex aequo ; intervalle de confiance bootstrap
    percentile de la différence des moyennes (graine fixée, donc reproductible) ;
    d de Cohen, g de Hedges et delta de Cliff. Si scipy est présent, les deux
    p-valeurs sont recalculées par scipy et l'écart est publié.
HYPOTHÈSES
    Observations indépendantes, à l'intérieur et entre les échantillons ; pour
    Welch, moyennes approximativement normales (n assez grand ou données peu
    asymétriques) ; les nombres lus sont bien les mesures (une colonne par fichier
    ou une liste en ligne).
LIMITES
    Pas de test apparié (mesures avant/après sur les mêmes sujets) ni de
    correction pour comparaisons multiples ; Mann-Whitney en approximation normale
    seulement (pas de loi exacte pour les petits n) ; bootstrap omis au-delà de
    200 000 observations ; seuils d'interprétation des effets conventionnels
    (Cohen 1988 : 0,2/0,5/0,8 ; Romano et al. 2006 : 0,147/0,33/0,474).
CONTRE-EXEMPLES
    Deux échantillons de 3 valeurs parfaitement séparés ([1.1, 2.2, 3.3] contre
    [4.4, 5.5, 6.6]) : Mann-Whitney rend p = 0,0809 par approximation normale, alors
    que la loi exacte (scipy, method="exact") donne p = 0,1 ; avec --test
    mann-whitney --alpha 0.09, l'outil conclut à une différence significative
    (code 1) que la loi exacte ne soutient pas. Constaté dans cette session.
INVOCATION
    {outil} --a "12.1 11.8 12.6 13.0 12.4" --b "13.2 13.9 12.8 14.1 13.5" --json
DOMAINE
    Deux échantillons indépendants de mesures numériques (temps de réponse,
    scores, durées de build...), de 2 à quelques centaines de milliers de valeurs.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import scipy
    import scipy.stats as scipy_stats
except ImportError:
    scipy = None
    scipy_stats = None

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULES = (
    INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
    INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE,
)

CODE_OK = 0
CODE_SIGNIFICATIF = 1
CODE_USAGE = 2
CODE_RIEN = 3

OCTETS_MAX_DEFAUT = 50 * 1024 * 1024
ITERATIONS_BOOTSTRAP = 2000
BUDGET_BOOTSTRAP = 20_000_000
OBSERVATIONS_MAX_BOOTSTRAP = 200_000
SEUILS_COHEN = ((0.2, "négligeable"), (0.5, "petit"), (0.8, "moyen"))
SEUILS_CLIFF = ((0.147, "négligeable"), (0.33, "petit"), (0.474, "moyen"))
EPSILON_FRACTION = 1e-15
ITERATIONS_FRACTION = 10_000
MINUSCULE = 1e-300


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Echantillon:
    """Un échantillon lu : valeurs retenues et jetons écartés."""

    nom: str
    source: str
    valeurs: list[float] = field(default_factory=list)
    ignores: int = 0
    exemples_ignores: list[str] = field(default_factory=list)
    virgule_entre_chiffres: bool = False


def separateurs(virgule_decimale: bool) -> re.Pattern[str]:
    """Motif des séparateurs de nombres (la virgule en est un sauf en mode décimal)."""
    return re.compile(r"[\s;]+" if virgule_decimale else r"[\s;,]+")


def lire_nombres(texte: str, nom: str, source: str, virgule_decimale: bool) -> Echantillon:
    """Extrait les nombres finis d'un texte ; les commentaires # et les jetons non numériques sont écartés."""
    echantillon = Echantillon(nom, source)
    echantillon.virgule_entre_chiffres = not virgule_decimale and re.search(r"\d,\d", texte) is not None
    motif = separateurs(virgule_decimale)
    for ligne in texte.splitlines():
        for jeton in motif.split(ligne.split("#", 1)[0]):
            if jeton:
                ajouter_jeton(echantillon, jeton, virgule_decimale)
    return echantillon


def ajouter_jeton(echantillon: Echantillon, jeton: str, virgule_decimale: bool) -> None:
    """Ajoute un jeton s'il représente un nombre fini, sinon le compte comme écarté."""
    try:
        valeur = float(jeton.replace(",", ".") if virgule_decimale else jeton)
    except ValueError:
        valeur = math.nan
    if math.isfinite(valeur):
        echantillon.valeurs.append(valeur)
        return
    echantillon.ignores += 1
    if len(echantillon.exemples_ignores) < 5:
        echantillon.exemples_ignores.append(jeton[:40])


def lire_fichier_borne(chemin: Path, octets_max: int) -> str:
    """Lit un fichier texte UTF-8, borné en taille ; refuse dossier, binaire, absent."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} est un dossier : un fichier de nombres est attendu")
    taille = chemin.stat().st_size
    if taille > octets_max:
        raise ErreurEntree(f"{chemin} pèse {taille} octets, au-delà de --max-octets {octets_max}")
    with chemin.open("rb") as flux:
        donnees = flux.read(octets_max + 1)
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas une liste de nombres")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en UTF-8 (octet {exc.start}) : {exc.reason}") from exc


def resoudre(chemin: str, racine: Path | None) -> Path:
    """Résout un chemin relatif contre --racine, ou le répertoire courant."""
    brut = Path(chemin)
    return brut if brut.is_absolute() or racine is None else racine / brut


def charger_echantillons(args: argparse.Namespace) -> tuple[Echantillon, Echantillon]:
    """Construit les échantillons a et b depuis --a/--b ou deux fichiers."""
    fichiers = list(args.fichiers)
    sources: list[tuple[str, str | None, str | None]] = []
    for nom, en_ligne in (("a", args.a), ("b", args.b)):
        if en_ligne is not None:
            sources.append((nom, en_ligne, None))
        elif fichiers:
            sources.append((nom, None, fichiers.pop(0)))
    if fichiers:
        raise ErreurEntree(f"trop d'entrées : {len(args.fichiers)} fichier(s) en plus de --a/--b ; il faut exactement deux échantillons")
    echantillons = []
    for nom, en_ligne, fichier in sources:
        if fichier is not None:
            chemin = resoudre(fichier, args.racine)
            texte = lire_fichier_borne(chemin, args.max_octets)
            echantillons.append(lire_nombres(texte, nom, str(chemin), args.virgule_decimale))
        else:
            echantillons.append(lire_nombres(en_ligne or "", nom, f"--{nom} (en ligne)", args.virgule_decimale))
    if len(echantillons) != 2:
        raise ErreurEntree("deux échantillons sont requis : --a et --b, ou deux fichiers, ou un fichier et --b")
    return echantillons[0], echantillons[1]


def decrire(valeurs: Sequence[float]) -> dict[str, Any]:
    """Statistiques descriptives d'un échantillon (n ≥ 2)."""
    quartiles = statistics.quantiles(valeurs, n=4, method="inclusive")
    return {
        "n": len(valeurs), "moyenne": statistics.fmean(valeurs),
        "ecart_type": statistics.stdev(valeurs), "variance": statistics.variance(valeurs),
        "mediane": statistics.median(valeurs), "q1": quartiles[0], "q3": quartiles[2],
        "min": min(valeurs), "max": max(valeurs),
    }


def fraction_continue_beta(a: float, b: float, x: float) -> float:
    """Fraction continue de la bêta incomplète (algorithme de Lentz modifié)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > MINUSCULE else MINUSCULE)
    h = d
    for m in range(1, ITERATIONS_FRACTION + 1):
        m2 = 2 * m
        for terme in (m * (b - m) * x / ((qam + m2) * (a + m2)),
                      -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + terme * d
            d = 1.0 / (d if abs(d) > MINUSCULE else MINUSCULE)
            c = 1.0 + terme / c
            c = c if abs(c) > MINUSCULE else MINUSCULE
            delta = d * c
            h *= delta
        if abs(delta - 1.0) < EPSILON_FRACTION:
            return h
    raise ArithmeticError(f"fraction continue non convergente (a={a}, b={b}, x={x})")


def beta_incomplete_reguliere(a: float, b: float, x: float, complement: float) -> float:
    """I_x(a, b) ; `complement` vaut 1 - x, calculé à part pour garder la précision."""
    if x <= 0.0:
        return 0.0
    if complement <= 0.0:
        return 1.0
    log_tete = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log(complement))
    tete = math.exp(log_tete)
    if x < (a + 1.0) / (a + b + 2.0):
        return tete * fraction_continue_beta(a, b, x) / a
    return 1.0 - tete * fraction_continue_beta(b, a, complement) / b


def p_student_bilaterale(t: float, ddl: float) -> float:
    """P(|T| ≥ |t|) pour une loi de Student à ddl degrés de liberté."""
    if math.isinf(t):
        return 0.0
    t2 = t * t
    return min(1.0, beta_incomplete_reguliere(ddl / 2.0, 0.5, ddl / (ddl + t2), t2 / (ddl + t2)))


def quantile_student(probabilite: float, ddl: float) -> float:
    """Quantile positif t tel que P(T ≤ t) = probabilite (≥ 0,5), par dichotomie."""
    cible_bilaterale = 2.0 * (1.0 - probabilite)
    bas, haut = 0.0, 1.0
    while p_student_bilaterale(haut, ddl) > cible_bilaterale and haut < 1e12:
        haut *= 2.0
    for _ in range(200):
        milieu = (bas + haut) / 2.0
        if p_student_bilaterale(milieu, ddl) > cible_bilaterale:
            bas = milieu
        else:
            haut = milieu
    return (bas + haut) / 2.0


def test_welch(a: dict[str, Any], b: dict[str, Any], niveau: float) -> dict[str, Any]:
    """Test t de Welch : statistique, degrés de liberté, p-valeur, intervalle de confiance."""
    va, vb = a["variance"] / a["n"], b["variance"] / b["n"]
    difference = a["moyenne"] - b["moyenne"]
    erreur_type = math.sqrt(va + vb)
    if erreur_type == 0.0:
        p = 1.0 if difference == 0.0 else 0.0
        return {"t": None, "ddl": None, "p": p, "ic": [difference, difference], "niveau": niveau,
                "note": "variances nulles dans les deux échantillons : test dégénéré"}
    t = difference / erreur_type
    ddl = (va + vb) ** 2 / (va ** 2 / (a["n"] - 1) + vb ** 2 / (b["n"] - 1))
    marge = quantile_student(0.5 + niveau / 2.0, ddl) * erreur_type
    return {"t": t, "ddl": ddl, "p": p_student_bilaterale(t, ddl),
            "ic": [difference - marge, difference + marge], "niveau": niveau, "erreur_type": erreur_type}


def rangs_moyens(valeurs_a: Sequence[float], valeurs_b: Sequence[float]) -> tuple[float, list[int]]:
    """Somme des rangs de a (rangs moyens en cas d'ex aequo) et tailles des groupes d'ex aequo."""
    combine = sorted([(v, 0) for v in valeurs_a] + [(v, 1) for v in valeurs_b])
    somme_a, groupes, i = 0.0, [], 0
    while i < len(combine):
        j = i
        while j + 1 < len(combine) and combine[j + 1][0] == combine[i][0]:
            j += 1
        rang = (i + j) / 2.0 + 1.0
        somme_a += rang * sum(1 for k in range(i, j + 1) if combine[k][1] == 0)
        if j > i:
            groupes.append(j - i + 1)
        i = j + 1
    return somme_a, groupes


def test_mann_whitney(valeurs_a: Sequence[float], valeurs_b: Sequence[float]) -> dict[str, Any]:
    """U de Mann-Whitney, approximation normale, continuité et correction des ex aequo."""
    na, nb = len(valeurs_a), len(valeurs_b)
    n = na + nb
    somme_a, groupes = rangs_moyens(valeurs_a, valeurs_b)
    u_a = somme_a - na * (na + 1) / 2.0
    moyenne_u = na * nb / 2.0
    terme_ex_aequo = sum(t ** 3 - t for t in groupes)
    variance_u = na * nb / 12.0 * ((n + 1) - terme_ex_aequo / (n * (n - 1)))
    resultat = {"u_a": u_a, "u_b": na * nb - u_a, "ex_aequo_groupes": len(groupes),
                "ex_aequo_valeurs": sum(groupes), "methode": "approximation normale, continuité 0,5"}
    if variance_u <= 0.0:
        return resultat | {"z": None, "p": 1.0, "note": "toutes les valeurs sont égales"}
    z = max(abs(u_a - moyenne_u) - 0.5, 0.0) / math.sqrt(variance_u)
    return resultat | {"z": z, "p": min(1.0, math.erfc(z / math.sqrt(2.0)))}


def quantile_lineaire(valeurs_triees: Sequence[float], q: float) -> float:
    """Quantile par interpolation linéaire sur une liste triée."""
    position = q * (len(valeurs_triees) - 1)
    bas = math.floor(position)
    haut = min(bas + 1, len(valeurs_triees) - 1)
    return valeurs_triees[bas] + (valeurs_triees[haut] - valeurs_triees[bas]) * (position - bas)


def bootstrap_difference(valeurs_a: Sequence[float], valeurs_b: Sequence[float], iterations: int,
                         graine: int, niveau: float) -> dict[str, Any]:
    """IC bootstrap percentile de la différence des moyennes, tirages reproductibles."""
    total = len(valeurs_a) + len(valeurs_b)
    if total > OBSERVATIONS_MAX_BOOTSTRAP:
        return {"statut": "non calculé", "raison": f"{total} observations > {OBSERVATIONS_MAX_BOOTSTRAP}"}
    effectif = min(iterations, max(200, BUDGET_BOOTSTRAP // total))
    generateur = random.Random(graine)
    differences = sorted(
        math.fsum(generateur.choices(valeurs_a, k=len(valeurs_a))) / len(valeurs_a)
        - math.fsum(generateur.choices(valeurs_b, k=len(valeurs_b))) / len(valeurs_b)
        for _ in range(effectif))
    queue = (1.0 - niveau) / 2.0
    return {"statut": "calculé", "iterations": effectif, "iterations_demandees": iterations,
            "graine": graine, "niveau": niveau, "methode": "percentile",
            "ic": [quantile_lineaire(differences, queue), quantile_lineaire(differences, 1.0 - queue)]}


def qualifier(valeur: float | None, seuils: Sequence[tuple[float, str]]) -> str | None:
    """Étiquette conventionnelle d'une taille d'effet (valeur absolue)."""
    if valeur is None:
        return None
    for seuil, etiquette in seuils:
        if abs(valeur) < seuil:
            return etiquette
    return "grand"


def delta_de_cliff(valeurs_a: Sequence[float], valeurs_b: Sequence[float]) -> float:
    """(#(a>b) − #(a<b)) / (na·nb), en O(n log n) par recherche dichotomique."""
    triees_b = sorted(valeurs_b)
    plus, moins = 0, 0
    for valeur in valeurs_a:
        moins += len(triees_b) - bisect.bisect_right(triees_b, valeur)
        plus += bisect.bisect_left(triees_b, valeur)
    return (plus - moins) / (len(valeurs_a) * len(valeurs_b))


def tailles_effet(a: dict[str, Any], b: dict[str, Any], valeurs_a: Sequence[float],
                  valeurs_b: Sequence[float]) -> dict[str, Any]:
    """d de Cohen (écart-type commun), g de Hedges, delta de Cliff, avec étiquettes."""
    na, nb = a["n"], b["n"]
    commun = math.sqrt(((na - 1) * a["variance"] + (nb - 1) * b["variance"]) / (na + nb - 2))
    d = (a["moyenne"] - b["moyenne"]) / commun if commun > 0 else None
    g = d * (1.0 - 3.0 / (4.0 * (na + nb) - 9.0)) if d is not None else None
    delta = delta_de_cliff(valeurs_a, valeurs_b)
    return {"cohen_d": d, "hedges_g": g, "interpretation_d": qualifier(d, SEUILS_COHEN),
            "ecart_type_commun": commun, "cliff_delta": delta,
            "interpretation_delta": qualifier(delta, SEUILS_CLIFF)}


def controler_avec_scipy(valeurs_a: Sequence[float], valeurs_b: Sequence[float],
                         welch: dict[str, Any], mw: dict[str, Any]) -> dict[str, Any]:
    """Recalcule les deux p-valeurs avec scipy et publie les écarts absolus."""
    try:
        p_welch = float(scipy_stats.ttest_ind(valeurs_a, valeurs_b, equal_var=False).pvalue)
        p_mw = float(scipy_stats.mannwhitneyu(valeurs_a, valeurs_b, alternative="two-sided",
                                              method="asymptotic", use_continuity=True).pvalue)
    except (ValueError, TypeError, FloatingPointError) as exc:
        return {"statut": "non comparable", "raison": f"{type(exc).__name__}: {exc}"}
    return {"statut": "calculé", "version": scipy.__version__,
            "p_welch_scipy": p_welch if math.isfinite(p_welch) else None,
            "ecart_p_welch": abs(p_welch - welch["p"]) if math.isfinite(p_welch) else None,
            "p_mann_whitney_scipy": p_mw, "ecart_p_mann_whitney": abs(p_mw - mw["p"])}


def avertir(a: dict[str, Any], b: dict[str, Any], welch: dict[str, Any], mw: dict[str, Any],
            alpha: float, echantillons: Sequence[Echantillon]) -> list[str]:
    """Signale les situations où la conclusion est fragile."""
    notes = [f"échantillon {e.nom} : virgule collée entre deux chiffres (« 1,5 ») lue comme séparateur ; "
             f"si ce sont des décimales, relancez avec --virgule-decimale" for e in echantillons if e.virgule_entre_chiffres]
    if min(a["n"], b["n"]) < 10:
        notes.append("moins de 10 observations dans un échantillon : puissance faible, p-valeurs approximatives")
    if (welch["p"] < alpha) != (mw["p"] < alpha):
        notes.append("Welch et Mann-Whitney concluent différemment : distributions probablement non normales "
                     "ou différences de forme plutôt que de position")
    for nom, desc in (("a", a), ("b", b)):
        if desc["ecart_type"] > 0 and abs(desc["moyenne"] - desc["mediane"]) > 0.5 * desc["ecart_type"]:
            notes.append(f"échantillon {nom} asymétrique (indice heuristique : moyenne et médiane écartées "
                         f"de plus d'un demi écart-type) ; préférer --test mann-whitney")
    return notes


def conclure(welch: dict[str, Any], mw: dict[str, Any], principal: str, alpha: float,
             effets: dict[str, Any]) -> dict[str, Any]:
    """Conclusion au seuil alpha sur le test principal, avec la taille d'effet."""
    p = welch["p"] if principal == "welch" else mw["p"]
    significatif = p < alpha
    taille = effets["interpretation_d"] if principal == "welch" else effets["interpretation_delta"]
    taille = taille or "indéfinie (écart-type commun nul)"
    phrase = (f"différence {'significative' if significatif else 'non significative'} au seuil {alpha} "
              f"({principal}, p = {p:.4g}) ; taille d'effet : {taille}")
    return {"alpha": alpha, "test_principal": principal, "p": p, "significatif": significatif, "phrase": phrase}


def analyser(ech_a: Echantillon, ech_b: Echantillon, args: argparse.Namespace,
             avec_scipy: bool) -> dict[str, Any]:
    """Enchaîne descriptions, tests, bootstrap, effets et contrôle croisé."""
    a, b = decrire(ech_a.valeurs), decrire(ech_b.valeurs)
    welch = test_welch(a, b, args.niveau)
    mw = test_mann_whitney(ech_a.valeurs, ech_b.valeurs)
    effets = tailles_effet(a, b, ech_a.valeurs, ech_b.valeurs)
    resultat = {
        "echantillons": {e.nom: d | {"source": e.source, "ignores": e.ignores, "exemples_ignores": e.exemples_ignores}
                         for e, d in ((ech_a, a), (ech_b, b))},
        "difference_moyennes": a["moyenne"] - b["moyenne"],
        "difference_medianes": a["mediane"] - b["mediane"],
        "welch": welch, "mann_whitney": mw,
        "bootstrap": bootstrap_difference(ech_a.valeurs, ech_b.valeurs, args.iterations, args.graine, args.niveau),
        "effets": effets,
        "conclusion": conclure(welch, mw, args.test, args.alpha, effets),
        "avertissements": avertir(a, b, welch, mw, args.alpha, (ech_a, ech_b)),
    }
    if avec_scipy:
        resultat["controle_scipy"] = controler_avec_scipy(ech_a.valeurs, ech_b.valeurs, welch, mw)
    return resultat


def verifier_effectifs(ech_a: Echantillon, ech_b: Echantillon) -> None:
    """Refuse de conclure sur rien (code 3) ou sur moins de deux valeurs (code 2)."""
    if not ech_a.valeurs and not ech_b.valeurs:
        raise ErreurEntree("dénominateur nul : aucun nombre lisible, rien à examiner", CODE_RIEN)
    for ech in (ech_a, ech_b):
        if len(ech.valeurs) < 2:
            raise ErreurEntree(f"refus : l'échantillon {ech.nom} ({ech.source}) a n = {len(ech.valeurs)} < 2 ; "
                               f"variance indéfinie, aucune conclusion possible")


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait du docstring les sections du contrat de mesure."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def construire_parseur() -> argparse.ArgumentParser:
    """Déclare l'interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Compare deux échantillons numériques indépendants : Welch, Mann-Whitney, "
                    "bootstrap, d de Cohen, delta de Cliff. Code 1 si la différence est significative.",
        epilog='exemple : comparer_echantillons.py --a "12.1 11.8 12.6 13.0" --b "13.2 13.9 12.8 14.1" --json   '
               "| comparer_echantillons.py avant.txt apres.txt --alpha 0.01",
    )
    parseur.add_argument("fichiers", nargs="*", metavar="FICHIER",
                         help="fichier(s) de nombres (séparés par espaces, retours, ; ou ,) ; # commente")
    parseur.add_argument("--a", metavar="NOMBRES", help='échantillon a en ligne : "1 2 3"')
    parseur.add_argument("--b", metavar="NOMBRES", help='échantillon b en ligne : "4 5 6"')
    parseur.add_argument("--alpha", type=float, default=0.05, help="seuil de signification (défaut 0.05)")
    parseur.add_argument("--niveau", type=float, default=0.95, help="niveau des intervalles de confiance (défaut 0.95)")
    parseur.add_argument("--test", choices=("welch", "mann-whitney"), default="welch",
                         help="test qui décide du code de sortie (défaut welch)")
    parseur.add_argument("--iterations", type=int, default=ITERATIONS_BOOTSTRAP,
                         help=f"tirages bootstrap (défaut {ITERATIONS_BOOTSTRAP}, réduits si n est grand)")
    parseur.add_argument("--graine", type=int, default=0, help="graine du bootstrap (défaut 0)")
    parseur.add_argument("--virgule-decimale", action="store_true",
                         help="la virgule est le séparateur décimal (1,5) ; sinon elle sépare les nombres")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT,
                         help="taille maximale lue par fichier (défaut 50 Mio)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : contre-vérifie les p-valeurs avec scipy s'il est installé")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def verifier_parametres(args: argparse.Namespace) -> None:
    """Refuse les paramètres hors domaine."""
    if not 0.0 < args.alpha < 1.0 or not 0.0 < args.niveau < 1.0:
        raise ErreurEntree("--alpha et --niveau doivent être strictement entre 0 et 1")
    if args.iterations < 1 or args.max_octets < 1:
        raise ErreurEntree("--iterations et --max-octets doivent être positifs")


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible : descriptions, tests, effets, conclusion."""
    for nom, d in rapport["echantillons"].items():
        print(f"{nom} : n={d['n']} moyenne={d['moyenne']:.6g} écart-type={d['ecart_type']:.6g} "
              f"médiane={d['mediane']:.6g} [{d['source']}]" + (f" ({d['ignores']} jetons écartés)" if d["ignores"] else ""))
    w, m, e = rapport["welch"], rapport["mann_whitney"], rapport["effets"]
    print(f"Welch : t={w['t'] if w['t'] is None else format(w['t'], '.4g')} ddl={w['ddl'] if w['ddl'] is None else format(w['ddl'], '.4g')} "
          f"p={w['p']:.4g} IC{w['niveau']:.0%}=[{w['ic'][0]:.6g} ; {w['ic'][1]:.6g}]")
    print(f"Mann-Whitney : U_a={m['u_a']:g} p={m['p']:.4g}")
    boot = rapport["bootstrap"]
    if boot["statut"] == "calculé":
        print(f"Bootstrap ({boot['iterations']} tirages, graine {boot['graine']}) : IC=[{boot['ic'][0]:.6g} ; {boot['ic'][1]:.6g}]")
    else:
        print(f"Bootstrap : non calculé ({boot['raison']})")
    texte_d = "indéfini (écart-type commun nul)" if e["cohen_d"] is None else f"{e['cohen_d']:.4g} ({e['interpretation_d']})"
    print(f"Effets : d de Cohen={texte_d}, delta de Cliff={e['cliff_delta']:.4g} ({e['interpretation_delta']})")
    print(rapport["conclusion"]["phrase"])
    for note in rapport["avertissements"]:
        print(f"  avertissement : {note}")


def nettoyer_non_finis(objet: Any) -> Any:
    """Remplace récursivement NaN et infinis par None : le JSON strict les interdit."""
    if isinstance(objet, float) and not math.isfinite(objet):
        return None
    if isinstance(objet, dict):
        return {cle: nettoyer_non_finis(valeur) for cle, valeur in objet.items()}
    if isinstance(objet, list):
        return [nettoyer_non_finis(valeur) for valeur in objet]
    return objet


def presenter_json(rapport: dict[str, Any]) -> None:
    """Écrit l'objet JSON unique sur stdout (NaN remplacés par null)."""
    print(json.dumps(nettoyer_non_finis(rapport), ensure_ascii=False, indent=2, allow_nan=False))


def base_rapport(examines: list[str], denominateur: int, moteur: str) -> dict[str, Any]:
    """Champs communs à tout rapport JSON, y compris en refus."""
    return {"outil": "comparer_echantillons", "moteur": moteur,
            "version_moteur": scipy.__version__ if moteur == "scipy" else None,
            "denominateur": denominateur, "unite_denominateur": "observations",
            "examines": examines, "contrat": extraire_contrat(__doc__ or "")}


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, refuse ou analyse, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    avec_scipy = args.moteur == "auto" and scipy_stats is not None
    if args.moteur == "auto" and scipy_stats is None:
        print("comparer_echantillons : scipy absent — repli stdlib seul, p-valeurs non contre-vérifiées", file=sys.stderr)
    moteur = "scipy" if avec_scipy else "stdlib"
    examines: list[str] = []
    denominateur = 0
    try:
        verifier_parametres(args)
        ech_a, ech_b = charger_echantillons(args)
        examines = [f"{e.nom} : {e.source} (n={len(e.valeurs)})" for e in (ech_a, ech_b)]
        denominateur = len(ech_a.valeurs) + len(ech_b.valeurs)
        verifier_effectifs(ech_a, ech_b)
        rapport = base_rapport(examines, denominateur, moteur) | analyser(ech_a, ech_b, args, avec_scipy)
    except (ErreurEntree, ArithmeticError, statistics.StatisticsError, OSError) as exc:
        code = exc.code if isinstance(exc, ErreurEntree) else CODE_USAGE
        print(f"comparer_echantillons : {exc}", file=sys.stderr)
        if args.json:
            presenter_json(base_rapport(examines, denominateur, moteur) | {"refus": str(exc)})
        return code
    if args.json:
        presenter_json(rapport)
    else:
        afficher_humain(rapport)
    return CODE_SIGNIFICATIF if rapport["conclusion"]["significatif"] else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
