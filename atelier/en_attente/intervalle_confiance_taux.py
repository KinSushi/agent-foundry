"""Intervalles de confiance d'un taux de réussite, différence de deux taux, McNemar exact, taille d'échantillon.

Un agent lit « 87/100 tests passés » comme 87 % tout court. Mesuré dans cette session :
pour 100/100, l'intervalle de Wald à 95 % rend [1 ; 1], une certitude, quand Wilson rend
[0,9630 ; 1] et Clopper-Pearson [0,9638 ; 1] ; l'interpréteur de référence n'a ni scipy ni
statsmodels. Tout est donc calculé en stdlib : sur 300 cas tirés au hasard (graine 42, n de 1
à 10^6), écart maximal à scipy 1.18.1 et statsmodels 0.15.0 de 5,5e-13 sur les bornes de
Clopper-Pearson et de 5,3e-12 sur la p-valeur exacte de McNemar.

QUESTION
    Ce taux de réussite (ex. 87/100 tests passés) est-il précis, et la différence
    avec cet autre taux est-elle réelle ?
MESURE
    Pour chaque taux k/n : intervalles de Wilson (score), de Clopper-Pearson
    (exact, par inversion de la bêta incomplète régularisée calculée en stdlib :
    fraction continue de Lentz puis dichotomie), d'Agresti-Coull, et de Wald
    (publié pour référence seulement, il est faux près de 0 et de 1). Pour deux
    taux indépendants : test z bilatéral à variance groupée et intervalle hybride
    de Newcombe (1998) sur la différence. Pour deux systèmes évalués sur les
    mêmes cas : table appariée et test exact de McNemar (binomiale sur les paires
    discordantes, bilatéral = 2 x la plus petite queue), plus le khi-deux corrigé
    de continuité. Taille d'échantillon pour une demi-largeur donnée : formule
    normale n = z² p (1 - p) / marge², et plus petit n dont l'intervalle de Wilson
    tient dans la marge. Si scipy ou statsmodels sont installés, chaque nombre est
    recalculé par eux et l'écart maximal est publié.
HYPOTHÈSES
    Essais indépendants et de même probabilité de réussite (un test instable,
    relancé jusqu'au succès, viole cette hypothèse) ; pour deux taux, échantillons
    indépendants ; pour McNemar, les deux systèmes ont vu exactement les mêmes cas.
    Les issues lues dans un fichier sont bien des réussites et des échecs
    (1/0, true/false, pass/fail, oui/non, réussi/échec...).
LIMITES
    Pas d'intervalle sur la différence appariée (seulement le test de McNemar) ;
    pas de correction pour comparaisons multiples ; un fichier ne fournit que
    deux colonnes au plus ; les issues neutres (skip, xfail, na) sont écartées et
    comptées, les jetons inconnus aussi. La décision de seuil porte sur la borne
    basse de l'intervalle choisi (Wilson par défaut), pas sur la proportion brute.
CONTRE-EXEMPLES
    Constaté : pour n = 100, la couverture exacte (sommation binomiale) de
    l'intervalle de Wilson à 95 % tombe à 86,06 % quand le vrai taux vaut
    p = 0,0015 (grille de pas 0,0005) : sur des échecs très rares, la décision
    de seuil par défaut promet 95 % de confiance sans la tenir. Sur la même
    grille, Clopper-Pearson (--methode clopper-pearson) ne descend pas sous
    95,04 %.
INVOCATION
    {outil} 87/100 --json
DOMAINE
    Taux de réussite de suites de tests, d'évaluations d'agents ou de modèles
    (exactitude sur un banc), de quelques essais à quelques millions.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
NOM_OUTIL = "intervalle_confiance_taux"
MOTEUR_STDLIB = "stdlib"

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

METHODES = ("wilson", "clopper-pearson", "agresti-coull", "wald")
OCTETS_MAX_DEFAUT = 100 * 1024 * 1024
MAX_EXAMINES = 50
MAX_EXEMPLES = 5
SEUIL_EXACT_MCNEMAR = 2000
EPSILON_FRACTION = 1e-15
ITERATIONS_FRACTION = 100_000
ITERATIONS_DICHOTOMIE = 2000
MINUSCULE = 1e-300

MOTIF_TAUX = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
MOTIF_SEPARATEURS = re.compile(r"[\s,;]+")
SUCCES = frozenset({"1", "true", "vrai", "oui", "yes", "y", "o", "pass", "passed", "ok",
                    "succes", "succès", "success", "réussi", "reussi", "réussite", "reussite"})
ECHECS = frozenset({"0", "false", "faux", "non", "no", "n", "fail", "failed", "failure", "ko",
                    "echec", "échec", "échoué", "echoue", "error", "erreur", "errored"})
NEUTRES = frozenset({"skip", "skipped", "ignore", "ignoré", "ignored", "na", "n/a", "nan",
                     "xfail", "xpass", "none", "null", "-"})
CLES_ISSUE = ("resultat", "résultat", "issue", "outcome", "statut", "status", "passed",
              "succes", "succès", "reussi", "réussi", "ok", "correct")


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Taux:
    """Un taux observé : k réussites sur n essais, et ce qui a été écarté."""

    nom: str
    source: str
    succes: int = 0
    n: int = 0
    neutres: int = 0
    inconnus: int = 0
    exemples_inconnus: list[str] = field(default_factory=list)


@dataclass
class Appariement:
    """Table appariée de deux systèmes sur les mêmes cas (a, b, c, d)."""

    source: str
    deux_reussis: int = 0
    seul_a: int = 0
    seul_b: int = 0
    deux_echoues: int = 0
    ecartes: int = 0

    @property
    def total(self) -> int:
        """Nombre de cas appariés retenus."""
        return self.deux_reussis + self.seul_a + self.seul_b + self.deux_echoues


# --------------------------------------------------------------------------- lecture


def resoudre(chemin: str, racine: Path | None) -> Path:
    """Résout un chemin relatif contre --racine, ou le répertoire courant."""
    brut = Path(chemin)
    return brut if brut.is_absolute() or racine is None else racine / brut


def lire_texte_borne(chemin: Path, octets_max: int) -> str:
    """Lit un fichier texte utf-8 borné ; refuse absent, dossier, binaire, trop gros."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} est un dossier : un fichier de résultats ou un taux k/n est attendu")
    with chemin.open("rb") as flux:
        donnees = flux.read(octets_max + 1)
    if len(donnees) > octets_max:
        raise ErreurEntree(f"{chemin} dépasse --max-octets {octets_max}")
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas un fichier de résultats")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en utf-8 (octet {exc.start}) : {exc.reason}") from exc


def classer_issue(valeur: Any) -> str:
    """Classe une valeur lue : 'succes', 'echec', 'neutre' ou 'inconnu'."""
    if isinstance(valeur, bool):
        return "succes" if valeur else "echec"
    if isinstance(valeur, (int, float)):
        return "succes" if valeur == 1 else "echec" if valeur == 0 else "inconnu"
    if valeur is None:
        return "neutre"
    jeton = str(valeur).strip().casefold()
    if jeton in SUCCES:
        return "succes"
    if jeton in ECHECS:
        return "echec"
    if jeton in NEUTRES or not jeton:
        return "neutre"
    return "inconnu"


def compter_issue(taux: Taux, valeur: Any) -> str:
    """Ajoute une issue au taux et rend sa classe."""
    classe = classer_issue(valeur)
    if classe == "succes":
        taux.succes += 1
        taux.n += 1
    elif classe == "echec":
        taux.n += 1
    elif classe == "neutre":
        taux.neutres += 1
    else:
        taux.inconnus += 1
        if len(taux.exemples_inconnus) < MAX_EXEMPLES:
            taux.exemples_inconnus.append(str(valeur)[:40])
    return classe


def format_fichier(chemin: Path, colonnes: Sequence[str]) -> str:
    """Devine le format : jsonl, csv, tsv ou texte (une issue par jeton)."""
    suffixe = chemin.suffix.casefold()
    if suffixe in (".jsonl", ".ndjson"):
        return "jsonl"
    if suffixe == ".tsv":
        return "tsv"
    if suffixe == ".csv" or colonnes:
        return "csv"
    return "texte"


def lignes_tabulaires(texte: str, format_: str) -> Iterable[dict[str, Any]]:
    """Rend les enregistrements d'un fichier csv, tsv ou jsonl."""
    if format_ == "jsonl":
        for numero, ligne in enumerate(texte.splitlines(), 1):
            if not ligne.strip():
                continue
            try:
                objet = json.loads(ligne)
            except json.JSONDecodeError as exc:
                raise ErreurEntree(f"ligne {numero} : JSON invalide ({exc.msg})") from exc
            yield objet if isinstance(objet, dict) else {"": objet}
        return
    lecteur = csv.DictReader(io.StringIO(texte), delimiter="\t" if format_ == "tsv" else ",")
    try:
        yield from lecteur
    except csv.Error as exc:
        raise ErreurEntree(f"csv illisible ligne {lecteur.line_num} : {exc}") from exc


def choisir_cle(enregistrement: dict[str, Any], colonne: str | None) -> str:
    """Clé portant l'issue : --colonne, la seule colonne, ou un nom usuel."""
    if colonne:
        if colonne not in enregistrement:
            raise ErreurEntree(f"colonne {colonne!r} absente ; colonnes : {', '.join(map(str, enregistrement))}")
        return colonne
    if len(enregistrement) == 1:
        return next(iter(enregistrement))
    for cle in CLES_ISSUE:
        if cle in enregistrement:
            return cle
    raise ErreurEntree(f"plusieurs colonnes ({', '.join(map(str, enregistrement))}) : préciser --colonne")


def lire_fichier_simple(chemin: Path, texte: str, colonne: str | None) -> Taux:
    """Un taux depuis un fichier : jetons libres, ou une colonne csv/jsonl."""
    taux = Taux(nom=chemin.name, source=str(chemin))
    format_ = format_fichier(chemin, [colonne] if colonne else [])
    if format_ == "texte":
        for ligne in texte.splitlines():
            for jeton in MOTIF_SEPARATEURS.split(ligne.split("#", 1)[0]):
                if jeton:
                    compter_issue(taux, jeton)
        return taux
    cle: str | None = None
    for enregistrement in lignes_tabulaires(texte, format_):
        cle = cle or choisir_cle(enregistrement, colonne)
        compter_issue(taux, enregistrement.get(cle))
    return taux


def lire_fichier_apparie(chemin: Path, texte: str, colonnes: Sequence[str]) -> tuple[Appariement, Taux, Taux]:
    """Deux colonnes sur les mêmes cas : table appariée et taux de chaque système."""
    format_ = format_fichier(chemin, colonnes)
    if format_ == "texte":
        raise ErreurEntree("--colonnes exige un fichier csv, tsv ou jsonl")
    table = Appariement(source=str(chemin))
    taux_a, taux_b = Taux(nom=colonnes[0], source=str(chemin)), Taux(nom=colonnes[1], source=str(chemin))
    for enregistrement in lignes_tabulaires(texte, format_):
        for colonne in colonnes:
            if colonne not in enregistrement:
                raise ErreurEntree(f"colonne {colonne!r} absente ; colonnes : {', '.join(map(str, enregistrement))}")
        issue_a = classer_issue(enregistrement[colonnes[0]])
        issue_b = classer_issue(enregistrement[colonnes[1]])
        if {issue_a, issue_b} <= {"succes", "echec"}:
            ranger_paire(table, issue_a == "succes", issue_b == "succes")
            compter_issue(taux_a, enregistrement[colonnes[0]])
            compter_issue(taux_b, enregistrement[colonnes[1]])
        else:
            table.ecartes += 1
    return table, taux_a, taux_b


def ranger_paire(table: Appariement, reussi_a: bool, reussi_b: bool) -> None:
    """Range un cas apparié dans la bonne case de la table."""
    if reussi_a and reussi_b:
        table.deux_reussis += 1
    elif reussi_a:
        table.seul_a += 1
    elif reussi_b:
        table.seul_b += 1
    else:
        table.deux_echoues += 1


def lire_taux_en_ligne(texte: str, nom: str) -> Taux:
    """Un taux écrit « k/n »."""
    trouve = MOTIF_TAUX.match(texte)
    if not trouve:
        raise ErreurEntree(f"{texte!r} n'est ni un taux k/n ni un fichier")
    k, n = int(trouve.group(1)), int(trouve.group(2))
    if k > n:
        raise ErreurEntree(f"taux {texte!r} : {k} réussites pour {n} essais, impossible")
    return Taux(nom=nom, source=texte.strip(), succes=k, n=n)


def lire_mcnemar(texte: str) -> Appariement:
    """Paires discordantes en ligne « B,C » : B = seul A réussit, C = seul B réussit."""
    morceaux = [m for m in MOTIF_SEPARATEURS.split(texte.strip()) if m]
    if len(morceaux) != 2 or not all(m.isdigit() for m in morceaux):
        raise ErreurEntree("--mcnemar attend deux entiers « B,C » (cas où seul A réussit, cas où seul B réussit)")
    return Appariement(source=f"--mcnemar {texte}", seul_a=int(morceaux[0]), seul_b=int(morceaux[1]))


def charger(args: argparse.Namespace) -> tuple[list[Taux], Appariement | None]:
    """Construit les taux et l'éventuelle table appariée depuis la ligne de commande."""
    taux: list[Taux] = []
    if args.mcnemar and args.entrees:
        raise ErreurEntree("--mcnemar se suffit à lui-même : ne pas y ajouter de taux ni de fichier")
    table: Appariement | None = lire_mcnemar(args.mcnemar) if args.mcnemar else None
    colonnes = [c.strip() for c in args.colonnes.split(",")] if args.colonnes else []
    if colonnes and len(colonnes) != 2:
        raise ErreurEntree("--colonnes attend exactement deux noms « A,B »")
    for position, entree in enumerate(args.entrees):
        nom = "ab"[position] if position < 2 else str(position)
        if MOTIF_TAUX.match(entree):
            taux.append(lire_taux_en_ligne(entree, nom))
            continue
        chemin = resoudre(entree, args.racine)
        texte = lire_texte_borne(chemin, args.max_octets)
        if colonnes:
            if table is not None:
                raise ErreurEntree("une seule table appariée à la fois (--colonnes ou --mcnemar)")
            table, taux_a, taux_b = lire_fichier_apparie(chemin, texte, colonnes)
            taux.extend([taux_a, taux_b])
        else:
            taux.append(lire_fichier_simple(chemin, texte, args.colonne))
    if len(taux) > 2:
        raise ErreurEntree(f"{len(taux)} taux fournis : deux au plus (comparaison d'un taux à un autre)")
    return taux, table


# --------------------------------------------------------------------------- calcul


def quantile_normal(niveau: float) -> float:
    """z tel que P(|Z| ≤ z) = niveau."""
    return NormalDist().inv_cdf(0.5 + niveau / 2.0)


def borner(intervalle: tuple[float, float]) -> list[float]:
    """Ramène un intervalle dans [0, 1]."""
    return [max(0.0, intervalle[0]), min(1.0, intervalle[1])]


def ic_wilson(k: int, n: int, z: float) -> list[float]:
    """Intervalle du score de Wilson (sans correction de continuité)."""
    p = k / n
    denominateur = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominateur
    demi = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominateur
    bornes = borner((centre - demi, centre + demi))
    return [0.0 if k == 0 else bornes[0], 1.0 if k == n else bornes[1]]


def ic_agresti_coull(k: int, n: int, z: float) -> list[float]:
    """Intervalle d'Agresti-Coull : Wald sur k + z²/2 réussites et n + z² essais."""
    n_tilde = n + z * z
    p_tilde = (k + z * z / 2.0) / n_tilde
    demi = z * math.sqrt(p_tilde * (1.0 - p_tilde) / n_tilde)
    return borner((p_tilde - demi, p_tilde + demi))


def ic_wald(k: int, n: int, z: float) -> list[float]:
    """Intervalle de Wald, publié pour montrer son défaut près de 0 et 1."""
    p = k / n
    demi = z * math.sqrt(p * (1.0 - p) / n)
    return borner((p - demi, p + demi))


def fraction_continue_beta(a: float, b: float, x: float) -> float:
    """Fraction continue de la bêta incomplète (Lentz modifié)."""
    somme, moins, plus = a + b, a - 1.0, a + 1.0
    c, d = 1.0, 1.0 - somme * x / plus
    d = 1.0 / (d if abs(d) > MINUSCULE else MINUSCULE)
    resultat = d
    for m in range(1, ITERATIONS_FRACTION + 1):
        double = 2 * m
        for terme in (m * (b - m) * x / ((moins + double) * (a + double)),
                      -(a + m) * (somme + m) * x / ((a + double) * (plus + double))):
            d = 1.0 + terme * d
            d = 1.0 / (d if abs(d) > MINUSCULE else MINUSCULE)
            c = 1.0 + terme / c
            c = c if abs(c) > MINUSCULE else MINUSCULE
            delta = c * d
            resultat *= delta
        if abs(delta - 1.0) < EPSILON_FRACTION:
            return resultat
    raise ArithmeticError(f"fraction continue non convergente (a={a}, b={b}, x={x})")


def beta_reguliere(a: float, b: float, x: float) -> float:
    """Bêta incomplète régularisée I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_tete = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log1p(-x))
    tete = math.exp(log_tete)
    if x < (a + 1.0) / (a + b + 2.0):
        return tete * fraction_continue_beta(a, b, x) / a
    return 1.0 - tete * fraction_continue_beta(b, a, 1.0 - x) / b


def beta_inverse(q: float, a: float, b: float) -> float:
    """x tel que I_x(a, b) = q, par dichotomie jusqu'à la résolution du flottant."""
    bas, haut = 0.0, 1.0
    for _ in range(ITERATIONS_DICHOTOMIE):
        milieu = (bas + haut) / 2.0
        if milieu in (bas, haut):
            break
        if beta_reguliere(a, b, milieu) < q:
            bas = milieu
        else:
            haut = milieu
    return (bas + haut) / 2.0


def ic_clopper_pearson(k: int, n: int, niveau: float) -> list[float]:
    """Intervalle exact de Clopper-Pearson ; la borne haute passe par le complément."""
    queue = (1.0 - niveau) / 2.0
    bas = 0.0 if k == 0 else beta_inverse(queue, k, n - k + 1)
    haut = 1.0 if k == n else 1.0 - beta_inverse(queue, n - k, k + 1)
    return [bas, haut]


def intervalles(k: int, n: int, niveau: float) -> dict[str, list[float]]:
    """Les quatre intervalles d'un taux."""
    z = quantile_normal(niveau)
    return {"wilson": ic_wilson(k, n, z), "clopper-pearson": ic_clopper_pearson(k, n, niveau),
            "agresti-coull": ic_agresti_coull(k, n, z), "wald": ic_wald(k, n, z)}


def verdict_seuil(intervalle: Sequence[float], seuil: float) -> str:
    """Position de l'intervalle par rapport au seuil."""
    if intervalle[0] >= seuil:
        return "atteint"
    if intervalle[1] < seuil:
        return "non atteint"
    return "indécis"


def decrire_taux(taux: Taux, args: argparse.Namespace) -> dict[str, Any]:
    """Proportion, intervalles et verdict de seuil d'un taux."""
    ics = intervalles(taux.succes, taux.n, args.niveau)
    sortie: dict[str, Any] = {
        "nom": taux.nom, "source": taux.source, "succes": taux.succes, "n": taux.n,
        "proportion": taux.succes / taux.n, "intervalles": ics,
        "demi_largeur_wilson": (ics["wilson"][1] - ics["wilson"][0]) / 2.0,
        "neutres_ecartes": taux.neutres, "inconnus_ecartes": taux.inconnus,
        "exemples_inconnus": taux.exemples_inconnus,
    }
    if args.seuil is not None:
        sortie["seuil"] = {"valeur": args.seuil, "methode": args.methode,
                           "verdict": verdict_seuil(ics[args.methode], args.seuil)}
    return sortie


def test_z_deux_taux(a: Taux, b: Taux) -> dict[str, Any]:
    """Test z bilatéral de l'égalité de deux proportions, variance groupée."""
    p1, p2 = a.succes / a.n, b.succes / b.n
    commun = (a.succes + b.succes) / (a.n + b.n)
    erreur = math.sqrt(commun * (1.0 - commun) * (1.0 / a.n + 1.0 / b.n))
    if erreur == 0.0:
        return {"z": None, "p": 1.0 if p1 == p2 else 0.0, "note": "proportion commune 0 ou 1 : test dégénéré"}
    z = (p1 - p2) / erreur
    return {"z": z, "p": math.erfc(abs(z) / math.sqrt(2.0))}


def ic_newcombe(a: Taux, b: Taux, niveau: float) -> list[float]:
    """Intervalle hybride du score de Newcombe (méthode 10) pour p_a - p_b."""
    z = quantile_normal(niveau)
    p1, p2 = a.succes / a.n, b.succes / b.n
    l1, u1 = ic_wilson(a.succes, a.n, z)
    l2, u2 = ic_wilson(b.succes, b.n, z)
    difference = p1 - p2
    return [difference - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            difference + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)]


def comparer_deux_taux(a: Taux, b: Taux, niveau: float) -> dict[str, Any]:
    """Différence de deux taux indépendants : estimation, Newcombe, test z."""
    newcombe = ic_newcombe(a, b, niveau)
    test = test_z_deux_taux(a, b)
    significatif = newcombe[0] > 0.0 or newcombe[1] < 0.0
    sortie = {"difference": a.succes / a.n - b.succes / b.n, "ic_newcombe": newcombe,
              "test_z": test, "niveau": niveau, "significatif": significatif,
              "decision": "intervalle de Newcombe excluant 0"}
    if significatif != (test["p"] < 1.0 - niveau):
        sortie["note"] = "Newcombe et le test z concluent différemment : cas limite, ne pas trancher"
    return sortie


def queue_binomiale_demi(k: int, n: int) -> float:
    """P(X ≤ k) pour X ~ Binomiale(n, 1/2), exacte jusqu'à SEUIL_EXACT_MCNEMAR."""
    if n <= SEUIL_EXACT_MCNEMAR:
        return sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return beta_reguliere(n - k, k + 1, 0.5)


def test_mcnemar(table: Appariement, niveau: float) -> dict[str, Any]:
    """McNemar exact (et khi-deux corrigé) sur les paires discordantes."""
    discordants = table.seul_a + table.seul_b
    sortie: dict[str, Any] = {
        "table": {"deux_reussis": table.deux_reussis, "seul_a_reussit": table.seul_a,
                  "seul_b_reussit": table.seul_b, "deux_echouent": table.deux_echoues},
        "discordants": discordants, "cas_ecartes": table.ecartes, "source": table.source,
    }
    if discordants == 0:
        return sortie | {"p_exact": 1.0, "khi2_corrige": None, "p_khi2": None, "significatif": False,
                         "note": "aucune paire discordante : les deux systèmes ne diffèrent sur aucun cas"}
    p_exact = min(1.0, 2.0 * queue_binomiale_demi(min(table.seul_a, table.seul_b), discordants))
    khi2 = (abs(table.seul_a - table.seul_b) - 1.0) ** 2 / discordants
    sortie |= {"p_exact": p_exact, "khi2_corrige": khi2, "p_khi2": math.erfc(math.sqrt(khi2 / 2.0)),
               "significatif": p_exact < 1.0 - niveau}
    if table.total:
        sortie["difference_appariee"] = (table.seul_a - table.seul_b) / table.total
    return sortie


def demi_largeur_wilson(p: float, n: int, z: float) -> float:
    """Demi-largeur de Wilson pour une proportion p observée sur n essais."""
    return z / (1.0 + z * z / n) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))


def taille_echantillon(marge: float, p: float, niveau: float) -> dict[str, Any]:
    """n nécessaire pour une demi-largeur donnée : formule normale et recherche Wilson."""
    z = quantile_normal(niveau)
    brut = z * z * p * (1.0 - p) / (marge * marge)
    bas, haut = 1, max(4, 4 * math.ceil(brut) + 100)
    while demi_largeur_wilson(p, haut, z) > marge:
        haut *= 2
    while bas < haut:
        milieu = (bas + haut) // 2
        if demi_largeur_wilson(p, milieu, z) <= marge:
            haut = milieu
        else:
            bas = milieu + 1
    sortie = {"marge": marge, "p_suppose": p, "niveau": niveau, "n_normal_brut": brut,
              "n_normal": math.ceil(brut - 1e-9), "n_wilson": bas}
    if p in (0.0, 1.0):
        sortie["note"] = "p supposé 0 ou 1 : la formule normale rend 0, seule la recherche Wilson a un sens"
    return sortie


# --------------------------------------------------------------------------- contrôle croisé


def importer_optionnels(moteur: str) -> dict[str, Any]:
    """Importe scipy et statsmodels s'ils sont présents ; une ligne sur stderr sinon."""
    modules: dict[str, Any] = {}
    if moteur == "stdlib":
        return modules
    absents = []
    try:
        import scipy
        import scipy.stats as stats_scipy
        modules["scipy"] = (scipy.__version__, stats_scipy)
    except ImportError:
        absents.append("scipy")
    try:
        import statsmodels
        import statsmodels.stats.contingency_tables as tables_sm
        import statsmodels.stats.proportion as proportion_sm
        modules["statsmodels"] = (statsmodels.__version__, proportion_sm, tables_sm)
    except ImportError:
        absents.append("statsmodels")
    if absents:
        print(f"{NOM_OUTIL} : {' et '.join(absents)} absent(s) — repli stdlib seul, résultats non "
              f"contre-vérifiés par {', '.join(absents)}", file=sys.stderr)
    return modules


def paires_scipy(stats: Any, rapport: dict[str, Any], niveau: float) -> list[tuple[str, float, float]]:
    """Nombres recalculés par scipy : Wilson, Clopper-Pearson, McNemar exact."""
    paires = []
    for taux in rapport["taux"]:
        resultat = stats.binomtest(taux["succes"], taux["n"])
        for methode, nom in (("wilson", "wilson"), ("exact", "clopper-pearson")):
            ic = resultat.proportion_ci(confidence_level=niveau, method=methode)
            paires += [(f"{taux['nom']}.{nom}.bas", taux["intervalles"][nom][0], float(ic.low)),
                       (f"{taux['nom']}.{nom}.haut", taux["intervalles"][nom][1], float(ic.high))]
    mcnemar = rapport.get("mcnemar")
    if mcnemar and mcnemar["discordants"]:
        table = mcnemar["table"]
        p = stats.binomtest(table["seul_a_reussit"], mcnemar["discordants"], 0.5).pvalue
        paires.append(("mcnemar.p_exact", mcnemar["p_exact"], float(p)))
    return paires


def paires_statsmodels(modules: tuple[Any, ...], rapport: dict[str, Any], niveau: float) -> list[tuple[str, float, float]]:
    """Nombres recalculés par statsmodels : quatre intervalles, Newcombe, z, McNemar, taille."""
    _, proportion, tables = modules
    alpha, paires = 1.0 - niveau, []
    for taux in rapport["taux"]:
        for nom, methode in (("wilson", "wilson"), ("clopper-pearson", "beta"),
                             ("agresti-coull", "agresti_coull"), ("wald", "normal")):
            bas, haut = proportion.proportion_confint(taux["succes"], taux["n"], alpha=alpha, method=methode)
            paires += [(f"{taux['nom']}.{nom}.bas", taux["intervalles"][nom][0], float(bas)),
                       (f"{taux['nom']}.{nom}.haut", taux["intervalles"][nom][1], float(haut))]
    paires += paires_difference_statsmodels(proportion, rapport, alpha)
    mcnemar = rapport.get("mcnemar")
    if mcnemar and mcnemar["discordants"]:
        t = mcnemar["table"]
        matrice = [[t["deux_reussis"], t["seul_a_reussit"]], [t["seul_b_reussit"], t["deux_echouent"]]]
        paires.append(("mcnemar.p_exact", mcnemar["p_exact"], float(tables.mcnemar(matrice, exact=True).pvalue)))
        corrige = tables.mcnemar(matrice, exact=False, correction=True)
        paires += [("mcnemar.khi2", mcnemar["khi2_corrige"], float(corrige.statistic)),
                   ("mcnemar.p_khi2", mcnemar["p_khi2"], float(corrige.pvalue))]
    taille = rapport.get("taille_echantillon")
    if taille and 0.0 < taille["p_suppose"] < 1.0:
        n = proportion.samplesize_confint_proportion(taille["p_suppose"], taille["marge"], alpha=alpha, method="normal")
        paires.append(("taille.n_normal_brut", taille["n_normal_brut"], float(n)))
    return paires


def paires_difference_statsmodels(proportion: Any, rapport: dict[str, Any], alpha: float) -> list[tuple[str, float, float]]:
    """Newcombe et test z recalculés par statsmodels."""
    difference = rapport.get("difference")
    if not difference:
        return []
    a, b = rapport["taux"]
    bas, haut = proportion.confint_proportions_2indep(a["succes"], a["n"], b["succes"], b["n"],
                                                      method="newcomb", compare="diff", alpha=alpha)
    paires = [("difference.newcombe.bas", difference["ic_newcombe"][0], float(bas)),
              ("difference.newcombe.haut", difference["ic_newcombe"][1], float(haut))]
    if difference["test_z"]["z"] is not None:
        _, p = proportion.proportions_ztest([a["succes"], b["succes"]], [a["n"], b["n"]])
        paires.append(("difference.test_z.p", difference["test_z"]["p"], float(p)))
    return paires


def controle_croise(modules: dict[str, Any], rapport: dict[str, Any], niveau: float) -> dict[str, Any]:
    """Recalcule ce qui peut l'être et publie l'écart maximal par bibliothèque."""
    sortie: dict[str, Any] = {}
    for nom, fonction in (("scipy", paires_scipy), ("statsmodels", paires_statsmodels)):
        if nom not in modules:
            continue
        try:
            argument = modules[nom][1] if nom == "scipy" else modules[nom]
            paires = fonction(argument, rapport, niveau)
        except (ValueError, TypeError, ArithmeticError) as exc:
            sortie[nom] = {"version": modules[nom][0], "statut": "non comparable", "raison": f"{type(exc).__name__}: {exc}"}
            continue
        ecarts = [{"grandeur": g, "stdlib": x, nom: y, "ecart": abs(x - y)} for g, x, y in paires]
        sortie[nom] = {"version": modules[nom][0], "statut": "calculé" if ecarts else "aucune grandeur comparable",
                       "comparaisons": len(ecarts), "ecart_max": max((e["ecart"] for e in ecarts), default=None),
                       "details": ecarts}
    return sortie


# --------------------------------------------------------------------------- sortie


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring."""
    contrat: dict[str, str] = {}
    courant = ""
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def nettoyer_non_finis(objet: Any) -> Any:
    """Remplace NaN et infinis par None : le JSON strict les interdit."""
    if isinstance(objet, float) and not math.isfinite(objet):
        return None
    if isinstance(objet, dict):
        return {cle: nettoyer_non_finis(valeur) for cle, valeur in objet.items()}
    if isinstance(objet, list):
        return [nettoyer_non_finis(valeur) for valeur in objet]
    return objet


def base_rapport(denominateur: int, examines: list[str], moteur: str) -> dict[str, Any]:
    """Champs communs à tout rapport JSON, y compris en refus."""
    return {"outil": NOM_OUTIL, "moteur": moteur, "moteur_calcul": MOTEUR_STDLIB,
            "denominateur": denominateur, "unite_denominateur": "essais (ou demande de planification)",
            "examines": examines[:MAX_EXAMINES], "examines_tronques": len(examines) > MAX_EXAMINES,
            "contrat": extraire_contrat(__doc__ or "")}


def avertissements(rapport: dict[str, Any]) -> list[str]:
    """Situations où la lecture des intervalles demande prudence."""
    notes = []
    for taux in rapport["taux"]:
        if taux["n"] < 30:
            notes.append(f"{taux['nom']} : n = {taux['n']} < 30, intervalles larges ; préférer Wilson ou Clopper-Pearson")
        if taux["succes"] in (0, taux["n"]):
            notes.append(f"{taux['nom']} : taux de 0 ou 100 % : l'intervalle de Wald est dégénéré, ne pas l'utiliser")
        if taux["inconnus_ecartes"]:
            notes.append(f"{taux['nom']} : {taux['inconnus_ecartes']} jeton(s) inconnu(s) écarté(s), ex. {taux['exemples_inconnus']}")
    return notes


def analyser(taux: list[Taux], table: Appariement | None, args: argparse.Namespace) -> dict[str, Any]:
    """Assemble intervalles, comparaison, McNemar et taille d'échantillon."""
    resultat: dict[str, Any] = {"niveau": args.niveau, "methode_decision": args.methode,
                                "taux": [decrire_taux(t, args) for t in taux]}
    if len(taux) == 2 and table is None:
        resultat["difference"] = comparer_deux_taux(taux[0], taux[1], args.niveau)
    if table is not None:
        resultat["mcnemar"] = test_mcnemar(table, args.niveau)
    if args.marge is not None:
        p = args.p_attendu if args.p_attendu is not None else (taux[0].succes / taux[0].n if len(taux) == 1 else 0.5)
        resultat["taille_echantillon"] = taille_echantillon(args.marge, p, args.niveau)
    resultat["avertissements"] = avertissements(resultat)
    return resultat


def defaut_trouve(rapport: dict[str, Any]) -> bool:
    """Code 1 : seuil non prouvé atteint, différence significative, ou McNemar significatif."""
    seuil_rate = any(t.get("seuil", {}).get("verdict") not in (None, "atteint") for t in rapport["taux"])
    difference = rapport.get("difference", {}).get("significatif", False)
    return seuil_rate or difference or rapport.get("mcnemar", {}).get("significatif", False)


def formater_ic(ic: Sequence[float]) -> str:
    """[bas ; haut] avec quatre décimales."""
    return f"[{ic[0]:.4f} ; {ic[1]:.4f}]"


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    niveau = f"{rapport['niveau']:.0%}"
    for taux in rapport["taux"]:
        print(f"{taux['nom']} : {taux['succes']}/{taux['n']} = {taux['proportion']:.4f}  ({taux['source']})")
        for methode, ic in taux["intervalles"].items():
            suffixe = "  (référence, déconseillé)" if methode == "wald" else ""
            print(f"  IC {niveau} {methode:<16} {formater_ic(ic)}{suffixe}")
        if "seuil" in taux:
            s = taux["seuil"]
            print(f"  seuil {s['valeur']} ({s['methode']}) : {s['verdict']}")
    if "difference" in rapport:
        d = rapport["difference"]
        print(f"différence a - b = {d['difference']:+.4f}, Newcombe {formater_ic(d['ic_newcombe'])}, "
              f"test z p = {d['test_z']['p']:.4g} -> {'significative' if d['significatif'] else 'non significative'}")
    if "mcnemar" in rapport:
        m = rapport["mcnemar"]
        print(f"McNemar : seul A {m['table']['seul_a_reussit']}, seul B {m['table']['seul_b_reussit']}, "
              f"p exact = {m['p_exact']:.4g} -> {'significatif' if m['significatif'] else 'non significatif'}")
    if "taille_echantillon" in rapport:
        t = rapport["taille_echantillon"]
        print(f"taille pour ±{t['marge']} à {niveau} (p = {t['p_suppose']}) : n = {t['n_normal']} (normale), "
              f"{t['n_wilson']} (Wilson)")
    for nom, controle in rapport.get("controle_croise", {}).items():
        print(f"contrôle {nom} {controle['version']} : {controle['statut']}, écart max {controle.get('ecart_max')}")
    for note in rapport["avertissements"]:
        print(f"  avertissement : {note}")


def presenter_json(rapport: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(nettoyer_non_finis(rapport), ensure_ascii=False, indent=2, allow_nan=False))


def construire_parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Intervalles de confiance d'un taux de réussite (Wilson, Clopper-Pearson, Agresti-Coull), "
                    "différence de deux taux (z, Newcombe), McNemar exact, taille d'échantillon.",
        epilog="exemples : intervalle_confiance_taux.py 87/100 --json | intervalle_confiance_taux.py 87/100 "
               "79/100 | intervalle_confiance_taux.py resultats.csv --colonnes avant,apres | "
               "intervalle_confiance_taux.py 870/1000 --seuil 0.85 | intervalle_confiance_taux.py --marge 0.03",
    )
    parseur.add_argument("entrees", nargs="*", metavar="ENTREE",
                         help="taux « k/n » ou fichier de résultats (une issue par jeton, ou csv/tsv/jsonl)")
    parseur.add_argument("--mcnemar", metavar="B,C", help="paires discordantes : cas où seul A réussit, cas où seul B réussit")
    parseur.add_argument("--colonne", help="colonne portant l'issue (csv/jsonl)")
    parseur.add_argument("--colonnes", metavar="A,B", help="deux colonnes appariées (mêmes cas) : McNemar")
    parseur.add_argument("--niveau", type=float, default=0.95, help="niveau de confiance (défaut 0.95)")
    parseur.add_argument("--methode", choices=METHODES, default="wilson", help="intervalle qui décide du seuil (défaut wilson)")
    parseur.add_argument("--seuil", type=float, help="taux minimal exigé : code 1 si la borne basse ne l'atteint pas")
    parseur.add_argument("--marge", type=float, help="demi-largeur visée : calcule la taille d'échantillon nécessaire")
    parseur.add_argument("--p-attendu", type=float, help="proportion supposée pour --marge (défaut : taux observé, sinon 0.5)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue par fichier")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : contre-vérifie avec scipy et statsmodels s'ils sont installés")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def verifier_parametres(args: argparse.Namespace) -> None:
    """Refuse les paramètres hors domaine."""
    if not 0.0 < args.niveau < 1.0:
        raise ErreurEntree("--niveau doit être strictement entre 0 et 1")
    if args.marge is not None and not 0.0 < args.marge < 1.0:
        raise ErreurEntree("--marge doit être strictement entre 0 et 1")
    for nom, valeur in (("--seuil", args.seuil), ("--p-attendu", args.p_attendu)):
        if valeur is not None and not 0.0 <= valeur <= 1.0:
            raise ErreurEntree(f"{nom} doit être entre 0 et 1")
    if args.colonne and args.colonnes:
        raise ErreurEntree("--colonne et --colonnes sont exclusifs")
    if not args.entrees and not args.mcnemar and args.marge is None:
        raise ErreurEntree("rien à calculer : donner un taux k/n, un fichier, --mcnemar ou --marge")


def denombrer(taux: list[Taux], table: Appariement | None, args: argparse.Namespace) -> tuple[int, list[str]]:
    """Dénominateur : essais lus (paires appariées comptées une fois), ou la demande de planification."""
    examines = [f"{t.nom} : {t.source} ({t.succes}/{t.n})" for t in taux]
    if table is not None and not taux:
        examines.append(f"mcnemar : {table.source}")
        return table.seul_a + table.seul_b, examines
    total = sum(t.n for t in taux) // (2 if table is not None else 1)
    if total == 0 and not taux and args.marge is not None:
        return 1, [f"planification : marge {args.marge}"]
    return total, examines


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, refuse ou calcule, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    examines: list[str] = []
    denominateur = 0
    moteur = MOTEUR_STDLIB
    try:
        verifier_parametres(args)
        taux, table = charger(args)
        denominateur, examines = denombrer(taux, table, args)
        vides = [t for t in taux if t.n == 0]
        if denominateur == 0 or vides:
            raise ErreurEntree(f"dénominateur nul : aucun essai exploitable ({', '.join(t.source for t in vides) or 'entrée vide'}), "
                               "rien à examiner", CODE_RIEN)
        modules = importer_optionnels(args.moteur)
        moteur = "+".join([MOTEUR_STDLIB, *modules]) if modules else MOTEUR_STDLIB
        rapport = base_rapport(denominateur, examines, moteur) | analyser(taux, table, args)
        if modules:
            rapport["controle_croise"] = controle_croise(modules, rapport, args.niveau)
    except (ErreurEntree, ArithmeticError, OSError) as exc:
        code = exc.code if isinstance(exc, ErreurEntree) else CODE_USAGE
        print(f"{NOM_OUTIL} : {exc}", file=sys.stderr)
        if args.json:
            presenter_json(base_rapport(denominateur, examines, moteur) | {"refus": str(exc)})
        return code
    if args.json:
        presenter_json(rapport)
    else:
        afficher_humain(rapport)
    return CODE_DEFAUT if defaut_trouve(rapport) else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
