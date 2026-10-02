"""Repérer les données personnelles d'un texte et le rendre masqué, sommes de contrôle à l'appui.

Un motif seul ne suffit pas : sur 10 000 suites de 16 chiffres tirées au hasard
(random.seed(0)), un motif « 16 chiffres » les prend toutes alors que 1 037 seulement
passent la clé de Luhn d'un vrai numéro de carte ; vérifier les clés (Luhn, mod 97, clé
du NIR) divise ici par près de dix les faux positifs avant tout masquage.

QUESTION
    Quelles données personnelles ce texte contient-il, et à quoi ressemble-t-il une
    fois masqué ?
MESURE
    Recherche par motifs puis validation de chaque candidat : courriels (syntaxe du
    local et du domaine) ; téléphones français (0X XX XX XX XX, +33, 0033) et
    internationaux au format E.164 (8 à 15 chiffres) ; IBAN (longueur du pays et clé
    mod 97) ; cartes bancaires (13 à 19 chiffres, préfixe de réseau et clé de Luhn) ;
    adresses IPv4 et IPv6 (module ipaddress ; boucle locale et adresse nulle exclues) ;
    NIR français (mois, département, Corse comprise, clé 97 - n mod 97) ; SIRET (Luhn sur
    14 chiffres et sur le SIREN inclus) et SIREN (Luhn, et groupement 3-3-3 ou mot
    clé proche). Les candidats rejetés par leur clé sont comptés à part. Les
    chevauchements sont tranchés par priorité (courriel, IBAN, carte, NIR, SIRET,
    SIREN, IPv6, IPv4, téléphone). Le texte masqué remplace chaque valeur par son
    étiquette ([COURRIEL], [IBAN]...) ; il figure dans le JSON et n'est écrit dans un
    fichier que si --sortie est donné. Avec phonenumbers installé, les téléphones sont
    trouvés et validés par cette bibliothèque (plans de numérotation réels).
HYPOTHÈSES
    Le texte est en utf-8 (repli latin-1 sans perte sinon) ; les numéros sont écrits
    d'un seul tenant ou avec des séparateurs usuels (espace, point, tiret) ; une
    valeur qui passe sa somme de contrôle est bien la donnée annoncée.
LIMITES
    Ne voit ni les noms, prénoms, adresses postales, dates de naissance, plaques
    d'immatriculation ni numéros de passeport (aucune clé à vérifier) ; un numéro
    coupé par un retour à la ligne échappe ; la clé de Luhn laisse passer un nombre
    sur dix au hasard, d'où l'exigence d'un groupement ou d'un mot clé pour le SIREN ;
    sans phonenumbers, un numéro international n'est contrôlé que par sa longueur. Un
    numéro de version à quatre composants (1.2.3.4) ressemble à une adresse IPv4 : il
    n'est écarté que s'il suit « v » ou « version ».
CONTRE-EXEMPLES
    Faux positif constaté : « commande n° 123 456 782 » est masquée comme SIREN, car
    123456782 passe Luhn et s'écrit en 3-3-3. Faux négatif constaté : le numéro
    américain « (415) 555-2671 », écrit sans indicatif +1, n'est pas reconnu par le
    moteur stdlib.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Journaux, exports, tickets, courriels et documents texte à anonymiser avant envoi
    à un tiers (prestataire, modèle de langage, ticket public), surtout pour les
    formats français et européens.
"""

from __future__ import annotations

import argparse
import bisect
import ipaddress
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import phonenumbers
except ImportError:
    phonenumbers = None

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_texte", "cle_luhn_valide", "iban_valide", "nir_valide", "masquer_texte", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES",
                  "CONTRE-EXEMPLES", TITRE_INVOCATION, "DOMAINE")

CAT_COURRIEL = "courriel"
CAT_TELEPHONE = "téléphone"
CAT_IBAN = "IBAN"
CAT_CARTE = "carte bancaire"
CAT_NIR = "NIR"
CAT_SIRET = "SIRET"
CAT_SIREN = "SIREN"
CAT_IPV4 = "IPv4"
CAT_IPV6 = "IPv6"
FORMAT_E164 = "E.164"
PRIORITES = (CAT_COURRIEL, CAT_IBAN, CAT_CARTE, CAT_NIR, CAT_SIRET, CAT_SIREN,
             CAT_IPV6, CAT_IPV4, CAT_TELEPHONE)
ETIQUETTES = ((CAT_COURRIEL, "[COURRIEL]"), (CAT_TELEPHONE, "[TELEPHONE]"),
              (CAT_IBAN, "[IBAN]"), (CAT_CARTE, "[CARTE]"), (CAT_NIR, "[NIR]"),
              (CAT_SIRET, "[SIRET]"), (CAT_SIREN, "[SIREN]"), (CAT_IPV4, "[IPV4]"),
              (CAT_IPV6, "[IPV6]"))

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3
APERCU_HUMAIN = 2000
TEXTE_JSON_MAX = 2_000_000

# Longueurs d'IBAN par pays (registre SWIFT).
LONGUEURS_IBAN = (
    ("AD", 24), ("AE", 23), ("AL", 28), ("AT", 20), ("AZ", 28), ("BA", 20), ("BE", 16),
    ("BG", 22), ("BH", 22), ("BR", 29), ("BY", 28), ("CH", 21), ("CR", 22), ("CY", 28),
    ("CZ", 24), ("DE", 22), ("DK", 18), ("DO", 28), ("EE", 20), ("EG", 29), ("ES", 24),
    ("FI", 18), ("FO", 18), ("FR", 27), ("GB", 22), ("GE", 22), ("GI", 23), ("GL", 18),
    ("GR", 27), ("GT", 28), ("HR", 21), ("HU", 28), ("IE", 22), ("IL", 23), ("IQ", 23),
    ("IS", 26), ("IT", 27), ("JO", 30), ("KW", 30), ("KZ", 20), ("LB", 28), ("LC", 32),
    ("LI", 21), ("LT", 20), ("LU", 20), ("LV", 21), ("MC", 27), ("MD", 24), ("ME", 22),
    ("MK", 19), ("MR", 27), ("MT", 31), ("MU", 30), ("NL", 18), ("NO", 15), ("PK", 24),
    ("PL", 28), ("PS", 29), ("PT", 25), ("QA", 29), ("RO", 24), ("RS", 22), ("SA", 24),
    ("SC", 31), ("SE", 24), ("SI", 19), ("SK", 24), ("SM", 27), ("ST", 25), ("SV", 28),
    ("TL", 23), ("TN", 24), ("TR", 26), ("UA", 29), ("VA", 22), ("VG", 24), ("XK", 20),
)
RESEAUX_CARTE = (
    ("American Express", re.compile(r"3[47]"), (15,)),
    ("Diners Club", re.compile(r"30[0-5]|3[689]"), (14, 15, 16, 17, 18, 19)),
    ("JCB", re.compile(r"35(?:2[89]|[3-8]\d)"), (16, 17, 18, 19)),
    ("Visa", re.compile(r"4"), (13, 16, 19)),
    ("Mastercard", re.compile(r"5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720)"), (16,)),
    ("Discover", re.compile(r"6(?:011|4[4-9]|5)"), (16, 17, 18, 19)),
    ("UnionPay", re.compile(r"62"), (16, 17, 18, 19)),
    ("Maestro", re.compile(r"5[06-8]|6"), (12, 13, 14, 15, 16, 17, 18, 19)),
)

MOTIF_COURRIEL = re.compile(
    r"(?<![\w.%+-])([\w.%+-]{1,64})@((?:[^\W_](?:[\w-]{0,61}[^\W_])?\.)+[^\W\d_]{2,63})(?![\w-])")
MOTIF_TEL_FR = re.compile(
    r"(?<![\w+])(?:(?:\+|00)33[\s.-]?(?:\(0\)[\s.-]?)?|0)[1-9](?:[\s.-]?\d{2}){4}(?![\d])")
MOTIF_TEL_INTL = re.compile(r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}(?:[\s.-]?\(?\d{1,5}\)?){1,6}(?![\d])")
MOTIF_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[  ]?[A-Za-z0-9]){11,30}")
MOTIF_CARTE = re.compile(r"(?<![\d])(?<!\d[ -])(?:\d[ -]?){11,18}\d(?![ -]?\d)")
MOTIF_NIR = re.compile(
    r"(?<![\w])([1-478])[ .]?(\d{2})[ .]?(\d{2})[ .]?(\d{2}|2[AaBb])[ .]?(\d{3})[ .]?(\d{3})[ .]?(\d{2})(?![\w])")
MOTIF_SIRET = re.compile(r"(?<![\d])(?<!\d[ .])\d{3}[ .]?\d{3}[ .]?\d{3}[ .]?\d{5}(?![ .]?\d)")
MOTIF_SIREN = re.compile(r"(?<![\d])(?<!\d[ .])\d{3}([ .]?)\d{3}\1\d{3}(?![ .]?\d)")
MOTIF_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
MOTIF_IPV6 = re.compile(
    r"(?<![\w:.])((?:[0-9A-Fa-f]{0,4}:){2,7}(?:[0-9A-Fa-f]{1,4}|(?:\d{1,3}\.){3}\d{1,3})?)(?![\w:]|\.\d)")
MOTIF_CONTEXTE_SIREN = re.compile(r"(?i)(siren|siret|rcs|sirene|immatricul)")
MOTIF_VERSION = re.compile(r"(?i)(?:\bv|version\s*:?\s*|ver\.\s*)$")


@dataclass(frozen=True)
class Candidat:
    """Une valeur repérée ; valide si sa vérification a réussi."""

    categorie: str
    debut: int
    fin: int
    valeur: str
    controle: str
    valide: bool


def cle_luhn_valide(chiffres: str) -> bool:
    """Clé de Luhn (cartes, SIREN, SIRET)."""
    total = 0
    for rang, c in enumerate(reversed(chiffres)):
        n = int(c)
        if rang % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def iban_valide(iban: str) -> bool:
    """Clé mod 97 (ISO 13616) : les 4 premiers caractères passent à la fin."""
    tourne = iban[4:] + iban[:4]
    if not tourne.isalnum():
        return False
    return int("".join(str(int(c, 36)) for c in tourne)) % 97 == 1


def nir_valide(sexe: str, annee: str, mois: str, dep: str, commune: str, ordre: str, cle: str) -> bool:
    """Clé du NIR : 97 - (n mod 97), la Corse (2A, 2B) devenant 19 et 18."""
    m = int(mois)
    if not (1 <= m <= 12 or 20 <= m <= 42 or 50 <= m <= 99):
        return False
    dep_num = {"2A": "19", "2B": "18"}.get(dep.upper(), dep)
    if not dep_num.isdigit():
        return False
    n = int(sexe + annee + mois + dep_num + commune + ordre)
    return 97 - n % 97 == int(cle)


def _chiffres(texte: str) -> str:
    """Seuls les chiffres d'une chaîne."""
    return "".join(c for c in texte if c.isdigit())


def _courriels(texte: str) -> Iterator[Candidat]:
    """Courriels : local sans point en tête, en fin ni doublé."""
    for m in MOTIF_COURRIEL.finditer(texte):
        local = m.group(1)
        ok = not (local.startswith(".") or local.endswith(".") or ".." in local)
        yield Candidat(CAT_COURRIEL, m.start(), m.end(), m.group(), "syntaxe", ok)


def _telephones_stdlib(texte: str) -> Iterator[Candidat]:
    """Téléphones français puis internationaux (longueur E.164 : 8 à 15 chiffres)."""
    for m in MOTIF_TEL_FR.finditer(texte):
        yield Candidat(CAT_TELEPHONE, m.start(), m.end(), m.group(), "plan français", True)
    for m in MOTIF_TEL_INTL.finditer(texte):
        brut = m.group()
        n = len(_chiffres(brut)) - (2 if brut.startswith("00") else 0)
        yield Candidat(CAT_TELEPHONE, m.start(), m.end(), brut, f"longueur {FORMAT_E164}", 8 <= n <= 15)


def _telephones_bibliotheque(texte: str, region: str) -> Iterator[Candidat]:
    """Téléphones trouvés et validés par phonenumbers (plans de numérotation réels)."""
    for m in phonenumbers.PhoneNumberMatcher(texte, region):
        yield Candidat(CAT_TELEPHONE, m.start, m.end, m.raw_string, "phonenumbers", True)


def _ibans(texte: str) -> Iterator[Candidat]:
    """IBAN : longueur imposée par le pays, puis clé mod 97 ; la recherche reprend après."""
    longueurs = dict(LONGUEURS_IBAN)
    pos = 0
    while m := MOTIF_IBAN.search(texte, pos):
        attendu = longueurs.get(m.group()[:2].upper())
        positions = [m.start() + i for i, c in enumerate(m.group()) if c.isalnum()]
        if attendu is None or len(positions) < attendu:
            pos = m.start() + 1
            continue
        fin = positions[attendu - 1] + 1
        brut = texte[m.start():fin]
        compact = "".join(c for c in brut if c.isalnum()).upper()
        colle = fin < len(texte) and texte[fin].isalnum()
        yield Candidat(CAT_IBAN, m.start(), fin, brut, "mod 97", not colle and iban_valide(compact))
        pos = fin


def reseau_carte(chiffres: str) -> str:
    """Nom du réseau dont le préfixe et la longueur conviennent, ou chaîne vide."""
    for nom, prefixe, longueurs in RESEAUX_CARTE:
        if prefixe.match(chiffres) and len(chiffres) in longueurs:
            return nom
    return ""


def _cartes(texte: str) -> Iterator[Candidat]:
    """Cartes : 13 à 19 chiffres, réseau plausible, clé de Luhn."""
    for m in MOTIF_CARTE.finditer(texte):
        chiffres = _chiffres(m.group())
        if not 13 <= len(chiffres) <= 19:
            continue
        reseau = reseau_carte(chiffres)
        ok = bool(reseau) and cle_luhn_valide(chiffres) and len(set(chiffres)) > 1
        yield Candidat(CAT_CARTE, m.start(), m.end(), m.group(), f"Luhn {reseau}".strip(), ok)


def _nirs(texte: str) -> Iterator[Candidat]:
    """NIR : mois et département plausibles, clé 97 - n mod 97."""
    for m in MOTIF_NIR.finditer(texte):
        yield Candidat(CAT_NIR, m.start(), m.end(), m.group(), "clé NIR", nir_valide(*m.groups()))


def _sirets(texte: str) -> Iterator[Candidat]:
    """SIRET : Luhn sur 14 chiffres et sur le SIREN inclus (La Poste : somme multiple de 5)."""
    for m in MOTIF_SIRET.finditer(texte):
        c = _chiffres(m.group())
        if c.startswith("356000000"):
            ok = sum(map(int, c)) % 5 == 0
        else:
            ok = cle_luhn_valide(c) and cle_luhn_valide(c[:9])
        yield Candidat(CAT_SIRET, m.start(), m.end(), m.group(), "Luhn", ok)


def _sirens(texte: str) -> Iterator[Candidat]:
    """SIREN : Luhn, et soit groupé 3-3-3, soit précédé d'un mot clé à moins de 40 caractères."""
    for m in MOTIF_SIREN.finditer(texte):
        contexte = MOTIF_CONTEXTE_SIREN.search(texte[max(0, m.start() - 40):m.start()])
        ok = cle_luhn_valide(_chiffres(m.group())) and (bool(m.group(1)) or contexte is not None)
        yield Candidat(CAT_SIREN, m.start(), m.end(), m.group(), "Luhn + groupement ou contexte", ok)


def _ip_personnelle(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Une adresse de boucle, nulle, multidiffusion ou réservée ne désigne personne."""
    return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved)


def _ipv4s(texte: str) -> Iterator[Candidat]:
    """IPv4 validées par ipaddress ; un numéro de version (v1.2.3.4) est écarté."""
    for m in MOTIF_IPV4.finditer(texte):
        if MOTIF_VERSION.search(texte[max(0, m.start() - 12):m.start()]):
            yield Candidat(CAT_IPV4, m.start(), m.end(), m.group(), "numéro de version", False)
            continue
        try:
            ok = _ip_personnelle(ipaddress.IPv4Address(m.group()))
        except ValueError:
            ok = False
        yield Candidat(CAT_IPV4, m.start(), m.end(), m.group(), "ipaddress", ok)


def _ipv6s(texte: str) -> Iterator[Candidat]:
    """IPv6 validées par ipaddress ; au moins 2 groupes et 4 chiffres hexadécimaux."""
    for m in MOTIF_IPV6.finditer(texte):
        brut = m.group(1)
        groupes = [g for g in brut.split(":") if g]
        hexa = sum(len(g) for g in groupes if "." not in g) + (4 if "." in brut else 0)
        try:
            ok = len(groupes) >= 2 and hexa >= 4 and _ip_personnelle(ipaddress.IPv6Address(brut))
        except ValueError:
            ok = False
        yield Candidat(CAT_IPV6, m.start(1), m.end(1), brut, "ipaddress", ok)


def _candidats(texte: str, region: str, avec_tiers: bool) -> Iterator[Candidat]:
    """Tous les candidats, validés ou non."""
    yield from _courriels(texte)
    yield from _ibans(texte)
    yield from _cartes(texte)
    yield from _nirs(texte)
    yield from _sirets(texte)
    yield from _sirens(texte)
    yield from _ipv6s(texte)
    yield from _ipv4s(texte)
    if avec_tiers:
        yield from _telephones_bibliotheque(texte, region)
    else:
        yield from _telephones_stdlib(texte)


def _retenir(valides: list[Candidat]) -> list[Candidat]:
    """Garde les candidats sans chevauchement, par priorité de catégorie puis longueur."""
    rang = {cat: i for i, cat in enumerate(PRIORITES)}
    ordre = sorted(valides, key=lambda c: (rang[c.categorie], c.debut - c.fin, c.debut))
    debuts: list[int] = []
    fins: list[int] = []
    retenus: list[Candidat] = []
    for c in ordre:
        i = bisect.bisect_left(debuts, c.debut)
        avant = i > 0 and fins[i - 1] > c.debut
        apres = i < len(debuts) and debuts[i] < c.fin
        if not (avant or apres):
            debuts.insert(i, c.debut)
            fins.insert(i, c.fin)
            retenus.append(c)
    return sorted(retenus, key=lambda c: c.debut)


def masquer_texte(texte: str, retenus: list[Candidat]) -> str:
    """Remplace chaque valeur retenue par son étiquette."""
    etiquettes = dict(ETIQUETTES)
    morceaux: list[str] = []
    curseur = 0
    for c in retenus:
        morceaux.append(texte[curseur:c.debut])
        morceaux.append(etiquettes[c.categorie])
        curseur = c.fin
    morceaux.append(texte[curseur:])
    return "".join(morceaux)


def _apercu(valeur: str) -> str:
    """Deux premiers et deux derniers caractères au plus, jamais la valeur entière."""
    return valeur[:2] + "…" + valeur[-2:] if len(valeur) >= 10 else valeur[:1] + "…"


def analyser_texte(texte: str, region: str = "FR", avec_tiers: bool = False) -> dict[str, object]:
    """Constats, rejets par somme de contrôle et texte masqué."""
    tous = list(_candidats(texte, region, avec_tiers))
    retenus = _retenir([c for c in tous if c.valide])
    debuts_lignes = [0] + [i + 1 for i, ch in enumerate(texte) if ch == "\n"]
    constats = []
    for c in retenus:
        ligne = bisect.bisect_right(debuts_lignes, c.debut)
        constats.append({"categorie": c.categorie, "ligne": ligne,
                         "colonne": c.debut - debuts_lignes[ligne - 1] + 1,
                         "longueur": c.fin - c.debut, "apercu": _apercu(c.valeur),
                         "controle": c.controle})
    rejetes = Counter(f"{c.categorie} ({c.controle})" for c in tous if not c.valide)
    return {"constats": constats,
            "par_categorie": dict(Counter(c.categorie for c in retenus)),
            "rejetes_par_controle": dict(sorted(rejetes.items())),
            "texte_masque": masquer_texte(texte, retenus),
            "lignes": len(debuts_lignes)}


def lire_texte(chemin: Path, taille_max: int) -> tuple[str, str]:
    """Rend (texte, encodage) ; lève ValueError pour un fichier binaire ou trop gros."""
    if chemin.stat().st_size > taille_max:
        raise ValueError(f"fichier de plus de {taille_max} octets (voir --taille-max)")
    brut = chemin.read_bytes()
    if b"\x00" in brut[:8192]:
        raise ValueError("fichier binaire (octet nul) : rien à masquer dans un texte")
    try:
        return brut.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return brut.decode("latin-1"), "latin-1 (repli)"


def ecrire_sortie(chemin: Path, texte: str, encodage: str) -> None:
    """Écrit le texte masqué dans l'encodage d'origine."""
    codec = "latin-1" if encodage.startswith("latin-1") else "utf-8"
    with chemin.open("w", encoding=codec, newline="") as flux:
        flux.write(texte)


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = ""
    for ligne in doc.splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne[:1].isspace():
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {titre: " ".join(l for l in lignes if l) for titre, lignes in sections.items()}


def afficher_json(rapport: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def afficher_humain(rapport: dict[str, object]) -> None:
    """Résumé, constats masqués, puis début du texte masqué."""
    print(f"{rapport['examines'][0]} — moteur {rapport['moteur']} — {rapport['verdict']} "
          f"({rapport['nombre_constats']} valeur(s) : {rapport['par_categorie']})")
    for c in rapport["constats"]:
        print(f"  l.{c['ligne']} c.{c['colonne']}  {c['categorie']:<15} {c['apercu']}  ({c['controle']})")
    if rapport["rejetes_par_controle"]:
        print(f"  candidats rejetés par leur contrôle : {rapport['rejetes_par_controle']}")
    if rapport["sortie"]:
        print(f"  texte masqué écrit dans {rapport['sortie']}")
    texte = str(rapport["texte_masque"])
    print("--- texte masqué ---")
    print(texte[:APERCU_HUMAIN] + ("\n[… tronqué ; --sortie pour le texte entier]"
                                   if len(texte) > APERCU_HUMAIN else ""))


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description="Repère les données personnelles d'un texte (courriels, téléphones, IBAN, "
                    "cartes, IP, NIR, SIREN/SIRET), validées par somme de contrôle, et rend le "
                    "texte masqué.",
        epilog=f"Exemple : python {RACINE.name}/masquer_donnees_personnelles.py journal.txt "
               "--sortie journal_masque.txt --json   (code 0 : rien ; 1 : données trouvées ; "
               "2 : entrée invalide ; 3 : texte vide)")
    p.add_argument("fichier", nargs="?", type=Path, help="fichier texte à examiner")
    p.add_argument("--texte", help="texte à examiner, donné en ligne (au lieu d'un fichier)")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base des chemins relatifs (défaut : dossier courant)")
    p.add_argument("--sortie", type=Path, default=None,
                   help="écrire le texte masqué dans ce fichier (rien n'est écrit sans elle)")
    p.add_argument("--ecraser", action="store_true", help="autoriser --sortie à remplacer un fichier")
    p.add_argument("--region", default="FR", help="région par défaut des numéros (phonenumbers)")
    p.add_argument("--taille-max", type=float, default=20.0, help="taille maximale lue, en Mo")
    p.add_argument("--stdlib", action="store_true",
                   help="ignorer phonenumbers même s'il est installé")
    return p


def _resoudre(chemin: Path | None, base: Path) -> Path | None:
    """Chemin absolu par rapport à la base."""
    if chemin is None:
        return None
    return chemin if chemin.is_absolute() else base / chemin


def _verifier_usage(o: argparse.Namespace) -> str:
    """Rend un message d'erreur d'usage, ou chaîne vide."""
    if (o.fichier is None) == (o.texte is None):
        return "donner soit un fichier, soit --texte (exactement l'un des deux)"
    if not o.base.is_dir():
        return f"--racine n'est pas un dossier : {o.base}"
    if o.fichier is not None and not o.fichier.exists():
        return f"fichier introuvable : {o.fichier}"
    if o.fichier is not None and not o.fichier.is_file():
        return f"un fichier texte est attendu, pas un dossier : {o.fichier}"
    return _verifier_sortie(o)


def _verifier_sortie(o: argparse.Namespace) -> str:
    """La sortie ne doit ni écraser l'entrée, ni écraser un fichier sans --ecraser."""
    if o.sortie is None:
        return ""
    if o.fichier is not None and o.sortie.resolve() == o.fichier.resolve():
        return "--sortie désigne le fichier d'entrée : refus de l'écraser"
    if o.sortie.exists() and not o.ecraser:
        return f"--sortie existe déjà ({o.sortie}) : ajouter --ecraser pour le remplacer"
    if not o.sortie.parent.is_dir():
        return f"dossier de --sortie introuvable : {o.sortie.parent}"
    return ""


def _charger(o: argparse.Namespace) -> tuple[str, str, str]:
    """Rend (texte, encodage, nom examiné)."""
    if o.texte is not None:
        return o.texte, "utf-8", "<texte>"
    texte, encodage = lire_texte(o.fichier, int(o.taille_max * 1_000_000))
    try:
        return texte, encodage, str(o.fichier.relative_to(o.base))
    except ValueError:
        return texte, encodage, str(o.fichier)


def _rapport(o: argparse.Namespace, texte: str, encodage: str,
             nom: str) -> tuple[dict[str, object], str]:
    """Assemble le rapport ; rend aussi le texte masqué complet (le JSON peut le tronquer)."""
    tiers = phonenumbers is not None and not o.stdlib
    analyse = analyser_texte(texte, o.region, tiers)
    masque = str(analyse["texte_masque"])
    rapport: dict[str, object] = {
        "denominateur": 1 if texte else 0,
        "examines": [nom],
        "moteur": "phonenumbers" if tiers else "stdlib",
        "verdict": "DONNÉES PERSONNELLES" if analyse["constats"] else "AUCUNE DONNÉE PERSONNELLE",
        "nombre_constats": len(analyse["constats"]),
        "caracteres": len(texte),
        "encodage": encodage,
        **analyse,
        "texte_masque": masque[:TEXTE_JSON_MAX],
        "texte_masque_tronque": len(masque) > TEXTE_JSON_MAX,
        "sortie": None,
    }
    return rapport, masque


def _ecrire_si_demande(o: argparse.Namespace, rapport: dict[str, object], masque: str,
                       encodage: str) -> str:
    """Écrit le texte masqué si --sortie est donné ; rend un message d'erreur ou ''."""
    if o.sortie is None or not masque:
        return ""
    try:
        ecrire_sortie(o.sortie, masque, encodage)
    except OSError as exc:
        return f"écriture impossible : {exc}"
    rapport["sortie"] = str(o.sortie)
    return ""


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : 0 (rien), 1 (données trouvées), 2 (usage), 3 (texte vide)."""
    o = _parseur().parse_args(argv)
    o.base = (o.racine if o.racine is not None else Path.cwd()).resolve()
    o.fichier = _resoudre(o.fichier, o.base)
    o.sortie = _resoudre(o.sortie, o.base)
    erreur = _verifier_usage(o)
    try:
        texte, encodage, nom = ("", "", "") if erreur else _charger(o)
    except (OSError, ValueError) as exc:
        erreur = f"{o.fichier} : {exc}"
    if erreur:
        print(f"masquer_donnees_personnelles : {erreur}", file=sys.stderr)
        return CODE_USAGE
    if phonenumbers is None and not o.stdlib:
        print("masquer_donnees_personnelles : phonenumbers absent — mode dégradé stdlib "
              "(téléphones par motif français et longueur E.164).", file=sys.stderr)
    rapport, masque = _rapport(o, texte, encodage, nom)
    if erreur := _ecrire_si_demande(o, rapport, masque, encodage):
        print(f"masquer_donnees_personnelles : {erreur}", file=sys.stderr)
        return CODE_USAGE
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    if not texte:
        print("masquer_donnees_personnelles : dénominateur nul — rien à examiner (texte vide).",
              file=sys.stderr)
    if o.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if not texte:
        return CODE_VIDE
    return CODE_TROUVE if rapport["nombre_constats"] else CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
