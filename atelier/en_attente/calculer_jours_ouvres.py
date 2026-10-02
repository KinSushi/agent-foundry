r"""Compter « du lundi au vendredi » ne suffit pas : en 2026, la France compte
261 jours du lundi au vendredi mais 252 jours ouvrés, neuf fériés tombant en
semaine (`calculer_jours_ouvres.py 2026-01-01 2026-12-31 --pays FR`). Règles
confrontées à holidays 0.105 (`--moteur stdlib --comparer`) : 0 écart pour FR,
FR-57, US (1990-2050) et DE (1991-2050) ; Pâques identique à dateutil sur 1583-4099.

QUESTION
    Combien de jours ouvrés entre deux dates (bornes incluses), ou quelle date
    tombe N jours ouvrés après (ou avant) une date donnée ?
MESURE
    Parcours jour par jour du calendrier grégorien ; un jour est ouvré s'il
    n'est ni un jour de week-end (paramétrable) ni un férié. Les fériés sont
    calculés par règles : dates fixes, Pâques (algorithme de Meeus/Jones/
    Butcher), n-ième jour de la semaine du mois (module calendar), report
    fédéral américain samedi→vendredi et dimanche→lundi. Avec la bibliothèque
    holidays, elle devient le moteur et les deux jeux de fériés sont comparés
    année par année.
HYPOTHÈSES
    Fériés légaux nationaux (FR métropole, Alsace-Moselle (FR-57, FR-67, FR-68), BE, DE
    au niveau fédéral, US au niveau fédéral) ; le calendrier de l'entreprise
    n'ajoute que les dates passées par --ferie ou --fichier-feries.
LIMITES
    Pas de fériés régionaux (Länder, États américains, cantons) sans holidays ;
    pas de ponts. Le lundi de Pentecôte est compté férié sauf 2005-2007
    (journée de solidarité) ; depuis 2008 une entreprise peut encore le faire
    travailler. Règles vérifiées sur 1990-2050 seulement ; hors de cette plage
    l'outil avertit. Avant 1583 (pas de Pâques grégorien), refus.
CONTRE-EXEMPLES
    Belgique : holidays 0.105 range aussi les dimanches de Pâques et de
    Pentecôte parmi les fériés, pas les règles intégrées : 122 écarts sur
    1990-2050, tous « sans effet » sur le décompte (le jour est déjà chômé). Une entreprise
    française qui travaille le lundi de Pentecôte obtient un jour de moins
    que la réalité si elle ne le précise pas.
INVOCATION
    {outil} 2026-01-01 2026-12-31 --pays FR --json
    {outil} 2026-05-01 --plus 10 --pays FR --json
DOMAINE
    Délais contractuels, plannings, SLA exprimés en jours ouvrés, entre 1990
    et 2050, pour les pays listés ou ceux que holidays connaît.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import holidays as bibliotheque_feries
except ImportError:
    bibliotheque_feries = None

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
TITRES_CONTRAT = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
                  TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)

LIMITE_EXAMINES = 40
LIMITE_FERIES = 500
LIMITE_JOURS = 1_000_000
ANNEES_VERIFIEES = (1990, 2050)
DEBUT_VERIFIE_PAR_PAYS = MappingProxyType({"DE": 1991})
PREMIERE_ANNEE_PAQUES = 1583

JOURS_SEMAINE = MappingProxyType({
    "lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
})
NOMS_JOURS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")

# Règle : (nom, genre, paramètres, première année, dernière année)
_FR = (
    ("Jour de l'an", "fixe", (1, 1), None, None),
    ("Lundi de Pâques", "paques", (1,), None, None),
    ("Fête du Travail", "fixe", (5, 1), None, None),
    ("Fête de la Victoire", "fixe", (5, 8), 1953, 1959),
    ("Fête de la Victoire", "fixe", (5, 8), 1982, None),
    ("Ascension", "paques", (39,), None, None),
    ("Lundi de Pentecôte", "paques", (50,), None, 2004),
    ("Lundi de Pentecôte", "paques", (50,), 2008, None),
    ("Fête nationale", "fixe", (7, 14), None, None),
    ("Assomption", "fixe", (8, 15), None, None),
    ("Toussaint", "fixe", (11, 1), None, None),
    ("Armistice", "fixe", (11, 11), None, None),
    ("Noël", "fixe", (12, 25), None, None),
)
_ALSACE_MOSELLE = (
    ("Vendredi saint", "paques", (-2,), None, None),
    ("Saint-Étienne", "fixe", (12, 26), None, None),
)
_BE = (
    ("Nouvel an", "fixe", (1, 1), None, None),
    ("Lundi de Pâques", "paques", (1,), None, None),
    ("Fête du Travail", "fixe", (5, 1), None, None),
    ("Ascension", "paques", (39,), None, None),
    ("Lundi de Pentecôte", "paques", (50,), None, None),
    ("Fête nationale", "fixe", (7, 21), None, None),
    ("Assomption", "fixe", (8, 15), None, None),
    ("Toussaint", "fixe", (11, 1), None, None),
    ("Armistice", "fixe", (11, 11), None, None),
    ("Noël", "fixe", (12, 25), None, None),
)
_DE = (
    ("Neujahr", "fixe", (1, 1), 1991, None),
    ("Karfreitag", "paques", (-2,), 1991, None),
    ("Ostermontag", "paques", (1,), 1991, None),
    ("Erster Mai", "fixe", (5, 1), 1991, None),
    ("Christi Himmelfahrt", "paques", (39,), 1991, None),
    ("Pfingstmontag", "paques", (50,), 1991, None),
    ("Tag der Deutschen Einheit", "fixe", (10, 3), 1991, None),
    ("Reformationstag", "fixe", (10, 31), 2017, 2017),
    ("Buß- und Bettag", "mercredi_avant", (11, 23), 1991, 1994),
    ("Erster Weihnachtstag", "fixe", (12, 25), 1991, None),
    ("Zweiter Weihnachtstag", "fixe", (12, 26), 1991, None),
)
_US = (
    ("New Year's Day", "fixe_us", (1, 1), None, None),
    ("Martin Luther King Jr. Day", "nieme", (1, 0, 3), 1986, None),
    ("Washington's Birthday", "nieme", (2, 0, 3), 1971, None),
    ("Memorial Day", "nieme", (5, 0, -1), 1971, None),
    ("Juneteenth National Independence Day", "fixe_us", (6, 19), 2021, None),
    ("Independence Day", "fixe_us", (7, 4), None, None),
    ("Labor Day", "nieme", (9, 0, 1), None, None),
    ("Columbus Day", "nieme", (10, 0, 2), 1971, None),
    ("Veterans Day", "fixe_us", (11, 11), 1978, None),
    ("Thanksgiving Day", "nieme", (11, 3, 4), None, None),
    ("Christmas Day", "fixe_us", (12, 25), None, None),
)
PAYS_MOSELLE = "FR-57"
PAYS_BAS_RHIN = "FR-67"
PAYS_HAUT_RHIN = "FR-68"
REGLES_PAYS = MappingProxyType({
    "FR": _FR, PAYS_MOSELLE: _FR + _ALSACE_MOSELLE, PAYS_BAS_RHIN: _FR + _ALSACE_MOSELLE,
    PAYS_HAUT_RHIN: _FR + _ALSACE_MOSELLE, "BE": _BE, "DE": _DE, "US": _US,
})
CORRESPONDANCE_BIBLIOTHEQUE = MappingProxyType({
    "FR": ("FR", None), PAYS_MOSELLE: ("FR", "57"), PAYS_BAS_RHIN: ("FR", "6AE"),
    PAYS_HAUT_RHIN: ("FR", "6AE"), "BE": ("BE", None), "DE": ("DE", None), "US": ("US", None),
})


class ErreurUsage(Exception):
    """Entrée invalide : l'outil rend le code 2."""


def lire_contrat() -> dict[str, str]:
    """Découpe la docstring du module en sections du contrat de mesure."""
    sections: dict[str, list[str]] = {}
    courant = ""
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in val if m) for cle, val in sections.items()}


def paques(annee: int) -> date:
    """Dimanche de Pâques grégorien (algorithme de Meeus/Jones/Butcher)."""
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois, jour = divmod(h + l - 7 * m + 114, 31)
    return date(annee, mois, jour + 1)


def nieme_jour_du_mois(annee: int, mois: int, jour_semaine: int, rang: int) -> date:
    """n-ième (rang ≥ 1) ou dernier (rang = -1) jour_semaine du mois."""
    semaines = calendar.monthcalendar(annee, mois)
    jours = [s[jour_semaine] for s in semaines if s[jour_semaine]]
    return date(annee, mois, jours[rang - 1] if rang > 0 else jours[rang])


def reporter_us(jour: date) -> date:
    """Report fédéral américain : samedi → vendredi, dimanche → lundi."""
    if jour.weekday() == 5:
        return jour - timedelta(days=1)
    if jour.weekday() == 6:
        return jour + timedelta(days=1)
    return jour


def appliquer_regle(regle: tuple, annee: int) -> list[tuple[date, str]]:
    """Dates produites par une règle pour une année donnée."""
    nom, genre, params, debut, fin = regle
    if (debut is not None and annee < debut) or (fin is not None and annee > fin):
        return []
    if genre == "fixe":
        return [(date(annee, *params), nom)]
    if genre == "paques":
        return [(paques(annee) + timedelta(days=params[0]), nom)]
    if genre == "nieme":
        return [(nieme_jour_du_mois(annee, *params), nom)]
    if genre == "mercredi_avant":
        borne = date(annee, *params) - timedelta(days=1)
        return [(borne - timedelta(days=(borne.weekday() - 2) % 7), nom)]
    jour = date(annee, *params)
    report = reporter_us(jour)
    if report == jour:
        return [(jour, nom)]
    return [(jour, nom), (report, f"{nom} (observed)")]


def feries_stdlib(pays: str, annee: int) -> dict[date, str]:
    """Fériés calculés par règles pour l'année, plus les reports débordant
    depuis l'année suivante (US : 1er janvier un samedi → 31 décembre)."""
    resultat: dict[date, str] = {}
    for an in (annee, annee + 1):
        if an < PREMIERE_ANNEE_PAQUES or an > 9999:
            continue
        for regle in REGLES_PAYS[pays]:
            for jour, nom in appliquer_regle(regle, an):
                if jour.year == annee:
                    resultat[jour] = f"{resultat[jour]}; {nom}" if jour in resultat else nom
    return resultat


def code_bibliotheque(pays: str) -> tuple[str, str | None]:
    """Code pays et subdivision pour holidays (« DE-BY » → (« DE », « BY »))."""
    if pays in CORRESPONDANCE_BIBLIOTHEQUE:
        return CORRESPONDANCE_BIBLIOTHEQUE[pays]
    code, _, subdiv = pays.partition("-")
    return code, subdiv or None


def feries_bibliotheque(pays: str, annee: int) -> dict[date, str]:
    """Fériés selon la bibliothèque holidays (code pays et subdivision)."""
    code, subdiv = code_bibliotheque(pays)
    table = bibliotheque_feries.country_holidays(code, subdiv=subdiv, years=[annee])
    return {jour: str(nom) for jour, nom in table.items() if jour.year == annee}


class Calendrier:
    """Calendrier ouvré : week-end, fériés (mis en cache par année), ajouts."""

    def __init__(self, pays: str, weekend: frozenset[int], ajouts: dict[date, str],
                 source: Callable[[str, int], dict[date, str]] | None) -> None:
        self.pays = pays
        self.weekend = weekend
        self.ajouts = ajouts
        self.source = source
        self.cache: dict[int, dict[date, str]] = {}

    def ferie(self, jour: date) -> str | None:
        """Nom du férié (ou de la fermeture ajoutée) ce jour-là, sinon None."""
        if jour in self.ajouts:
            return self.ajouts[jour]
        if self.source is None:
            return None
        if jour.year not in self.cache:
            self.cache[jour.year] = self.source(self.pays, jour.year)
        return self.cache[jour.year].get(jour)

    def est_ouvre(self, jour: date) -> bool:
        """Vrai si le jour n'est ni de week-end ni férié."""
        return jour.weekday() not in self.weekend and self.ferie(jour) is None


def lire_date(texte: str) -> date:
    """Date ISO (AAAA-MM-JJ ou AAAAMMJJ) ou « aujourdhui »."""
    if texte.lower() in ("aujourdhui", "aujourd'hui", "today"):
        return date.today()
    try:
        return date.fromisoformat(texte)
    except ValueError as exc:
        raise ErreurUsage(f"date invalide « {texte} » : attendu AAAA-MM-JJ") from exc


def lire_weekend(texte: str) -> frozenset[int]:
    """Jours de week-end : « sam,dim », « ven,sam », « 6,7 » ou « aucun »."""
    if texte.strip().lower() in ("aucun", "none", ""):
        return frozenset()
    jours = set()
    for morceau in texte.lower().split(","):
        cle = morceau.strip()[:3]
        if cle not in JOURS_SEMAINE:
            raise ErreurUsage(f"jour de week-end inconnu « {morceau} » (lun…dim, mon…sun, 1…7)")
        jours.add(JOURS_SEMAINE[cle])
    if len(jours) == 7:
        raise ErreurUsage("les sept jours sont en week-end : aucun jour ne peut être ouvré")
    return frozenset(jours)


def lire_fichier_feries(chemin: Path) -> dict[date, str]:
    """Fichier texte : une date AAAA-MM-JJ par ligne, suivie d'un libellé facultatif."""
    if not chemin.is_file():
        raise ErreurUsage(f"fichier de fériés introuvable ou n'est pas un fichier : {chemin}")
    try:
        lignes = chemin.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ErreurUsage(f"fichier de fériés illisible ({chemin}) : {exc}") from exc
    ajouts: dict[date, str] = {}
    for numero, ligne in enumerate(lignes, 1):
        propre = ligne.split("#", 1)[0].strip()
        if not propre:
            continue
        morceaux = propre.split(maxsplit=1)
        try:
            jour = date.fromisoformat(morceaux[0])
        except ValueError as exc:
            raise ErreurUsage(f"{chemin}:{numero} : date invalide « {morceaux[0]} »") from exc
        ajouts[jour] = morceaux[1] if len(morceaux) > 1 else "fermeture (fichier)"
    return ajouts


def choisir_source(pays: str, moteur: str) -> tuple[Callable[[str, int], dict[date, str]] | None, str]:
    """Choisit la source des fériés et le nom du moteur effectif."""
    if pays == "AUCUN":
        return None, "aucun férié"
    connu_stdlib = pays in REGLES_PAYS
    if moteur == "holidays" and bibliotheque_feries is None:
        raise ErreurUsage("--moteur holidays demandé mais la bibliothèque holidays est absente")
    if moteur != "stdlib" and bibliotheque_feries is not None:
        code = code_bibliotheque(pays)[0]
        if code in bibliotheque_feries.list_supported_countries():
            return feries_bibliotheque, "holidays"
    if connu_stdlib:
        return feries_stdlib, "stdlib"
    pays_connus = ", ".join(sorted(REGLES_PAYS))
    raise ErreurUsage(f"pays « {pays} » inconnu des règles intégrées ({pays_connus}) ; "
                      "installer holidays pour les autres pays")


def compter_intervalle(cal: Calendrier, debut: date, fin: date) -> dict[str, Any]:
    """Décompte bornes incluses ; si fin < debut, le résultat est négatif."""
    signe = 1 if fin >= debut else -1
    bas, haut = min(debut, fin), max(debut, fin)
    if (haut - bas).days + 1 > LIMITE_JOURS:
        raise ErreurUsage(f"intervalle de plus de {LIMITE_JOURS} jours : refusé")
    jours = [bas + timedelta(days=i) for i in range((haut - bas).days + 1)]
    ouvres = sum(1 for j in jours if cal.est_ouvre(j))
    weekend = sum(1 for j in jours if j.weekday() in cal.weekend)
    return {"mode": "intervalle", "debut": debut.isoformat(), "fin": fin.isoformat(),
            "bornes": "incluses", "jours_calendaires": len(jours) * signe,
            "jours_ouvres": ouvres * signe, "jours_weekend": weekend * signe,
            "parcourus": jours}


def avancer(cal: Calendrier, debut: date, nombre: int) -> dict[str, Any]:
    """Date atteinte après |nombre| jours ouvrés (le jour de départ n'est pas compté)."""
    if abs(nombre) > LIMITE_JOURS:
        raise ErreurUsage(f"--plus au-delà de {LIMITE_JOURS} : refusé")
    pas = timedelta(days=1 if nombre >= 0 else -1)
    jour, restant, parcourus = debut, abs(nombre), [debut] if nombre == 0 else []
    try:
        while restant:
            jour += pas
            parcourus.append(jour)
            restant -= cal.est_ouvre(jour)
            if len(parcourus) > 2 * LIMITE_JOURS:
                raise ErreurUsage("aucun jour ouvré trouvé dans la limite de parcours")
    except OverflowError as exc:
        raise ErreurUsage("le calcul sort du calendrier (années 1 à 9999)") from exc
    return {"mode": "decalage", "debut": debut.isoformat(), "jours_ouvres_demandes": nombre,
            "resultat": jour.isoformat(), "resultat_jour": NOMS_JOURS[jour.weekday()],
            "depart_ouvre": cal.est_ouvre(debut), "jours_calendaires": len(parcourus),
            "parcourus": parcourus}


def lister_feries(cal: Calendrier, jours: Iterable[date]) -> list[dict[str, Any]]:
    """Fériés et fermetures rencontrés, avec leur effet sur le décompte."""
    rencontres: list[dict[str, Any]] = []
    for jour in jours:
        nom = cal.ferie(jour)
        if nom is not None:
            rencontres.append({"date": jour.isoformat(), "jour": NOMS_JOURS[jour.weekday()],
                               "nom": nom, "retire_un_jour_ouvre": jour.weekday() not in cal.weekend})
    return rencontres


def comparer_moteurs(pays: str, annees: Iterable[int], weekend: frozenset[int]) -> dict[str, Any]:
    """Compare fériés calculés et fériés de holidays, année par année."""
    ecarts = []
    examinees = []
    for annee in annees:
        examinees.append(annee)
        a, b = feries_stdlib(pays, annee), feries_bibliotheque(pays, annee)
        for jour in sorted(set(a) ^ set(b)):
            ecarts.append({"date": jour.isoformat(), "stdlib": a.get(jour), "holidays": b.get(jour),
                           "effet_sur_decompte": jour.weekday() not in weekend})
    utiles = [e for e in ecarts if e["effet_sur_decompte"]]
    return {"bibliotheque": f"holidays {bibliotheque_feries.__version__}",
            "annees": [min(examinees), max(examinees)] if examinees else [],
            "ecarts": ecarts[:200], "ecarts_total": len(ecarts),
            "ecarts_qui_changent_le_decompte": len(utiles)}


def annees_couvertes(resultat: dict[str, Any]) -> range:
    """Années touchées par le calcul."""
    parcourus = resultat["parcourus"]
    return range(min(parcourus).year, max(parcourus).year + 1)


def construire_calendrier(args: argparse.Namespace, racine: Path) -> tuple[Calendrier, str]:
    """Assemble le calendrier ouvré à partir des options."""
    pays = args.pays.upper()
    source, moteur = choisir_source(pays, args.moteur)
    ajouts: dict[date, str] = {}
    if args.fichier_feries:
        chemin = Path(args.fichier_feries)
        ajouts.update(lire_fichier_feries(chemin if chemin.is_absolute() else racine / chemin))
    for texte in args.ferie or []:
        ajouts[lire_date(texte)] = "fermeture (--ferie)"
    return Calendrier(pays, lire_weekend(args.weekend), ajouts, source), moteur


def avertissements_domaine(cal: Calendrier, annees: range) -> list[str]:
    """Avertit quand le calcul sort de la plage de règles vérifiées."""
    messages = []
    bas = DEBUT_VERIFIE_PAR_PAYS.get(cal.pays, ANNEES_VERIFIEES[0])
    haut = ANNEES_VERIFIEES[1]
    if cal.source is not None and (annees.start < bas or annees.stop - 1 > haut):
        messages.append(f"années {annees.start}-{annees.stop - 1} hors de la plage vérifiée "
                        f"{bas}-{haut} : règles de fériés non confrontées")
    if cal.pays.startswith("FR") and cal.source is not None:
        messages.append("lundi de Pentecôte compté férié : vérifier s'il est la journée de "
                        "solidarité travaillée de l'entreprise")
    return messages


def calculer(args: argparse.Namespace, racine: Path) -> dict[str, Any]:
    """Cœur : calcule, liste les fériés, compare les moteurs si possible."""
    cal, moteur = construire_calendrier(args, racine)
    debut = lire_date(args.debut)
    if args.plus is not None and args.fin is not None:
        raise ErreurUsage("donner soit une date de fin, soit --plus N, pas les deux")
    if args.plus is None and args.fin is None:
        raise ErreurUsage("donner une date de fin ou --plus N")
    if args.plus is not None:
        resultat = avancer(cal, debut, args.plus)
    else:
        resultat = compter_intervalle(cal, debut, lire_date(args.fin))
    annees = annees_couvertes(resultat)
    if cal.source is not None and annees.start < PREMIERE_ANNEE_PAQUES:
        raise ErreurUsage(f"année {annees.start} avant {PREMIERE_ANNEE_PAQUES} : fériés mobiles "
                          "non calculables (utiliser --pays AUCUN pour ne compter que le week-end)")
    comparaison = None
    peut_comparer = bibliotheque_feries is not None and cal.pays in REGLES_PAYS
    if peut_comparer and (args.comparer or moteur == "holidays"):
        premiere = DEBUT_VERIFIE_PAR_PAYS.get(cal.pays, ANNEES_VERIFIEES[0])
        plage = range(premiere, ANNEES_VERIFIEES[1] + 1) if args.comparer else annees
        comparaison = comparer_moteurs(cal.pays, plage, cal.weekend)
    parcourus = resultat.pop("parcourus")
    feries = lister_feries(cal, parcourus)
    resultat.update({
        "pays": cal.pays, "moteur": moteur,
        "weekend": [NOMS_JOURS[j] for j in sorted(cal.weekend)],
        "feries_rencontres_total": len(feries),
        "feries_rencontres": feries[:LIMITE_FERIES],
        "comparaison": comparaison,
        "avertissements": avertissements_domaine(cal, annees),
    })
    return {"denominateur": len(parcourus),
            "examines": [j.isoformat() for j in parcourus[:LIMITE_EXAMINES]],
            "examines_tronques": len(parcourus) > LIMITE_EXAMINES, **resultat}


def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible par un humain."""
    if res["mode"] == "intervalle":
        print(f"{res['debut']} → {res['fin']} (bornes incluses) : {res['jours_ouvres']} jours ouvrés "
              f"sur {res['jours_calendaires']} jours calendaires ({res['pays']}, moteur {res['moteur']})")
    else:
        print(f"{res['debut']} + {res['jours_ouvres_demandes']} jours ouvrés = {res['resultat']} "
              f"({res['resultat_jour']}) — {res['pays']}, moteur {res['moteur']}")
    for ferie in res["feries_rencontres"]:
        effet = "retire un jour ouvré" if ferie["retire_un_jour_ouvre"] else "tombe un jour de week-end"
        print(f"  férié {ferie['date']} ({ferie['jour']}) {ferie['nom']} — {effet}")
    comparaison = res["comparaison"]
    if comparaison:
        print(f"comparaison {comparaison['bibliotheque']} sur {comparaison['annees']} : "
              f"{comparaison['ecarts_total']} écart(s), dont {comparaison['ecarts_qui_changent_le_decompte']} "
              "qui change(nt) le décompte")
        for ecart in comparaison["ecarts"][:20]:
            print(f"  {ecart['date']} stdlib={ecart['stdlib']} holidays={ecart['holidays']}")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Compte les jours ouvrés entre deux dates (bornes incluses) ou donne la date "
                    "atteinte après N jours ouvrés, fériés nationaux compris.",
        epilog="Exemples : calculer_jours_ouvres.py 2026-01-01 2026-12-31 --pays FR --json\n"
               "           calculer_jours_ouvres.py 2026-05-01 --plus 10 --pays FR\n"
               "Codes : 0 calcul fait ; 1 écart entre moteurs qui change le décompte ; "
               "2 entrée invalide ; 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("debut", help="date de départ AAAA-MM-JJ (ou « aujourdhui »)")
    parseur.add_argument("fin", nargs="?", help="date de fin AAAA-MM-JJ (incluse)")
    parseur.add_argument("--plus", type=int, help="nombre de jours ouvrés à ajouter (négatif : retrancher)")
    parseur.add_argument("--pays", default="FR",
                         help="FR, FR-57, FR-67, FR-68, BE, DE, US, AUCUN, ou tout code connu de holidays")
    parseur.add_argument("--weekend", default="sam,dim", help="jours chômés chaque semaine (défaut sam,dim)")
    parseur.add_argument("--ferie", action="append", metavar="DATE", help="fermeture supplémentaire (répétable)")
    parseur.add_argument("--fichier-feries", metavar="FICHIER", help="fichier de fermetures, une date par ligne")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "holidays"), default="auto",
                         help="source des fériés (auto : holidays si installée)")
    parseur.add_argument("--comparer", action="store_true",
                         help=f"comparer règles intégrées et holidays sur {ANNEES_VERIFIEES[0]}-{ANNEES_VERIFIEES[1]}")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    if bibliotheque_feries is None:
        print("holidays absente : fériés calculés par les règles intégrées (stdlib)", file=sys.stderr)
    racine = args.racine if args.racine is not None else Path.cwd()
    try:
        res = calculer(args, racine)
    except ErreurUsage as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    except (NotImplementedError, KeyError, ValueError) as exc:
        print(f"erreur : pays ou subdivision refusé par holidays : {exc}", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : rien à examiner", file=sys.stderr)
        return 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        imprimer_humain(res)
    for message in res["avertissements"]:
        print(f"avertissement : {message}", file=sys.stderr)
    comparaison = res["comparaison"]
    return 1 if comparaison and comparaison["ecarts_qui_changent_le_decompte"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
