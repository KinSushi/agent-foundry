"""Analyse des plages d'adresses IP (CIDR v4/v6) : contenu, classe, chevauchements, agrégation.

Pourquoi : la bibliothèque standard classe mal les blocs spéciaux. Mesuré le 2026-10-02
sur CPython 3.14.7 (``ipaddress.ip_network(bloc).is_private`` pour chaque bloc IPv4 de la
table de cet outil) : sur ces 19 blocs, ``is_private`` en déclare 14 privés alors
que 3 seulement relèvent de la RFC 1918, et 100.64.0.0/10 n'est ni privé ni global.

QUESTION
    Ces plages d'adresses se chevauchent-elles, et que contient chacune ?
MESURE
    Pour chaque plage (argument en ligne, ou fichier d'une plage par ligne) : réseau,
    masque, diffusion, nombre d'adresses et d'hôtes utilisables (calcul arithmétique,
    sans énumération), classe par le bloc spécial IANA le plus spécifique qui la contient
    (privée, cgnat, loopback, link-local, multicast, réservée, publique, ou mixte si la
    plage couvre plusieurs classes). Chevauchements par balayage trié (inclusion ou
    identité : deux blocs CIDR sont disjoints ou emboîtés). Agrégation par
    ipaddress.collapse_addresses (et netaddr.cidr_merge, contrôlé contre elle, si présent).
    Appartenance : --contient ADRESSE liste les plages qui la contiennent.
HYPOTHÈSES
    Les entrées sont des adresses, des CIDR (préfixe ou masque) ou des intervalles
    « début-fin » ; une plage aux bits d'hôte positionnés (10.0.0.5/8) est ramenée à son
    réseau et signalée. La table des blocs spéciaux reflète le registre IANA connu de
    l'auteur à la date de l'outil.
LIMITES
    Granularité au bloc de la table : un bloc non listé est déclaré « publique ». Aucune
    notion de routage, d'annonce BGP ni d'attribution à un opérateur. Les identifiants
    de zone IPv6 (fe80::1%eth0) sont refusés. Un fichier est lu en flux, borné par
    --max-lignes ; au-delà, la lecture s'arrête et le JSON le dit.
CONTRE-EXEMPLES
    192.0.0.0/24 sort « mixte » et non « réservée » : il contient 192.0.0.9/32 et
    192.0.0.10/32, joignables globalement selon l'IANA ; c'est exact mais surprend.
    64:ff9b::/96 (traduction IPv6 vers IPv4) sort « publique » : l'IANA le déclare
    globalement joignable, alors qu'en pratique il ne désigne que des traductions locales.
INVOCATION
    {outil} 10.0.0.0/8 10.1.0.0/16 192.168.1.0/24 --json
DOMAINE
    Revue de règles de pare-feu, de groupes de sécurité, de plans d'adressage et de
    listes d'autorisation, en IPv4 comme en IPv6.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import netaddr
except ImportError:
    netaddr = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
             INTITULE_INVOCATION, "DOMAINE")

PRIVEE = "privée"
CGNAT = "cgnat"
LOOPBACK = "loopback"
LINK_LOCAL = "link-local"
MULTICAST = "multicast"
RESERVEE = "réservée"
PUBLIQUE = "publique"
MIXTE = "mixte"

NOTATION_CIDR = "CIDR"
REGISTRE_IANA = "IANA"
METAVAR_ADRESSE = "ADRESSE"

MAX_EXAMINES = 200
OCTETS_SONDE_BINAIRE = 8192

# (bloc, classe, désignation, référence) — registre IANA des adresses à usage spécial.
BLOCS_SPECIAUX = (
    ("0.0.0.0/8", RESERVEE, "« ce réseau »", "RFC 791"),
    ("0.0.0.0/32", RESERVEE, "« cet hôte » (non spécifiée)", "RFC 1122"),
    ("10.0.0.0/8", PRIVEE, "usage privé", "RFC 1918"),
    ("100.64.0.0/10", CGNAT, "espace partagé (NAT d'opérateur)", "RFC 6598"),
    ("127.0.0.0/8", LOOPBACK, "boucle locale", "RFC 1122"),
    ("169.254.0.0/16", LINK_LOCAL, "lien local", "RFC 3927"),
    ("172.16.0.0/12", PRIVEE, "usage privé", "RFC 1918"),
    ("192.0.0.0/24", RESERVEE, "affectations de protocole IETF", "RFC 6890"),
    ("192.0.0.9/32", PUBLIQUE, "anycast PCP", "RFC 7723"),
    ("192.0.0.10/32", PUBLIQUE, "anycast TURN", "RFC 8155"),
    ("192.0.2.0/24", RESERVEE, "documentation (TEST-NET-1)", "RFC 5737"),
    ("192.88.99.0/24", RESERVEE, "relais 6to4 anycast (déprécié)", "RFC 7526"),
    ("192.168.0.0/16", PRIVEE, "usage privé", "RFC 1918"),
    ("198.18.0.0/15", RESERVEE, "tests de performance", "RFC 2544"),
    ("198.51.100.0/24", RESERVEE, "documentation (TEST-NET-2)", "RFC 5737"),
    ("203.0.113.0/24", RESERVEE, "documentation (TEST-NET-3)", "RFC 5737"),
    ("224.0.0.0/4", MULTICAST, "multidiffusion", "RFC 5771"),
    ("240.0.0.0/4", RESERVEE, "réservé pour usage futur", "RFC 1112"),
    ("255.255.255.255/32", RESERVEE, "diffusion limitée", "RFC 919"),
    ("::/8", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("::/128", RESERVEE, "adresse non spécifiée", "RFC 4291"),
    ("::1/128", LOOPBACK, "boucle locale", "RFC 4291"),
    ("::ffff:0:0/96", RESERVEE, "IPv4 mappée", "RFC 4291"),
    ("64:ff9b::/96", PUBLIQUE, "préfixe bien connu de traduction IPv4/IPv6", "RFC 6052"),
    ("64:ff9b:1::/48", PRIVEE, "traduction IPv4/IPv6 locale", "RFC 8215"),
    ("100::/8", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("100::/64", RESERVEE, "préfixe de rejet", "RFC 6666"),
    ("200::/7", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("400::/6", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("800::/5", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("1000::/4", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("2001::/23", RESERVEE, "affectations de protocole IETF", "RFC 2928"),
    ("2001:1::1/128", PUBLIQUE, "anycast PCP", "RFC 7723"),
    ("2001:1::2/128", PUBLIQUE, "anycast TURN", "RFC 8155"),
    ("2001:3::/32", PUBLIQUE, "AMT", "RFC 7450"),
    ("2001:4:112::/48", PUBLIQUE, "AS112-v6", "RFC 7535"),
    ("2001:20::/28", PUBLIQUE, "ORCHIDv2", "RFC 7343"),
    ("2001:30::/28", PUBLIQUE, "DRIP DET", "RFC 9374"),
    ("2001:db8::/32", RESERVEE, "documentation", "RFC 3849"),
    ("3fff::/20", RESERVEE, "documentation", "RFC 9637"),
    ("4000::/3", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("5f00::/16", RESERVEE, "identifiants SRv6", "RFC 9602"),
    ("6000::/3", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("8000::/3", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("a000::/3", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("c000::/3", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("e000::/4", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("f000::/5", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("f800::/6", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("fc00::/7", PRIVEE, "adresses locales uniques (ULA)", "RFC 4193"),
    ("fe00::/9", RESERVEE, "réservé IETF (hors unicast global)", "RFC 4291"),
    ("fe80::/10", LINK_LOCAL, "lien local", "RFC 4291"),
    ("fec0::/10", RESERVEE, "site local (déprécié)", "RFC 3879"),
    ("ff00::/8", MULTICAST, "multidiffusion", "RFC 4291"),
)

Reseau = ipaddress.IPv4Network | ipaddress.IPv6Network
Adresse = ipaddress.IPv4Address | ipaddress.IPv6Address


class EntreeInvalide(Exception):
    """Entrée qui n'est ni une plage, ni un intervalle, ni un fichier lisible (code 2)."""


@dataclass(frozen=True)
class BlocSpecial:
    """Un bloc du registre, réduit à des bornes entières pour des comparaisons rapides."""

    version: int
    debut: int
    fin: int
    prefixe: int
    texte: str
    classe: str
    designation: str
    reference: str


@dataclass
class Plage:
    """Une plage examinée : son texte d'origine, sa provenance et le réseau retenu."""

    entree: str
    source: str
    reseau: Reseau
    bits_hote_corriges: bool = False
    issue_intervalle: bool = False


@dataclass
class Lecture:
    """Résultat de la lecture des arguments : plages valides, entrées refusées, alertes."""

    plages: list[Plage] = field(default_factory=list)
    invalides: list[dict[str, str]] = field(default_factory=list)
    fichiers: list[str] = field(default_factory=list)
    tronque: bool = False


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


# --------------------------------------------------------------------------- table


def construire_table() -> tuple[BlocSpecial, ...]:
    """Convertit BLOCS_SPECIAUX en bornes entières (appelée une fois par exécution)."""
    table = []
    for texte, classe, designation, reference in BLOCS_SPECIAUX:
        reseau = ipaddress.ip_network(texte)
        table.append(BlocSpecial(reseau.version, int(reseau.network_address),
                                 int(reseau.broadcast_address), reseau.prefixlen,
                                 texte, classe, designation, reference))
    return tuple(table)


def bornes(reseau: Reseau) -> tuple[int, int]:
    """Bornes entières (première, dernière adresse) d'un réseau."""
    return int(reseau.network_address), int(reseau.broadcast_address)


def classer(reseau: Reseau, table: Sequence[BlocSpecial]) -> dict[str, object]:
    """Classe un réseau par le bloc le plus spécifique qui le contient, ou « mixte »."""
    debut, fin = bornes(reseau)
    meme_version = [b for b in table if b.version == reseau.version]
    contenants = [b for b in meme_version if b.debut <= debut and fin <= b.fin]
    inclus = [b for b in meme_version if debut <= b.debut and b.fin <= fin
              and not (b.debut == debut and b.fin == fin)]
    porteur = max(contenants, key=lambda b: b.prefixe) if contenants else None
    classe = porteur.classe if porteur else PUBLIQUE
    classes = {classe} | {b.classe for b in inclus}
    resultat: dict[str, object] = {
        "classe": classe if len(classes) == 1 else MIXTE,
        "designation": porteur.designation if porteur else "unicast global",
        "reference": porteur.reference if porteur else "",
        "bloc": porteur.texte if porteur else "",
    }
    if len(classes) > 1:
        resultat["classes_couvertes"] = sorted(classes)
        resultat["blocs_speciaux_inclus"] = [
            {"bloc": b.texte, "classe": b.classe, "designation": b.designation}
            for b in sorted(inclus, key=lambda b: (b.debut, b.prefixe))[:20]]
    return resultat


# --------------------------------------------------------------------------- calculs


def compter_hotes(reseau: Reseau) -> int:
    """Hôtes utilisables, même convention que ipaddress.hosts() (RFC 3021 pour /31)."""
    total = reseau.num_addresses
    if reseau.version == 4:
        return total if reseau.prefixlen >= 31 else total - 2
    return total if reseau.prefixlen >= 127 else total - 1


def bornes_utilisables(reseau: Reseau) -> tuple[str, str]:
    """Première et dernière adresse utilisables, sans énumérer le réseau."""
    premier, dernier = reseau.network_address, reseau.broadcast_address
    if reseau.version == 4 and reseau.prefixlen < 31:
        return str(premier + 1), str(dernier - 1)
    if reseau.version == 6 and reseau.prefixlen < 127:
        return str(premier + 1), str(dernier)
    return str(premier), str(dernier)


def decrire_plage(rang: int, plage: Plage, table: Sequence[BlocSpecial]) -> dict[str, object]:
    """Fiche complète d'une plage : géométrie, comptes, classe."""
    reseau = plage.reseau
    premiere, derniere = bornes_utilisables(reseau)
    fiche: dict[str, object] = {
        "rang": rang,
        "entree": plage.entree,
        "source": plage.source,
        "reseau": str(reseau),
        "version": reseau.version,
        "prefixe": reseau.prefixlen,
        "adresse_reseau": str(reseau.network_address),
        "masque": str(reseau.netmask),
        "masque_inverse": str(reseau.hostmask),
        "diffusion": str(reseau.broadcast_address) if reseau.version == 4 else None,
        "nombre_adresses": reseau.num_addresses,
        "nombre_hotes": compter_hotes(reseau),
        "premiere_utilisable": premiere,
        "derniere_utilisable": derniere,
        "bits_hote_corriges": plage.bits_hote_corriges,
        "issue_intervalle": plage.issue_intervalle,
    }
    fiche.update(classer(reseau, table))
    return fiche


def trouver_chevauchements(plages: Sequence[Plage]) -> Iterator[tuple[int, int, str]]:
    """Balayage trié : rend (contenant, contenu, relation) pour chaque paire emboîtée.

    Deux blocs CIDR sont disjoints ou emboîtés ; après tri par (version, début,
    préfixe), la pile des blocs ouverts est une chaîne d'emboîtements : chaque élément
    restant sur la pile contient le bloc courant.
    """
    ordre = sorted(range(len(plages)), key=lambda i: (plages[i].reseau.version,
                                                     int(plages[i].reseau.network_address),
                                                     plages[i].reseau.prefixlen, i))
    pile: list[int] = []
    for i in ordre:
        reseau = plages[i].reseau
        debut, _ = bornes(reseau)
        while pile and (plages[pile[-1]].reseau.version != reseau.version
                        or bornes(plages[pile[-1]].reseau)[1] < debut):
            pile.pop()
        for j in pile:
            relation = "identique" if plages[j].reseau == reseau else "contient"
            yield j, i, relation
        pile.append(i)


def agreger_stdlib(plages: Sequence[Plage]) -> list[str]:
    """Agrégation minimale par version avec ipaddress.collapse_addresses."""
    resultat: list[str] = []
    for version in (4, 6):
        reseaux = [p.reseau for p in plages if p.reseau.version == version]
        resultat.extend(str(r) for r in ipaddress.collapse_addresses(reseaux))
    return resultat


def agreger_netaddr(plages: Sequence[Plage]) -> list[str]:
    """Agrégation avec netaddr.cidr_merge (bibliothèque optionnelle)."""
    fusion = netaddr.cidr_merge([str(p.reseau) for p in plages])
    return [str(ipaddress.ip_network(str(r))) for r in fusion]


def tester_appartenance(cibles: Sequence[str], plages: Sequence[Plage],
                        table: Sequence[BlocSpecial]) -> list[dict[str, object]]:
    """Pour chaque adresse (ou réseau) demandée, les plages qui la contiennent."""
    resultats = []
    for cible in cibles:
        objet = lire_cible(cible)
        contenue = [i for i, p in enumerate(plages) if contient(p.reseau, objet)]
        specifique = max(contenue, key=lambda i: plages[i].reseau.prefixlen) if contenue else None
        reseau_cible = ipaddress.ip_network(objet) if not isinstance(objet, (
            ipaddress.IPv4Network, ipaddress.IPv6Network)) else objet
        resultats.append({
            "cible": cible,
            "contenue_dans": [{"rang": i, "reseau": str(plages[i].reseau),
                               "source": plages[i].source} for i in contenue],
            "plus_specifique": str(plages[specifique].reseau) if specifique is not None else None,
            "classe_cible": classer(reseau_cible, table)["classe"],
        })
    return resultats


def lire_cible(texte: str) -> Adresse | Reseau:
    """Interprète l'argument de --contient : adresse, sinon réseau."""
    try:
        return ipaddress.ip_address(texte.strip())
    except ValueError:
        pass
    try:
        return ipaddress.ip_network(texte.strip(), strict=False)
    except ValueError as exc:
        raise EntreeInvalide(f"--contient « {texte} » : ni adresse ni réseau IP ({exc})") from exc


def contient(reseau: Reseau, objet: Adresse | Reseau) -> bool:
    """Vrai si l'adresse ou le réseau est entièrement dans la plage (même version)."""
    if objet.version != reseau.version:
        return False
    if isinstance(objet, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return objet.subnet_of(reseau)
    return objet in reseau


# --------------------------------------------------------------------------- lecture


def analyser_entree(texte: str, source: str) -> list[Plage]:
    """Transforme un jeton (adresse, CIDR, intervalle a-b) en plages ; lève ValueError."""
    jeton = texte.strip()
    if "-" in jeton:
        debut_txt, fin_txt = (x.strip() for x in jeton.split("-", 1))
        debut, fin = ipaddress.ip_address(debut_txt), ipaddress.ip_address(fin_txt)
        if debut.version != fin.version or debut > fin:
            raise ValueError(f"intervalle « {jeton} » incohérent (versions ou ordre)")
        return [Plage(jeton, source, r, issue_intervalle=True)
                for r in ipaddress.summarize_address_range(debut, fin)]
    try:
        return [Plage(jeton, source, ipaddress.ip_network(jeton, strict=True))]
    except ValueError:
        reseau = ipaddress.ip_network(jeton, strict=False)
        return [Plage(jeton, source, reseau, bits_hote_corriges=True)]


def decouper_ligne(ligne: str) -> list[str]:
    """Retire le commentaire, recolle les intervalles « a - b », découpe sur , et blancs."""
    utile = re.split(r"[#;]", ligne, maxsplit=1)[0]
    utile = re.sub(r"\s*-\s*", "-", utile.strip())
    return [j for j in re.split(r"[,\s]+", utile) if j]


def verifier_fichier(chemin: Path, texte: str) -> None:
    """Refuse un dossier ou un fichier binaire (l'entrée est alors déclarée invalide)."""
    if chemin.is_dir():
        raise EntreeInvalide(f"« {texte} » est un dossier : attendu une plage IP ou un "
                             "fichier d'une plage par ligne")
    try:
        with chemin.open("rb") as flux:
            sonde = flux.read(OCTETS_SONDE_BINAIRE)
    except OSError as exc:
        raise EntreeInvalide(f"« {texte} » illisible : {exc.strerror or exc}") from exc
    if b"\x00" in sonde:
        raise EntreeInvalide(f"« {texte} » est un fichier binaire (octet nul) : "
                             "attendu un texte d'une plage par ligne")


def lire_fichier(chemin: Path, texte: str, lecture: Lecture, max_lignes: int) -> None:
    """Lit un fichier d'une plage par ligne, en flux ; consigne les lignes invalides."""
    verifier_fichier(chemin, texte)
    lecture.fichiers.append(texte)
    with chemin.open("rb") as flux:
        for numero, brute in enumerate(flux, start=1):
            if numero > max_lignes:
                lecture.tronque = True
                break
            source = f"{texte}:{numero}"
            try:
                ligne = brute.decode("utf-8")
            except UnicodeDecodeError:
                lecture.invalides.append({"source": source, "entree": repr(brute[:80]),
                                          "erreur": "ligne non décodable en utf-8"})
                continue
            ajouter_jetons(decouper_ligne(ligne), source, lecture)


def ajouter_jetons(jetons: Sequence[str], source: str, lecture: Lecture) -> None:
    """Ajoute les plages de chaque jeton, ou consigne l'erreur."""
    for jeton in jetons:
        try:
            lecture.plages.extend(analyser_entree(jeton, source))
        except ValueError as exc:
            lecture.invalides.append({"source": source, "entree": jeton, "erreur": str(exc)})


def lire_arguments(arguments: Sequence[str], base: Path, max_lignes: int) -> Lecture:
    """Chaque argument est une plage, un intervalle, ou un fichier d'une plage par ligne."""
    lecture = Lecture()
    for rang, texte in enumerate(arguments, start=1):
        source = f"argument {rang}"
        try:
            lecture.plages.extend(analyser_entree(texte, source))
            continue
        except ValueError as exc:
            erreur = str(exc)
        chemin = Path(texte) if Path(texte).is_absolute() else base / texte
        if not chemin.exists():
            lecture.invalides.append({"source": source, "entree": texte,
                                      "erreur": f"ni plage IP valide ({erreur}) ni fichier existant"})
            continue
        try:
            lire_fichier(chemin, texte, lecture, max_lignes)
        except EntreeInvalide as exc:
            lecture.invalides.append({"source": source, "entree": texte, "erreur": str(exc)})
    return lecture


# --------------------------------------------------------------------------- rapport


def choisir_moteur(demande: str) -> str:
    """Rend 'netaddr' ou 'stdlib' ; prévient sur stderr si la bibliothèque manque."""
    if demande == "stdlib":
        return "stdlib"
    if netaddr is None:
        if demande == "netaddr":
            raise EntreeInvalide("--moteur netaddr demandé mais netaddr n'est pas installé")
        print("netaddr absent : agrégation par ipaddress.collapse_addresses seule "
              "(moteur stdlib)", file=sys.stderr)
        return "stdlib"
    return "netaddr"


def construire_rapport(lecture: Lecture, moteur: str, cibles: Sequence[str],
                       max_paires: int) -> dict[str, Any]:
    """Assemble le rapport complet (fiches, chevauchements, agrégat, appartenances)."""
    table = construire_table()
    plages = lecture.plages
    fiches = [decrire_plage(i, p, table) for i, p in enumerate(plages)]
    paires, total_paires = [], 0
    for j, i, relation in trouver_chevauchements(plages):
        total_paires += 1
        if len(paires) < max_paires:
            paires.append({"contenant": str(plages[j].reseau), "source_contenant": plages[j].source,
                           "contenu": str(plages[i].reseau), "source_contenu": plages[i].source,
                           "relation": relation})
    agregat = agreger_stdlib(plages)
    rapport: dict[str, Any] = {
        "outil": Path(__file__).stem,
        "python": platform.python_version(),
        "moteur": moteur,
        "registre": f"blocs à usage spécial {REGISTRE_IANA} ({len(table)} blocs embarqués)",
        "denominateur": len(plages),
        "examines": [f"{p.entree} -> {p.reseau}" for p in plages[:MAX_EXAMINES]],
        "examines_tronques": len(plages) > MAX_EXAMINES,
        "fichiers_lus": lecture.fichiers,
        "lecture_tronquee": lecture.tronque,
        "entrees_invalides": lecture.invalides,
        "plages": fiches,
        "chevauchements": {"total": total_paires, "liste": paires,
                           "liste_tronquee": total_paires > len(paires)},
        "agregat": agregat,
        "reduction": {"avant": len(plages), "apres": len(agregat)},
        "appartenance": tester_appartenance(cibles, plages, table) if cibles else [],
    }
    if moteur == "netaddr":
        rapport["controle_netaddr"] = comparer_agregats(agregat, agreger_netaddr(plages))
    return rapport


def comparer_agregats(stdlib: Sequence[str], autre: Sequence[str]) -> dict[str, object]:
    """Écarts entre l'agrégat stdlib et celui de netaddr (vide si cohérents)."""
    a, b = set(stdlib), set(autre)
    return {"version_netaddr": getattr(netaddr, "__version__", "?"),
            "seulement_stdlib": sorted(a - b), "seulement_netaddr": sorted(b - a),
            "coherent": a == b}


def code_de_sortie(rapport: dict[str, Any]) -> int:
    """2 si entrée invalide, 1 si chevauchement, 0 sinon (3 géré en amont)."""
    if rapport["entrees_invalides"]:
        return 2
    return 1 if rapport["chevauchements"]["total"] else 0


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Sortie lisible : une ligne par plage, puis chevauchements et agrégat."""
    for fiche in rapport["plages"]:
        diffusion = f" diffusion {fiche['diffusion']}" if fiche["diffusion"] else ""
        note = " [bits d'hôte corrigés]" if fiche["bits_hote_corriges"] else ""
        print(f"{fiche['reseau']:<43} {fiche['classe']:<10} {fiche['nombre_hotes']} hôte(s)"
              f"{diffusion} ({fiche['source']}){note}")
    chevauchements = rapport["chevauchements"]
    print(f"\nChevauchements : {chevauchements['total']}")
    for paire in chevauchements["liste"]:
        print(f"  {paire['contenant']} ({paire['source_contenant']}) {paire['relation']} "
              f"{paire['contenu']} ({paire['source_contenu']})")
    reduction = rapport["reduction"]
    print(f"\nAgrégat ({reduction['avant']} -> {reduction['apres']}) : "
          + ", ".join(rapport["agregat"]))
    for test in rapport["appartenance"]:
        print(f"\n{test['cible']} ({test['classe_cible']}) : "
              + (", ".join(c["reseau"] for c in test["contenue_dans"]) or "dans aucune plage"))


def signaler_invalides(invalides: Sequence[dict[str, str]]) -> None:
    """Une ligne sur stderr par entrée refusée (au plus 20), puis le total."""
    for invalide in invalides[:20]:
        print(f"invalide ({invalide['source']}) « {invalide['entree']} » : {invalide['erreur']}",
              file=sys.stderr)
    if len(invalides) > 20:
        print(f"... {len(invalides)} entrées invalides au total", file=sys.stderr)


def afficher_json(objet: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(objet, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande, aide en français."""
    parseur = argparse.ArgumentParser(
        prog=Path(__file__).name,
        description=f"Analyse des plages d'adresses IP ({NOTATION_CIDR} v4/v6, intervalles a-b) : "
                    f"réseau, diffusion, hôtes, classe {REGISTRE_IANA}, chevauchements, "
                    "agrégation, appartenance. "
                    "Code 1 si deux plages se chevauchent, 2 si une entrée est invalide.",
        epilog=f"Exemple : python {RACINE.name}/{Path(__file__).name} 10.0.0.0/8 "
               "10.1.0.0/16 regles_parefeu.txt --contient 10.1.2.3 --json",
    )
    parseur.add_argument("plages", nargs="+",
                         help="plages (10.0.0.0/8, 2001:db8::/32, 10.0.0.1-10.0.0.9) ou "
                              "fichiers d'une plage par ligne (# commente)")
    parseur.add_argument("--contient", action="append", default=[], metavar=METAVAR_ADRESSE,
                         help="adresse (ou réseau) dont on cherche les plages contenantes ; répétable")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "netaddr"), default="auto",
                         help="auto : netaddr pour l'agrégation s'il est installé (contrôlé "
                              "contre ipaddress), sinon stdlib")
    parseur.add_argument("--max-lignes", type=int, default=1_000_000,
                         help="lignes lues au plus par fichier (défaut : 1 000 000)")
    parseur.add_argument("--max-paires", type=int, default=1000,
                         help="chevauchements détaillés au plus (tous sont comptés)")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, analyse, rapporte, rend le code de sortie."""
    args = construire_parseur().parse_args(argv)
    base = args.racine if args.racine is not None else Path.cwd()
    try:
        moteur = choisir_moteur(args.moteur)
        lecture = lire_arguments(args.plages, base, max(1, args.max_lignes))
        rapport = construire_rapport(lecture, moteur, args.contient, max(0, args.max_paires))
    except EntreeInvalide as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            afficher_json({"denominateur": 0, "examines": [], "erreur": str(exc)})
        return 2
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    signaler_invalides(lecture.invalides)
    code = code_de_sortie(rapport)
    if rapport["denominateur"] == 0 and code == 0:
        print("dénominateur nul : aucune plage valide, rien à examiner", file=sys.stderr)
        code = 3
    rapport["code_sortie"] = code
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
