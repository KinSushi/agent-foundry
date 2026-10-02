"""VAN, TRI, XIRR, échéancier d'emprunt et taux effectif, calculés en décimal exact.

Un agent qui calcule un TRI prend la première racine venue : mesuré dans cette session,
numpy-financial 1.1.0 rend `irr([-100, 230, -132]) = 0.1000000000000021` sans un mot,
alors que 20 % annule aussi la VAN ; cet outil rend les deux et signale l'ambiguïté.
Et un échéancier arrondi au centime à chaque mois dérive : 200 000 à 3,5 % sur 240
mois, mensualité 1 159,92, finit en flottants sur un solde de -0,14 au lieu de 0 ; ici
la dernière échéance est ajustée et la somme des amortissements vaut le capital au
centime.

QUESTION
    Que valent ces flux financiers : VAN, TRI, échéancier d'emprunt ?
MESURE
    VAN au taux donné (premier flux en t = 0, non actualisé) et, pour comparaison,
    la valeur « tableur » qui actualise aussi le premier flux ; TRI par Newton
    (point de départ 10 %) avec bissection de secours, après balayage de la VAN
    sur 4 000 points pour trouver toutes les racines entre -99 % et +10 000 % par
    période et compter les changements de signe des flux (règle de Descartes) ;
    XIRR et XVAN à dates irrégulières, convention Exact/365 (jours réels divisés
    par 365, origine au premier flux) ; échéancier à mensualités constantes :
    intérêts du mois arrondis au centime, amortissement = mensualité - intérêts,
    capital restant, dernière échéance ajustée pour solder exactement ; taux
    mensuel proportionnel (annuel/12) ou actuariel ; taux effectif annuel ;
    taux annuel effectif global approché avec frais. Calcul en
    decimal.Decimal à 40 chiffres. Si numpy-financial est installé, VAN, TRI et
    mensualité sont refaits par lui et l'écart est publié.
HYPOTHÈSES
    Flux en fin de période régulière (sauf dates données) ; montants signés
    (décaissement négatif) ; dates en ISO 8601 (AAAA-MM-JJ) ; arrondi bancaire
    « demi au-dessus » sauf --arrondi demi-pair ; frais payés à la mise à
    disposition des fonds.
LIMITES
    Pas d'assurance, de différé, de taux variable ni de remboursement anticipé ;
    le taux annuel effectif global est approché sur des mois égaux (le calcul
    réglementaire compte les jours exacts) ; un TRI hors de [-99 %, +10 000 %] par
    période n'est pas cherché ; pas de convention Exact/360 ni 30/360 ; la VAN
    ne dit rien du risque.
CONTRE-EXEMPLES
    Constaté dans cette session : les flux [-100, 220, -121] ont une racine double
    à 10 % (la VAN touche zéro sans changer de signe) ; le balayage ne voit aucun
    changement de signe, Newton ne converge pas en 100 itérations, et l'outil
    répond « aucun TRI » (code 1) alors que 10 % annule la VAN.
INVOCATION
    {outil} --flux=-1000,300,400,500 --taux 0.08 --json
DOMAINE
    Décisions d'investissement, prêts amortissables à échéances constantes,
    comparaison d'offres, contrôle de tableurs financiers, en toute devise.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import numpy_financial
except ImportError:
    numpy_financial = None

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
NORME_DATES = "ISO 8601"
FORMAT_DATE = "AAAA-MM-JJ"
INDICATEUR_XIRR = "XIRR"
INDICATEUR_XVAN = "XVAN"

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

PRECISION = 40
CENTIME = Decimal("0.01")
DIX_CHIFFRES = Decimal("1e-10")
TOLERANCE_RACINE = Decimal("1e-28")
ITERATIONS_NEWTON = 100
ITERATIONS_BISSECTION = 400
POINTS_BALAYAGE = 4000
TAUX_MIN = -0.99
TAUX_MAX = 100.0
DEPART_NEWTON = Decimal("0.1")
JOURS_ANNEE = Decimal(365)
TAILLE_MAX_FICHIER = 20 * 1024 * 1024
LIGNES_MAX = 100_000
DUREE_MAX = 1200
EXAMINES_MAX = 50
ARRONDIS = {"demi-haut": ROUND_HALF_UP, "demi-pair": ROUND_HALF_EVEN}
ESPACES_MILLIERS = (" ", " ", " ", "_", "'")


class ErreurFinance(Exception):
    """Entrée invalide : code de sortie porté."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Flux:
    """Flux lus : montants, dates éventuelles, origine."""

    montants: tuple[Decimal, ...]
    dates: tuple[date, ...] | None
    source: str


# --------------------------------------------------------------------------- #
# Lecture des nombres

def apercu(texte: str) -> str:
    """Extrait court et sur une ligne d'une entrée, pour les messages."""
    court = " ".join(texte.split())
    return court if len(court) <= 40 else court[:40] + "…"


def lire_montant(texte: str, virgule_decimale: bool) -> Decimal:
    """Lit « -1000 », « 1 234,56 » (si virgule décimale), « (500) » (négatif comptable)."""
    brut = texte.strip()
    negatif = brut.startswith("(") and brut.endswith(")")
    brut = brut.strip("()")
    for espace in ESPACES_MILLIERS:
        brut = brut.replace(espace, "")
    if virgule_decimale:
        brut = brut.replace(",", ".")
    try:
        valeur = Decimal(brut)
    except InvalidOperation as erreur:
        raise ErreurFinance(f"montant « {apercu(texte)} » illisible") from erreur
    if not valeur.is_finite():
        raise ErreurFinance(f"montant « {apercu(texte)} » non fini")
    return -valeur if negatif else valeur


def lire_taux(texte: str, nom: str) -> Decimal:
    """Lit « 0.08 », « 8% », « 8 % », « 0,08 » ; refuse ≤ -100 %."""
    brut = texte.strip().replace(",", ".").replace(" ", "")
    pourcent = brut.endswith("%")
    try:
        valeur = Decimal(brut.rstrip("%"))
    except InvalidOperation as erreur:
        raise ErreurFinance(f"{nom} « {texte} » illisible (0.08 ou 8% attendu)") from erreur
    if not valeur.is_finite():
        raise ErreurFinance(f"{nom} « {texte} » non fini")
    valeur = valeur / 100 if pourcent else valeur
    if valeur <= -1:
        raise ErreurFinance(f"{nom} « {texte} » : un taux doit être > -100 %")
    return valeur


def lire_flux_texte(texte: str) -> list[Decimal]:
    """« -1000,300,400 » ou « -1000;300,5;400 » (point-virgule : virgule décimale)."""
    virgule_decimale = ";" in texte
    morceaux = texte.split(";") if virgule_decimale else re.split(r"[,\s]+", texte.strip())
    montants = [lire_montant(m, virgule_decimale) for m in morceaux if m.strip()]
    if not montants:
        raise ErreurFinance("--flux : aucun montant")
    return montants


def lire_date(texte: str) -> date:
    """Date ISO 8601 AAAA-MM-JJ."""
    try:
        return date.fromisoformat(texte.strip())
    except ValueError as erreur:
        raise ErreurFinance(f"date « {apercu(texte)} » : format {FORMAT_DATE} attendu") from erreur


def lire_dates_texte(texte: str) -> list[date]:
    """« 2024-01-01,2024-07-01 » ou séparées par ; ou des espaces."""
    return [lire_date(m) for m in re.split(r"[,;\s]+", texte.strip()) if m]


# --------------------------------------------------------------------------- #
# Lecture d'un CSV de flux

def lire_texte_borne(chemin: Path) -> str:
    """Texte UTF-8 d'un fichier borné ; refuse absent, dossier, binaire."""
    if not chemin.exists():
        raise ErreurFinance(f"{chemin} : fichier introuvable")
    if chemin.is_dir():
        raise ErreurFinance(f"{chemin} : est un dossier, un CSV de flux est attendu")
    if chemin.stat().st_size > TAILLE_MAX_FICHIER:
        raise ErreurFinance(f"{chemin} : plus de {TAILLE_MAX_FICHIER} octets")
    try:
        donnees = chemin.read_bytes()
    except OSError as erreur:
        raise ErreurFinance(f"{chemin} : lecture impossible ({erreur.strerror})") from erreur
    if b"\x00" in donnees:
        raise ErreurFinance(f"{chemin} : contenu binaire, CSV texte attendu")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as erreur:
        raise ErreurFinance(f"{chemin} : n'est pas du texte UTF-8 (octet {erreur.start})") from erreur


def separateur_csv(texte: str) -> str:
    """Séparateur dominant de la première ligne utile."""
    premiere = next((l for l in texte.splitlines() if l.strip() and not l.lstrip().startswith("#")), "")
    return max(";\t,", key=premiere.count) if any(s in premiere for s in ";\t,") else ","


def est_entete(ligne: Sequence[str]) -> bool:
    """Une ligne sans aucun chiffre est un en-tête."""
    return not any(re.search(r"\d", champ) for champ in ligne)


def interpreter_ligne(champs: Sequence[str], virgule: bool) -> tuple[date | None, Decimal]:
    """« montant » ou « date ; montant »."""
    utiles = [c for c in champs if c.strip()]
    if len(utiles) == 1:
        return None, lire_montant(utiles[0], virgule)
    if len(utiles) == 2 and re.match(r"^\s*\d{4}-\d{2}-\d{2}\s*$", utiles[0]):
        return lire_date(utiles[0]), lire_montant(utiles[1], virgule)
    raise ErreurFinance(f"ligne « {apercu(';'.join(champs))} » : « montant » ou « date;montant » attendu")


def lire_csv_flux(chemin: Path) -> Flux:
    """CSV d'une colonne (montants) ou deux (date ISO, montant), en-tête facultatif."""
    texte = lire_texte_borne(chemin)
    separateur = separateur_csv(texte)
    lignes = [l for l in csv.reader(io.StringIO(texte), delimiter=separateur)
              if l and any(c.strip() for c in l) and not l[0].lstrip().startswith("#")]
    if lignes and est_entete(lignes[0]):
        lignes = lignes[1:]
    if len(lignes) > LIGNES_MAX:
        raise ErreurFinance(f"{chemin} : plus de {LIGNES_MAX} lignes")
    lus = []
    for numero, ligne in enumerate(lignes, start=1):
        try:
            lus.append(interpreter_ligne(ligne, separateur != ","))
        except ErreurFinance as erreur:
            raise ErreurFinance(f"{chemin.name}, ligne utile {numero} : {erreur}") from erreur
    dates = [d for d, _ in lus]
    if any(d is not None for d in dates) and not all(d is not None for d in dates):
        raise ErreurFinance(f"{chemin} : dates présentes sur certaines lignes seulement")
    return Flux(tuple(m for _, m in lus), tuple(dates) if lus and dates[0] is not None else None, str(chemin))


# --------------------------------------------------------------------------- #
# VAN et TRI à périodes régulières

def van(montants: Sequence[Decimal], taux: Decimal) -> Decimal:
    """VAN = Σ CF_t / (1+r)^t, t = 0..n-1 (Horner sur v = 1/(1+r))."""
    v = 1 / (1 + taux)
    total = Decimal(0)
    for montant in reversed(montants):
        total = total * v + montant
    return total


def derivee_van(montants: Sequence[Decimal], taux: Decimal) -> Decimal:
    """dVAN/dr = Σ -t CF_t (1+r)^(-t-1)."""
    v = 1 / (1 + taux)
    total = Decimal(0)
    for t in range(len(montants) - 1, 0, -1):
        total = total * v - t * montants[t]
    return total * v * v


def signe_van_flottant(montants: Sequence[float], x: float) -> float:
    """VAN en flottants au point x = 1/(1+r), mise à l'échelle pour éviter le dépassement."""
    total = 0.0
    if x <= 1.0:
        for montant in reversed(montants):
            total = total * x + montant
        return total
    y = 1.0 / x
    for montant in montants:
        total = total * y + montant
    return total


def grille_taux() -> list[float]:
    """Taux de balayage, répartis en log(1+r) entre TAUX_MIN et TAUX_MAX."""
    bas, haut = math.log1p(TAUX_MIN), math.log1p(TAUX_MAX)
    pas = (haut - bas) / (POINTS_BALAYAGE - 1)
    return [math.expm1(bas + i * pas) for i in range(POINTS_BALAYAGE)]


def encadrements(fonction: Callable[[float], float]) -> list[tuple[float, float]]:
    """Intervalles de taux où la fonction change de signe."""
    grille = grille_taux()
    valeurs = [fonction(r) for r in grille]
    paires = []
    for (r1, f1), (r2, f2) in zip(zip(grille, valeurs), zip(grille[1:], valeurs[1:])):
        if f1 == 0:
            paires.append((r1, r1))
        elif f1 * f2 < 0:
            paires.append((r1, r2))
    return paires


def bissection(fonction: Callable[[Decimal], Decimal], bas: Decimal, haut: Decimal) -> Decimal:
    """Bissection décimale sur un intervalle encadrant une racine."""
    f_bas = fonction(bas)
    for _ in range(ITERATIONS_BISSECTION):
        milieu = (bas + haut) / 2
        f_milieu = fonction(milieu)
        if f_milieu == 0 or haut - bas < TOLERANCE_RACINE:
            return milieu
        if (f_milieu > 0) == (f_bas > 0):
            bas, f_bas = milieu, f_milieu
        else:
            haut = milieu
    return (bas + haut) / 2


def newton(fonction: Callable[[Decimal], Decimal], derivee: Callable[[Decimal], Decimal],
           depart: Decimal) -> Decimal | None:
    """Newton décimal ; None s'il diverge, sort du domaine ou stagne."""
    taux = depart
    for _ in range(ITERATIONS_NEWTON):
        pente = derivee(taux)
        if pente == 0:
            return None
        suivant = taux - fonction(taux) / pente
        if suivant <= -1 or not suivant.is_finite():
            return None
        if abs(suivant - taux) < TOLERANCE_RACINE:
            return suivant
        taux = suivant
    return None


def raffiner(fonction: Callable[[Decimal], Decimal], derivee: Callable[[Decimal], Decimal],
             bas: float, haut: float) -> Decimal:
    """Racine dans [bas, haut] : Newton depuis le milieu, bissection si Newton sort de l'intervalle."""
    if bas == haut:
        return Decimal(repr(bas))
    gauche, droite = Decimal(repr(bas)), Decimal(repr(haut))
    candidat = newton(fonction, derivee, (gauche + droite) / 2)
    if candidat is not None and gauche <= candidat <= droite:
        return candidat
    return bissection(fonction, gauche, droite)


def racines(fonction: Callable[[Decimal], Decimal], derivee: Callable[[Decimal], Decimal],
            fonction_flottante: Callable[[float], float]) -> list[Decimal]:
    """Toutes les racines trouvées par balayage puis raffinement, dédoublonnées."""
    trouvees: list[Decimal] = []
    for bas, haut in encadrements(fonction_flottante):
        racine = raffiner(fonction, derivee, bas, haut)
        if not any(abs(racine - r) < DIX_CHIFFRES for r in trouvees):
            trouvees.append(racine)
    return sorted(trouvees)


def changements_de_signe(montants: Sequence[Decimal]) -> int:
    """Nombre de changements de signe (zéros ignorés) : borne de Descartes."""
    signes = [m > 0 for m in montants if m != 0]
    return sum(a != b for a, b in zip(signes, signes[1:]))


def choisir_tri(fonction: Callable[[Decimal], Decimal], derivee: Callable[[Decimal], Decimal],
                toutes: Sequence[Decimal]) -> tuple[Decimal | None, str]:
    """TRI principal : Newton depuis 10 % ; bissection de secours sinon."""
    candidat = newton(fonction, derivee, DEPART_NEWTON)
    if candidat is not None and any(abs(candidat - r) < DIX_CHIFFRES for r in toutes):
        return candidat, "newton"
    if candidat is not None and not toutes:
        return candidat, "newton"
    if toutes:
        proche = min(toutes, key=lambda r: abs(r - DEPART_NEWTON))
        return proche, "bissection"
    return None, "aucune"


def analyser_tri(fonction: Callable[[Decimal], Decimal], derivee: Callable[[Decimal], Decimal],
                 fonction_flottante: Callable[[float], float], signes: int) -> dict[str, Any]:
    """TRI principal, toutes les racines, diagnostic d'unicité."""
    toutes = racines(fonction, derivee, fonction_flottante) if signes else []
    principal, methode = choisir_tri(fonction, derivee, toutes) if signes else (None, "aucune")
    return {"tri": texte_taux(principal), "methode": methode, "tri_tous": [texte_taux(r) for r in toutes],
            "changements_de_signe": signes, "tri_unique": principal is not None and len(toutes) <= 1}


# --------------------------------------------------------------------------- #
# XVAN et XIRR (dates irrégulières, Exact/365)

def fractions_annee(dates: Sequence[date]) -> list[Decimal]:
    """(d_i - d_0) / 365 : convention Exact/365, origine au premier flux."""
    origine = dates[0]
    return [Decimal((d - origine).days) / JOURS_ANNEE for d in dates]


def xvan(montants: Sequence[Decimal], annees: Sequence[Decimal], taux: Decimal) -> Decimal:
    """Σ CF_i / (1+r)^(t_i), via exp(-t_i ln(1+r))."""
    logarithme = (1 + taux).ln()
    return sum((m * (-t * logarithme).exp() for m, t in zip(montants, annees)), Decimal(0))


def derivee_xvan(montants: Sequence[Decimal], annees: Sequence[Decimal], taux: Decimal) -> Decimal:
    """Σ -t_i CF_i / (1+r)^(t_i+1)."""
    base = 1 + taux
    logarithme = base.ln()
    return sum((-t * m * (-t * logarithme).exp() for m, t in zip(montants, annees)), Decimal(0)) / base


def xvan_flottante(montants: Sequence[float], annees: Sequence[float], taux: float) -> float:
    """XVAN en flottants pour le balayage."""
    try:
        return sum(m * math.exp(-t * math.log1p(taux)) for m, t in zip(montants, annees))
    except OverflowError:
        return math.copysign(math.inf, montants[-1]) if montants else 0.0


# --------------------------------------------------------------------------- #
# Emprunt

def taux_mensuel(annuel: Decimal, conversion: str) -> Decimal:
    """Taux mensuel proportionnel (annuel/12) ou actuariel ((1+a)^(1/12) - 1)."""
    if conversion == "proportionnel":
        return annuel / 12
    return (1 + annuel) ** (Decimal(1) / 12) - 1


def mensualite_exacte(capital: Decimal, taux: Decimal, duree: int) -> Decimal:
    """M = C r / (1 - (1+r)^-n) ; C/n si r = 0."""
    if taux == 0:
        return capital / duree
    return capital * taux / (1 - (1 + taux) ** -duree)


def echeancier(capital: Decimal, taux: Decimal, duree: int, arrondi: str) -> dict[str, Any]:
    """Tableau d'amortissement au centime, dernière échéance ajustée."""
    mode = ARRONDIS[arrondi]
    exacte = mensualite_exacte(capital, taux, duree)
    mensualite = exacte.quantize(CENTIME, rounding=mode)
    restant, lignes = capital, []
    for numero in range(1, duree + 1):
        interets = (restant * taux).quantize(CENTIME, rounding=mode)
        principal = restant if numero == duree else mensualite - interets
        restant -= principal
        lignes.append({"numero": numero, "echeance": str(principal + interets), "interets": str(interets),
                       "principal": str(principal), "capital_restant": str(restant)})
    return {"mensualite": mensualite, "mensualite_exacte": exacte, "lignes": lignes}


def resumer_emprunt(capital: Decimal, taux: Decimal, duree: int, arrondi: str) -> dict[str, Any]:
    """Échéancier, totaux, ajustement final et contrôle du capital."""
    table = echeancier(capital, taux, duree, arrondi)
    lignes = table["lignes"]
    total_interets = sum((Decimal(l["interets"]) for l in lignes), Decimal(0))
    total_principal = sum((Decimal(l["principal"]) for l in lignes), Decimal(0))
    derniere = Decimal(lignes[-1]["echeance"])
    return {"capital": str(capital), "duree_mois": duree, "taux_mensuel": texte_taux(taux),
            "mensualite": str(table["mensualite"]), "mensualite_exacte": str(table["mensualite_exacte"].quantize(DIX_CHIFFRES)),
            "derniere_echeance": str(derniere), "ajustement_final": str(derniere - table["mensualite"]),
            "total_interets": str(total_interets), "total_rembourse": str(total_interets + total_principal),
            "capital_amorti_egal_capital": total_principal == capital,
            "solde_final": lignes[-1]["capital_restant"], "echeancier": lignes}


def taeg_approche(capital: Decimal, frais: Decimal, lignes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Taux annuel effectif global approché : TRI mensuel des flux (C - frais, -échéances), annualisé."""
    montants = [capital - frais] + [-Decimal(l["echeance"]) for l in lignes]
    flottants = [float(m) for m in montants]
    analyse = analyser_tri(lambda r: van(montants, r), lambda r: derivee_van(montants, r),
                           lambda r: signe_van_flottant(flottants, 1 / (1 + r)), changements_de_signe(montants))
    if analyse["tri"] is None:
        return {"taeg_approche": None, "tri_mensuel": None}
    mensuel = Decimal(analyse["tri"])
    return {"tri_mensuel": analyse["tri"], "taeg_approche": texte_taux((1 + mensuel) ** 12 - 1)}


# --------------------------------------------------------------------------- #
# Présentation et contre-vérification

def texte_taux(taux: Decimal | None) -> str | None:
    """Taux arrondi à 12 décimales, en chaîne exacte."""
    if taux is None:
        return None
    return str(taux.quantize(Decimal("1e-12")).normalize()) if taux != 0 else "0"


def en_pourcent(texte: str | None) -> str:
    """« 0.0889633947 » → « 8.8963 % »."""
    return "—" if texte is None else f"{Decimal(texte) * 100:.4f} %"


def controler_numpy(montants: Sequence[Decimal], taux: Decimal | None, analyse: dict[str, Any]) -> dict[str, Any] | None:
    """VAN et TRI par numpy-financial, écarts publiés."""
    if numpy_financial is None:
        return None
    valeurs = [float(m) for m in montants]
    controle: dict[str, Any] = {"moteur": "numpy-financial", "version": getattr(numpy_financial, "__version__", None)}
    if taux is not None:
        van_np = float(numpy_financial.npv(float(taux), valeurs))
        controle.update({"van": van_np, "ecart_van": van_np - float(van(montants, taux))})
    tri_np = float(numpy_financial.irr(valeurs))
    controle["tri"] = None if math.isnan(tri_np) else tri_np
    if analyse["tri"] is not None and not math.isnan(tri_np):
        controle["ecart_tri"] = tri_np - float(analyse["tri"])
    return controle


def controler_emprunt_numpy(capital: Decimal, taux: Decimal, duree: int, exacte: str) -> dict[str, Any] | None:
    """Mensualité par numpy-financial (pmt), écart publié."""
    if numpy_financial is None:
        return None
    mensualite = -float(numpy_financial.pmt(float(taux), duree, float(capital)))
    return {"moteur": "numpy-financial", "mensualite": mensualite, "ecart_mensualite": mensualite - float(exacte)}


# --------------------------------------------------------------------------- #
# Calculs assemblés

def analyser_flux(flux: Flux, taux: Decimal | None, periodes_an: int) -> dict[str, Any]:
    """VAN, TRI (ou XVAN, XIRR si dates)."""
    montants = list(flux.montants)
    flottants = [float(m) for m in montants]
    signes = changements_de_signe(montants)
    if flux.dates is None:
        analyse = analyser_tri(lambda r: van(montants, r), lambda r: derivee_van(montants, r),
                               lambda r: signe_van_flottant(flottants, 1 / (1 + r)), signes)
        if taux is not None:
            valeur = van(montants, taux)
            analyse.update({"taux": texte_taux(taux), "van": str(valeur.quantize(CENTIME)),
                            "van_convention_tableur": str((valeur / (1 + taux)).quantize(CENTIME))})
        if analyse["tri"] is not None and periodes_an > 1:
            analyse["tri_annualise"] = texte_taux((1 + Decimal(analyse["tri"])) ** periodes_an - 1)
        analyse["controle"] = controler_numpy(montants, taux, analyse)
        return analyse
    return analyser_flux_dates(montants, flux.dates, taux, signes)


def analyser_flux_dates(montants: list[Decimal], dates: Sequence[date], taux: Decimal | None, signes: int) -> dict[str, Any]:
    """XVAN et XIRR (Exact/365)."""
    if list(dates) != sorted(dates):
        raise ErreurFinance("dates non croissantes : trier les flux par date")
    annees = fractions_annee(dates)
    annees_f = [float(a) for a in annees]
    flottants = [float(m) for m in montants]
    analyse = analyser_tri(lambda r: xvan(montants, annees, r), lambda r: derivee_xvan(montants, annees, r),
                           lambda r: xvan_flottante(flottants, annees_f, r), signes)
    analyse = {"convention": "Exact/365, origine " + dates[0].isoformat(),
               **{("x" + k if k in ("tri", "tri_tous") else k): v for k, v in analyse.items()}}
    if taux is not None:
        analyse.update({"taux": texte_taux(taux), "xvan": str(xvan(montants, annees, taux).quantize(CENTIME))})
    return analyse


def analyser_emprunt(args: argparse.Namespace) -> dict[str, Any]:
    """Échéancier, taux effectif, taux annuel effectif global approché et contrôle."""
    capital = lire_montant(args.emprunt, False)
    if capital <= 0:
        raise ErreurFinance("--emprunt : capital strictement positif attendu")
    if args.taux_annuel is None or args.duree_mois is None:
        raise ErreurFinance("--emprunt exige --taux-annuel et --duree-mois")
    if not 1 <= args.duree_mois <= DUREE_MAX:
        raise ErreurFinance(f"--duree-mois entre 1 et {DUREE_MAX}")
    annuel = lire_taux(args.taux_annuel, "--taux-annuel")
    if annuel < 0:
        raise ErreurFinance("--taux-annuel négatif non pris en charge")
    mensuel = taux_mensuel(annuel, args.conversion)
    resume = resumer_emprunt(capital, mensuel, args.duree_mois, args.arrondi)
    resume.update({"taux_annuel": texte_taux(annuel), "conversion": args.conversion,
                   "taux_effectif_annuel": texte_taux((1 + mensuel) ** 12 - 1)})
    frais = lire_montant(args.frais, False) if args.frais else Decimal(0)
    if frais:
        resume.update(taeg_approche(capital, frais, resume["echeancier"]))
    resume["controle"] = controler_emprunt_numpy(capital, mensuel, args.duree_mois, resume["mensualite_exacte"])
    return resume


def taux_effectif(args: argparse.Namespace) -> dict[str, Any]:
    """Taux effectif annuel d'un taux nominal capitalisé m fois par an."""
    nominal = lire_taux(args.taux_nominal, "--taux-nominal")
    if args.capitalisations < 1:
        raise ErreurFinance("--capitalisations ≥ 1 attendu")
    effectif = (1 + nominal / args.capitalisations) ** args.capitalisations - 1
    return {"taux_nominal": texte_taux(nominal), "capitalisations": args.capitalisations,
            "taux_effectif": texte_taux(effectif)}


def lire_flux(args: argparse.Namespace) -> Flux | None:
    """Flux depuis --flux/--dates ou depuis le fichier CSV."""
    if args.fichier and args.flux:
        raise ErreurFinance("donner --flux ou un fichier CSV, pas les deux")
    if args.fichier:
        chemin = Path(args.fichier).expanduser()
        flux = lire_csv_flux(args.racine / chemin if args.racine and not chemin.is_absolute() else chemin)
    elif args.flux:
        flux = Flux(tuple(lire_flux_texte(args.flux)), None, "--flux")
    else:
        return None
    if args.dates:
        dates = lire_dates_texte(args.dates)
        if len(dates) != len(flux.montants):
            raise ErreurFinance(f"{len(dates)} dates pour {len(flux.montants)} flux")
        flux = Flux(flux.montants, tuple(dates), flux.source)
    return flux


def calculer(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Exécute les calculs demandés ; rend le rapport et les éléments examinés."""
    rapport: dict[str, Any] = {}
    examines: list[str] = []
    flux = lire_flux(args)
    taux = lire_taux(args.taux, "--taux") if args.taux else None
    if flux is not None:
        if not flux.montants:
            raise ErreurFinance(f"{flux.source} : aucun flux", CODE_RIEN)
        rapport["flux"] = {"source": flux.source, "nombre": len(flux.montants),
                           **analyser_flux(flux, taux, args.periodes_par_an)}
        examines += [f"flux {i} : {m}" for i, m in enumerate(flux.montants)]
    if args.emprunt:
        rapport["emprunt"] = analyser_emprunt(args)
        examines += [f"échéance {l['numero']}" for l in rapport["emprunt"]["echeancier"]]
    if args.taux_nominal:
        rapport["taux_effectif"] = taux_effectif(args)
        examines.append(f"taux nominal {args.taux_nominal}")
    return rapport, examines


# --------------------------------------------------------------------------- #
# Interface

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
        description="VAN, TRI (toutes les racines), XIRR Exact/365, échéancier d'emprunt au centime et taux "
                    "effectifs, en arithmétique décimale ; contre-vérification numpy-financial si présent.",
        epilog="exemple : calculer_finance.py --flux=-1000,300,400,500 --taux 8%   "
               "| calculer_finance.py flux.csv --json   "
               "| calculer_finance.py --emprunt 200000 --taux-annuel 3.5% --duree-mois 240 --frais 1500",
    )
    parseur.add_argument("fichier", nargs="?", metavar="FICHIER",
                         help="CSV de flux : une colonne (montant) ou deux (date AAAA-MM-JJ, montant)")
    parseur.add_argument("--flux", metavar="MONTANTS",
                         help="flux signés : --flux=-1000,300,400 (« ; » si virgule décimale)")
    parseur.add_argument("--dates", metavar="DATES", help="dates AAAA-MM-JJ des flux (active XIRR/XVAN)")
    parseur.add_argument("--taux", metavar="TAUX", help="taux d'actualisation par période (0.08 ou 8%%)")
    parseur.add_argument("--periodes-par-an", type=int, default=1, help="pour annualiser le TRI (12 si flux mensuels)")
    parseur.add_argument("--emprunt", metavar="CAPITAL", help="capital emprunté")
    parseur.add_argument("--taux-annuel", metavar="TAUX", help="taux nominal annuel de l'emprunt")
    parseur.add_argument("--duree-mois", type=int, help="nombre de mensualités")
    parseur.add_argument("--conversion", choices=("proportionnel", "actuariel"), default="proportionnel",
                         help="taux mensuel : annuel/12 (défaut) ou (1+annuel)^(1/12)-1")
    parseur.add_argument("--frais", metavar="MONTANT", help="frais de dossier (taux annuel effectif global approché)")
    parseur.add_argument("--arrondi", choices=tuple(ARRONDIS), default="demi-haut", help="arrondi au centime")
    parseur.add_argument("--taux-nominal", metavar="TAUX", help="taux nominal annuel à convertir en taux effectif")
    parseur.add_argument("--capitalisations", type=int, default=12, help="capitalisations par an (défaut 12)")
    parseur.add_argument("--echeancier-complet", action="store_true", help="afficher toutes les lignes (texte)")
    parseur.add_argument("--sortie", type=Path, help="écrire l'échéancier en CSV dans ce fichier")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def ecrire_csv(chemin: Path, lignes: Sequence[dict[str, Any]]) -> None:
    """Écrit l'échéancier en CSV (séparateur ;)."""
    try:
        with chemin.open("w", encoding="utf-8", newline="") as sortie:
            ecrivain = csv.DictWriter(sortie, fieldnames=list(lignes[0]), delimiter=";")
            ecrivain.writeheader()
            ecrivain.writerows(lignes)
    except OSError as erreur:
        raise ErreurFinance(f"--sortie {chemin} : écriture impossible ({erreur.strerror})") from erreur


def afficher_flux(f: dict[str, Any]) -> None:
    """Résumé texte d'une analyse de flux."""
    cle = "xtri" if "xtri" in f else "tri"
    if "van" in f:
        print(f"VAN au taux {en_pourcent(f['taux'])} : {f['van']} (convention tableur, 1er flux actualisé : "
              f"{f['van_convention_tableur']})")
    if "xvan" in f:
        print(f"{INDICATEUR_XVAN} au taux {en_pourcent(f['taux'])} : {f['xvan']} ({f['convention']})")
    nom = INDICATEUR_XIRR if cle == "xtri" else "TRI"
    print(f"{nom} : {en_pourcent(f[cle])} [{f['methode']}] ; racines trouvées : "
          f"{', '.join(en_pourcent(r) for r in f[cle + '_tous']) or 'aucune'} ; changements de signe : "
          f"{f['changements_de_signe']}" + ("" if f["tri_unique"] else " — TRI NON UNIQUE OU ABSENT"))
    if f.get("tri_annualise"):
        print(f"TRI annualisé : {en_pourcent(f['tri_annualise'])}")


def afficher_emprunt(e: dict[str, Any], complet: bool) -> None:
    """Résumé texte d'un emprunt et de son échéancier."""
    print(f"Emprunt {e['capital']} sur {e['duree_mois']} mois, taux mensuel {en_pourcent(e['taux_mensuel'])} "
          f"({e['conversion']}) : mensualité {e['mensualite']} (exacte {e['mensualite_exacte']}), dernière "
          f"{e['derniere_echeance']} (ajustement {e['ajustement_final']}), intérêts {e['total_interets']}, "
          f"taux effectif annuel {en_pourcent(e['taux_effectif_annuel'])}")
    if e.get("taeg_approche"):
        print(f"Taux annuel effectif global approché (frais inclus) : {en_pourcent(e['taeg_approche'])}")
    lignes = e["echeancier"]
    montrees = lignes if complet or len(lignes) <= 6 else lignes[:3] + lignes[-3:]
    for l in montrees:
        print(f"  {l['numero']:>4}  échéance {l['echeance']:>12}  intérêts {l['interets']:>10}  "
              f"principal {l['principal']:>12}  restant {l['capital_restant']:>14}")


def afficher_humain(rapport: dict[str, Any], complet: bool) -> None:
    """Affichage lisible de toutes les parties du rapport."""
    if "flux" in rapport:
        afficher_flux(rapport["flux"])
    if "emprunt" in rapport:
        afficher_emprunt(rapport["emprunt"], complet)
    if "taux_effectif" in rapport:
        t = rapport["taux_effectif"]
        print(f"Taux nominal {en_pourcent(t['taux_nominal'])} capitalisé {t['capitalisations']} fois : "
              f"taux effectif {en_pourcent(t['taux_effectif'])}")


def defaut_trouve(rapport: dict[str, Any]) -> bool:
    """TRI absent ou non unique : décision ambiguë."""
    flux = rapport.get("flux")
    return flux is not None and not flux["tri_unique"]


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, calcule, publie."""
    args = construire_parseur().parse_args(argv)
    if numpy_financial is None:
        print("calculer_finance : numpy-financial absent — repli stdlib (decimal) seul, sans contre-vérification",
              file=sys.stderr)
    try:
        if args.racine is not None and not args.racine.is_dir():
            raise ErreurFinance(f"--racine {args.racine} : dossier introuvable")
        with localcontext() as contexte:
            contexte.prec = PRECISION
            rapport, examines = calculer(args)
        if args.sortie and "emprunt" in rapport:
            ecrire_csv(args.sortie, rapport["emprunt"]["echeancier"])
    except ErreurFinance as erreur:
        print(f"calculer_finance : {erreur}", file=sys.stderr)
        if erreur.code == CODE_RIEN:
            print("calculer_finance : dénominateur nul — rien à examiner", file=sys.stderr)
        return erreur.code
    if not examines:
        print("calculer_finance : dénominateur nul — ni flux, ni emprunt, ni taux nominal : rien à examiner",
              file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "calculer_finance", "denominateur": 0, "examines": []}, ensure_ascii=False))
        return CODE_RIEN
    if defaut_trouve(rapport):
        print("calculer_finance : TRI absent ou non unique — la décision par le TRI est ambiguë", file=sys.stderr)
    if args.json:
        print(json.dumps({"outil": "calculer_finance", "moteur": "numpy-financial" if numpy_financial else "stdlib",
                          "precision_decimale": PRECISION, "denominateur": len(examines),
                          "examines": examines[:EXAMINES_MAX], "examines_tronques": len(examines) > EXAMINES_MAX,
                          **rapport, "contrat": extraire_contrat(__doc__ or "")}, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport, args.echeancier_complet)
    return CODE_DEFAUT if defaut_trouve(rapport) else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
