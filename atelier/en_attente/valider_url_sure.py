"""Dire si une URL peut être visitée sans risque de SSRF ni d'usurpation, sans résoudre de nom.

Un filtre écrit avec ipaddress ne voit pas les adresses déguisées : sur 12 écritures de
127.0.0.1 ou de 0.0.0.0 (2130706433, 0x7f.1, 127.1, 0177.0.0.1...), socket.inet_aton,
donc le système qui ouvrira la connexion, les accepte toutes et ipaddress.ip_address
une seule. De même validators 0.35.0 déclare valides https://exаmple.com/ (« а »
cyrillique) et, même avec private=False, http://[::1]/.

QUESTION
    Cette URL peut-elle être visitée par un serveur ou montrée à un humain sans risque
    de SSRF (accès au réseau interne, aux métadonnées du nuage) ni d'usurpation
    (homographe, identifiants trompeurs) ?
MESURE
    Analyse lexicale de l'URL (urllib.parse plus contrôles propres), sans aucune
    requête réseau par défaut : caractères de contrôle et blancs ; schéma (http et
    https seuls admis, --schemas pour en ajouter) ; barres obliques manquantes et
    antislash (lus autrement par les navigateurs) ; partie userinfo avant « @ » ; hôte
    décodé (pourcentages), normalisé (NFKC, points idéographiques, caractères
    invisibles) puis lu comme IPv4 à la manière d'inet_aton (décimal, octal,
    hexadécimal, formes abrégées) ou IPv6 (y compris IPv4 mappé, 6to4, nat64,
    Teredo) et classé par ipaddress (boucle, privé, lien local, nulle, réservée,
    partagée) ; adresses et noms de métadonnées du nuage (169.254.169.254...) ; noms
    internes (localhost, .local, .internal...) et services DNS joker qui renvoient une
    adresse incrustée dans le nom ; IDNA et punycode, mélange d'écritures dans une
    étiquette et étiquette entièrement composée de sosies latins (unicodedata) ; port
    hors 80 et 443, ou port de service interne. Option --resoudre (désactivée par
    défaut) : résout le nom et classe chaque adresse obtenue. Avec la bibliothèque idna,
    l'encodage suit IDNA 2008 (UTS #46) ; avec validators, son avis est joint.
HYPOTHÈSES
    Le client qui suivra l'URL se comporte comme un navigateur ou comme la libc
    (inet_aton) ; l'URL est absolue ; seuls http et https doivent être joignables.
LIMITES
    Sans --resoudre, un nom public qui pointe vers une adresse interne (DNS rebinding,
    enregistrement A sur 127.0.0.1) est jugé sûr ; même avec --resoudre, la réponse
    DNS peut changer entre la vérification et la connexion, et les redirections ne sont
    pas suivies. Sans la bibliothèque idna, la forme punycode suit IDNA 2003 : faß.de
    devient fass.de alors qu'avec idna (IDNA 2008) elle devient xn--fa-hia.de. La
    table de sosies ne couvre que les lettres cyrilliques, grecques et arméniennes les
    plus courantes. Ne juge pas la réputation du domaine.
CONTRE-EXEMPLES
    Faux négatif constaté : http://localtest.example.net/ (nom public dont un
    enregistrement pourrait viser 127.0.0.1) est jugé sûr sans --resoudre. Faux positif
    constaté : https://api.example.com:8443/ est signalé (port inhabituel, faible) alors
    que ce port est courant pour des API légitimes.
INVOCATION
    {outil} "http://0x7f.1/admin" --json
DOMAINE
    URL reçues d'un utilisateur ou d'un tiers (webhooks, aperçus de liens, imports
    distants, liens d'un courriel) avant qu'un service ne les suive ou ne les affiche.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
import unicodedata
import urllib.parse
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import idna
except ImportError:
    idna = None
try:
    import validators
except ImportError:
    validators = None

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_url", "lire_ipv4_souple", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES",
                  "CONTRE-EXEMPLES", TITRE_INVOCATION, "DOMAINE")
ATTAQUE = "SSRF"
NORMALISATION = "NFKC"
CODEC_IDNA = "idna"
GRAVITES = ("info", "faible", "moyenne", "élevée", "critique")

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3
LONGUEUR_MAX = 8192
URLS_MAX = 10_000
SCHEMAS_ADMIS = ("http", "https")
PORTS_DEFAUT = (("http", 80), ("https", 443))
PORTS_INTERNES = frozenset({21, 22, 23, 25, 110, 111, 135, 139, 143, 389, 445, 873, 1433, 1521,
                            2049, 2375, 2376, 2379, 3306, 3389, 5432, 5672, 5984, 6379, 6443,
                            8500, 9000, 9042, 9200, 9300, 10250, 11211, 15672, 27017})
IP_METADONNEES = frozenset({"169.254.169.254", "169.254.170.2", "100.100.100.200",
                            "168.63.129.16", "fd00:ec2::254"})
NOMS_METADONNEES = frozenset({"metadata", "metadata.google.internal", "metadata.goog",
                              "instance-data", "instance-data.ec2.internal"})
SUFFIXES_INTERNES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan",
                     ".intranet", ".corp", ".private", ".test", ".invalid")
NOMS_INTERNES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
DNS_JOKERS = (".nip.io", ".sslip.io", ".xip.io", ".localtest.me", ".lvh.me", ".vcap.me",
              ".traefik.me", ".lacolhost.com")
NOMS_JOKERS = frozenset({"localtest.me", "lvh.me", "vcap.me", "lacolhost.com"})
INVISIBLES = frozenset("­͏؜ᅟᅠ឴឵᠎​‌‍"
                       "‎‏‪‫‬‭‮⁠⁡⁢⁣"
                       "⁤⁦⁧⁨⁩﻿")
POINTS_UNICODE = (("。", "."), ("．", "."), ("｡", "."))
SOSIES = (("а", "a"), ("с", "c"), ("е", "e"), ("һ", "h"), ("і", "i"), ("ј", "j"), ("ӏ", "l"),
          ("о", "o"), ("р", "p"), ("ԛ", "q"), ("ѕ", "s"), ("у", "y"), ("х", "x"), ("ԝ", "w"),
          ("ԁ", "d"), ("ь", "b"), ("α", "a"), ("ο", "o"), ("ν", "v"), ("ρ", "p"), ("ι", "i"),
          ("κ", "k"), ("υ", "u"), ("χ", "x"), ("ε", "e"), ("օ", "o"), ("ս", "u"), ("հ", "h"),
          ("ո", "n"), ("զ", "q"), ("ց", "g"))
ECRITURES_ASIATIQUES = (frozenset({"LATIN", "CJK", "HIRAGANA", "KATAKANA"}),
                        frozenset({"LATIN", "CJK", "BOPOMOFO"}),
                        frozenset({"LATIN", "CJK", "HANGUL"}))
MOTIF_IP_INCRUSTEE = re.compile(r"(?:^|[.-])(\d{1,3})[.-](\d{1,3})[.-](\d{1,3})[.-](\d{1,3})(?=[.-]|$)")
MOTIF_PARTIE_IPV4 = re.compile(r"0[xX][0-9a-fA-F]*|[0-9]+")


@dataclass(frozen=True)
class Risque:
    """Un risque : règle, gravité, explication."""

    regle: str
    gravite: str
    detail: str


@dataclass
class Analyse:
    """Ce qui a été lu d'une URL et les risques trouvés."""

    url: str
    schema: str = ""
    hote: str = ""
    hote_ascii: str = ""
    hote_unicode: str = ""
    ip: str = ""
    port: int | None = None
    risques: list[Risque] = field(default_factory=list)
    avis_validators: bool | None = None
    adresses_resolues: list[str] = field(default_factory=list)


def lire_ipv4_souple(hote: str) -> ipaddress.IPv4Address | None:
    """Lit un hôte comme inet_aton : 1 à 4 parties décimales, octales (0..) ou hexa (0x..).

    Rend None si l'hôte ne se termine pas par un nombre ; lève ValueError s'il s'y termine
    mais n'est pas une adresse valide (le navigateur le refuserait aussi).
    """
    parties = hote[:-1].split(".") if hote.endswith(".") else hote.split(".")
    if not parties or not MOTIF_PARTIE_IPV4.fullmatch(parties[-1]):
        return None
    if len(parties) > 4 or not all(MOTIF_PARTIE_IPV4.fullmatch(p) for p in parties):
        raise ValueError("hôte numérique invalide")
    nombres = [_nombre_ipv4(p) for p in parties]
    if any(n > 255 for n in nombres[:-1]) or nombres[-1] >= 256 ** (5 - len(nombres)):
        raise ValueError("partie d'adresse IPv4 hors bornes")
    valeur = nombres[-1]
    for rang, n in enumerate(nombres[:-1]):
        valeur += n << (8 * (3 - rang))
    return ipaddress.IPv4Address(valeur)


def _nombre_ipv4(partie: str) -> int:
    """Une partie d'adresse : 0x.. hexadécimal, 0.. octal, sinon décimal."""
    if partie[:2].lower() == "0x":
        return int(partie[2:] or "0", 16)
    if len(partie) > 1 and partie.startswith("0"):
        if any(c in "89" for c in partie):
            raise ValueError("chiffre 8 ou 9 dans une partie octale")
        return int(partie, 8)
    return int(partie)


def _ipv4_incluse(ip: ipaddress.IPv6Address) -> tuple[ipaddress.IPv4Address | None, str]:
    """Adresse IPv4 portée par une adresse IPv6 (mappée, 6to4, Teredo, NAT64, compatible)."""
    if ip.ipv4_mapped:
        return ip.ipv4_mapped, "IPv4 mappée dans IPv6 (::ffff:)"
    if ip.sixtofour:
        return ip.sixtofour, "IPv4 incluse par 6to4 (2002::/16)"
    if ip.teredo:
        return ip.teredo[1], "IPv4 cliente incluse par Teredo (2001::/32)"
    if ip in ipaddress.IPv6Network("64:ff9b::/96"):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF), "IPv4 incluse par NAT64 (64:ff9b::/96)"
    if int(ip) >> 32 == 0 and int(ip) > 1:
        return ipaddress.IPv4Address(int(ip)), "IPv4 compatible (::a.b.c.d, obsolète)"
    return None, ""


def _nature_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Catégorie non publique de l'adresse, ou chaîne vide si elle est publique."""
    tests = (("is_unspecified", "adresse nulle (joint la machine locale)"),
             ("is_loopback", "boucle locale"), ("is_link_local", "lien local"),
             ("is_private", "réseau privé"), ("is_multicast", "multidiffusion"),
             ("is_reserved", "plage réservée"))
    for attribut, nom in tests:
        if getattr(ip, attribut):
            return nom
    return "" if ip.is_global else "plage non routable publiquement"


def classer_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[Risque]:
    """Risques d'une adresse : métadonnées du nuage, plage interne, IPv4 cachée dans IPv6."""
    risques: list[Risque] = []
    if isinstance(ip, ipaddress.IPv6Address):
        incluse, comment = _ipv4_incluse(ip)
        if incluse is not None:
            risques.append(Risque("ip-deguisee", "élevée", f"{comment} : {incluse}"))
            return risques + classer_ip(incluse)
    if str(ip) in IP_METADONNEES:
        risques.append(Risque("metadonnees-nuage", "critique",
                              f"{ip} est un point de métadonnées du nuage (identifiants d'instance)"))
    elif nature := _nature_ip(ip):
        risques.append(Risque("ip-interne", "élevée", f"{ip} : {nature}"))
    return risques


def _normaliser_hote(hote: str, risques: list[Risque]) -> str:
    """Pourcentages, invisibles, points unicode, NFKC, casse, point final."""
    if "%" in hote:
        risques.append(Risque("pourcent-dans-hote", "élevée",
                              "hôte encodé en pourcentages : le client le décode avant de se connecter"))
        hote = urllib.parse.unquote(hote)
    invisibles = sorted({f"U+{ord(c):04X}" for c in hote if c in INVISIBLES})
    if invisibles:
        risques.append(Risque("caractere-invisible", "élevée",
                              f"caractères invisibles dans l'hôte : {', '.join(invisibles)}"))
    norme = "".join(c for c in hote if c not in INVISIBLES)
    for point, remplacement in POINTS_UNICODE:
        norme = norme.replace(point, remplacement)
    norme = unicodedata.normalize(NORMALISATION, norme).lower()
    if norme.rstrip(".") != hote.lower().rstrip(".") and not invisibles:
        risques.append(Risque("normalisation-unicode", "élevée",
                              f"l'hôte devient « {norme} » une fois normalisé ({NORMALISATION})"))
    return norme[:-1] if norme.endswith(".") and not norme.endswith("..") else norme


def _ecriture(car: str) -> str:
    """Écriture d'une lettre d'après son nom Unicode (LATIN, CYRILLIC, CJK...)."""
    nom = unicodedata.name(car, "")
    if nom.startswith("KATAKANA-HIRAGANA"):
        return "KATAKANA"
    return nom.split(" ")[0] if nom else "INCONNUE"


def _risques_etiquette(etiquette: str) -> Iterator[Risque]:
    """Mélange d'écritures ou étiquette faite de sosies latins."""
    lettres = [c for c in etiquette if c.isalpha()]
    ecritures = {_ecriture(c) for c in lettres}
    sosies = dict(SOSIES)
    squelette = "".join(sosies.get(c, c) for c in etiquette)
    if len(ecritures) > 1 and not any(ecritures <= admis for admis in ECRITURES_ASIATIQUES):
        yield Risque("homographe", "élevée",
                     f"étiquette « {etiquette} » mêlant les écritures {', '.join(sorted(ecritures))}"
                     + (f" ; se lit comme « {squelette} »" if squelette.isascii() else ""))
    elif ecritures and "LATIN" not in ecritures and lettres and all(c in sosies for c in lettres):
        yield Risque("homographe", "élevée",
                     f"étiquette « {etiquette} » entièrement en sosies de lettres latines : "
                     f"imite « {squelette} »")


def _encoder_idna(hote: str) -> tuple[str, str]:
    """Rend (forme ASCII, erreur) ; idna (IDNA 2008, UTS #46) si présent, sinon codec stdlib."""
    try:
        if idna is not None:
            return idna.encode(hote, uts46=True).decode("ascii"), ""
        return hote.encode(CODEC_IDNA).decode("ascii"), ""
    except (UnicodeError, ValueError) as exc:
        return "", str(exc)[:160]


def _decoder_punycode(hote: str) -> tuple[str, str]:
    """Forme Unicode des étiquettes xn-- ; rend (forme, erreur)."""
    morceaux = []
    for etiquette in hote.split("."):
        if etiquette.startswith("xn--"):
            try:
                etiquette = etiquette[4:].encode("ascii").decode("punycode")
            except (UnicodeError, ValueError) as exc:
                return hote, f"étiquette punycode invalide « {etiquette} » ({exc})"
        morceaux.append(etiquette)
    return ".".join(morceaux), ""


def _risques_idn(a: Analyse) -> Iterator[Risque]:
    """Nom internationalisé : formes ASCII et Unicode, erreurs, homographes."""
    unicode_, erreur = _decoder_punycode(a.hote)
    if erreur:
        yield Risque("idna-invalide", "moyenne", erreur)
    a.hote_unicode = unicode_
    if a.hote.isascii() and unicode_ == a.hote:
        a.hote_ascii = a.hote
        return
    a.hote_ascii, erreur = _encoder_idna(unicode_)
    if erreur:
        yield Risque("idna-invalide", "moyenne", f"encodage {CODEC_IDNA.upper()} refusé : {erreur}")
    yield Risque("nom-internationalise", "info", f"forme Unicode « {unicode_} », forme ASCII "
                                                   f"« {a.hote_ascii or '?'} »")
    for etiquette in unicode_.split("."):
        yield from _risques_etiquette(etiquette)


def _risques_nom(hote: str) -> Iterator[Risque]:
    """Noms internes, métadonnées, DNS joker, adresse incrustée dans le nom."""
    if hote in NOMS_METADONNEES:
        yield Risque("metadonnees-nuage", "critique", f"{hote} désigne le service de métadonnées du nuage")
    if hote in NOMS_INTERNES or hote.endswith(SUFFIXES_INTERNES):
        yield Risque("nom-interne", "élevée", f"{hote} est un nom local ou réservé (réseau interne)")
    elif "." not in hote:
        yield Risque("nom-interne", "moyenne",
                     f"nom sans domaine « {hote} » : complété par les suffixes DNS du réseau local")
    if hote in NOMS_JOKERS or hote.endswith(DNS_JOKERS):
        yield Risque("dns-joker", "élevée", f"{hote} relève d'un service DNS qui renvoie l'adresse "
                                            "écrite dans le nom ou 127.0.0.1")
    for m in MOTIF_IP_INCRUSTEE.finditer(hote):
        try:
            incrustee = ipaddress.IPv4Address(".".join(m.groups()))
        except ValueError:
            continue
        if _nature_ip(incrustee):
            yield Risque("ip-incrustee", "moyenne",
                         f"le nom contient l'adresse {incrustee} ({_nature_ip(incrustee)})")


def _risques_hote(a: Analyse, brut: str, entre_crochets: bool) -> Iterator[Risque]:
    """Hôte : adresse IP (toutes écritures) ou nom."""
    if entre_crochets:
        a.hote = brut.lower()
        yield from _risques_ipv6(a)
        return
    risques: list[Risque] = []
    a.hote = _normaliser_hote(brut, risques)
    yield from risques
    if not a.hote:
        yield Risque("hote-absent", "élevée", "aucun hôte")
        return
    try:
        ip = lire_ipv4_souple(a.hote)
    except ValueError as exc:
        yield Risque("hote-invalide", "élevée", f"hôte numérique « {a.hote} » illisible : {exc}")
        return
    if ip is not None:
        a.ip = str(ip)
        if a.hote != str(ip):
            yield Risque("ip-deguisee", "élevée",
                         f"« {a.hote} » est l'adresse {ip} écrite sous une forme non canonique")
        yield from classer_ip(ip)
        return
    yield from _risques_idn(a)
    yield from _risques_nom(a.hote_ascii or a.hote)


def _risques_ipv6(a: Analyse) -> Iterator[Risque]:
    """Hôte entre crochets : adresse IPv6, zone éventuelle."""
    adresse, _, zone = a.hote.partition("%")
    zone = urllib.parse.unquote(zone)
    try:
        ip = ipaddress.IPv6Address(adresse)
    except ValueError:
        yield Risque("hote-invalide", "élevée", f"adresse IPv6 illisible « {a.hote} »")
        return
    a.ip = str(ip)
    if zone:
        yield Risque("ipv6-zone", "moyenne", f"identifiant de zone « {zone} » (interface locale)")
    yield from classer_ip(ip)


def _risques_port(a: Analyse, port: str) -> Iterator[Risque]:
    """Port explicite : invalide, de service interne ou inhabituel."""
    if not port:
        return
    if not port.isdigit() or not 0 < int(port) < 65536:
        yield Risque("port-invalide", "élevée", f"port « {port[:20]} » invalide")
        return
    a.port = int(port)
    if a.port in PORTS_INTERNES:
        yield Risque("port-service-interne", "moyenne",
                     f"port {a.port} : service d'administration ou de données, jamais exposé au web")
    elif (a.schema, a.port) not in PORTS_DEFAUT and a.port not in (80, 443):
        yield Risque("port-inhabituel", "faible", f"port {a.port} inhabituel pour {a.schema or 'le web'}")


def _risques_bruts(url: str) -> Iterator[Risque]:
    """Contrôles sur la chaîne brute, avant tout découpage."""
    if any(unicodedata.category(c) == "Cc" or c in " \u00a0\u2028\u2029" for c in url):
        yield Risque("caractere-de-controle", "élevée",
                     "blanc ou caractère de contrôle (CR, LF, tabulation...) : injection d'en-tête "
                     "ou lecture divergente selon le client")
    yield from _risques_antislash(url)


def _risques_antislash(url: str) -> Iterator[Risque]:
    """Antislash : les navigateurs le lisent comme « / », urllib non."""
    _, deux_points, reste = url.partition(":")
    avant_requete = re.split(r"[?#]", reste, maxsplit=1)[0]
    if not deux_points or "\\" not in avant_requete:
        return
    tete = re.match(r"[/\\]*", reste).group()
    apres = reste[len(tete):]
    if "\\" in tete or "\\" in re.split(r"[/?#]", apres, maxsplit=1)[0]:
        hote_navigateur = re.split(r"[/\\?#]", apres, maxsplit=1)[0].rsplit("@", 1)[-1]
        yield Risque("antislash", "élevée",
                     "antislash dans l'autorité : urllib et les navigateurs ne lisent pas le même "
                     f"hôte (un navigateur irait vers « {hote_navigateur[:80]} »)")
    else:
        yield Risque("antislash", "faible", "antislash dans le chemin : normalisé en « / » par les navigateurs")


def _separer_autorite(netloc: str) -> tuple[str, str, str, bool]:
    """Rend (userinfo, hôte, port, entre crochets) d'une autorité."""
    userinfo, arobase, hoteport = netloc.rpartition("@")
    if not arobase:
        userinfo = ""
    if hoteport.startswith("["):
        hote, _, reste = hoteport[1:].partition("]")
        return userinfo, hote, reste[1:] if reste.startswith(":") else reste, True
    hote, deux_points, port = hoteport.rpartition(":")
    return (userinfo, hote, port, False) if deux_points else (userinfo, hoteport, "", False)


def _risques_userinfo(userinfo: str) -> Iterator[Risque]:
    """« @ » : identifiants en clair, ou faux hôte placé avant le vrai."""
    if not userinfo:
        return
    utilisateur = urllib.parse.unquote(userinfo.partition(":")[0])
    if "." in utilisateur or "@" in userinfo:
        yield Risque("userinfo-trompeur", "élevée",
                     f"« {utilisateur[:60]}@ » ressemble à un hôte mais n'est qu'un nom d'utilisateur : "
                     "le vrai hôte suit le dernier « @ »")
    else:
        yield Risque("userinfo", "moyenne", "identifiants placés dans l'URL (avant « @ »)")


def analyser_url(url: str, schemas: tuple[str, ...] = SCHEMAS_ADMIS) -> Analyse:
    """Analyse complète d'une URL, sans réseau."""
    a = Analyse(url=url)
    if len(url) > LONGUEUR_MAX:
        a.risques.append(Risque("url-illisible", "élevée", f"URL de plus de {LONGUEUR_MAX} caractères"))
        return a
    a.risques += _risques_bruts(url)
    try:
        morceaux = urllib.parse.urlsplit(url.strip())
    except ValueError as exc:
        a.risques.append(Risque("url-illisible", "élevée", f"urllib.parse refuse l'URL : {exc}"))
        return a
    a.schema = morceaux.scheme
    a.risques += _risques_schema(morceaux, schemas)
    netloc = morceaux.netloc or _hote_sans_barres(morceaux)
    userinfo, hote, port, crochets = _separer_autorite(netloc)
    a.risques += _risques_userinfo(userinfo)
    if morceaux.netloc or a.schema in schemas:
        a.risques += _risques_hote(a, hote, crochets)
        a.risques += _risques_port(a, port)
    return a


def _risques_schema(morceaux: urllib.parse.SplitResult, schemas: tuple[str, ...]) -> Iterator[Risque]:
    """Schéma absent, interdit, ou barres obliques manquantes."""
    if not morceaux.scheme:
        yield Risque("schema-absent", "moyenne", "URL sans schéma : relative ou ambiguë")
    elif morceaux.scheme not in schemas:
        yield Risque("schema-interdit", "élevée",
                     f"schéma « {morceaux.scheme} » : seuls {', '.join(schemas)} sont admis "
                     "(file, gopher, data, dict... ouvrent d'autres protocoles)")
    elif not morceaux.netloc:
        yield Risque("barres-manquantes", "élevée",
                     "autorité vide (http:/x, http:x, http:///x) : un navigateur prendra le "
                     "chemin pour l'hôte")


def _hote_sans_barres(morceaux: urllib.parse.SplitResult) -> str:
    """Hôte qu'un navigateur lirait dans http:/x ou http:///x (chemin pris pour l'autorité)."""
    if morceaux.scheme not in ("http", "https"):
        return ""
    return re.split(r"[/\\?#]", morceaux.path.lstrip("/\\"), maxsplit=1)[0]


def resoudre(a: Analyse) -> None:
    """Option --resoudre : résolution DNS et classement de chaque adresse obtenue."""
    cible = a.hote_ascii or a.hote
    if not cible or a.ip:
        return
    try:
        infos = socket.getaddrinfo(cible, a.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        a.risques.append(Risque("resolution-echouee", "faible", f"résolution de {cible} impossible : {exc}"))
        return
    for adresse in sorted({info[4][0] for info in infos}):
        a.adresses_resolues.append(adresse)
        for risque in classer_ip(ipaddress.ip_address(adresse.split("%")[0])):
            a.risques.append(Risque("resolution-interne", risque.gravite, f"{cible} → {risque.detail}"))


def _gravite_max(a: Analyse) -> str:
    """Gravité la plus haute des risques, ou chaîne vide."""
    return max((r.gravite for r in a.risques), key=GRAVITES.index, default="")


def _resultat(a: Analyse, seuil: str) -> dict[str, object]:
    """Entrée JSON d'une URL ; les risques sous le seuil restent listés à part."""
    rang = GRAVITES.index(seuil)
    retenus = [asdict(r) for r in a.risques if GRAVITES.index(r.gravite) >= rang]
    donnees = asdict(a)
    donnees["url"] = a.url[:300]
    donnees["risques"] = retenus
    donnees["remarques"] = [asdict(r) for r in a.risques if GRAVITES.index(r.gravite) < rang]
    donnees["verdict"] = "RISQUÉE" if retenus else "SÛRE"
    donnees["gravite_max"] = _gravite_max(a)
    return donnees


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
    """Une ligne par URL, puis ses risques."""
    print(f"{rapport['denominateur']} URL examinée(s) — moteur {rapport['moteur']} — "
          f"{rapport['nombre_urls_risquees']} risquée(s) (seuil {rapport['gravite_min']}) — "
          f"résolution DNS {'activée' if rapport['resolution_dns'] else 'désactivée'}")
    for r in rapport["resultats"]:
        print(f"  [{r['verdict']}] {r['url'][:120]}  (hôte « {r['hote']} »"
              + (f", ip {r['ip']}" if r["ip"] else "") + ")")
        for risque in r["risques"]:
            print(f"      {risque['regle']} [{risque['gravite']}] {risque['detail']}")


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description=f"Dit si des URL peuvent être suivies sans risque {ATTAQUE} (réseau interne, "
                    "métadonnées du nuage) ni d'usurpation (homographes, userinfo). Aucune "
                    "requête réseau sauf --resoudre.",
        epilog=f"Exemple : python {RACINE.name}/valider_url_sure.py \"http://0x7f.1/admin\" "
               "\"https://exаmple.com/\" --json   (code 0 : aucune URL risquée ; 1 : risque ; "
               "2 : entrée invalide ; 3 : aucune URL)")
    p.add_argument("urls", nargs="*", help="URL à examiner")
    p.add_argument("--fichier", type=Path, default=None,
                   help="fichier texte d'URL, une par ligne (lignes vides et # ignorées)")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base du chemin relatif de --fichier (défaut : dossier courant)")
    p.add_argument("--schemas", default=",".join(SCHEMAS_ADMIS),
                   help="schémas admis, séparés par des virgules (défaut : http,https)")
    p.add_argument("--gravite-min", choices=GRAVITES, default="faible",
                   help="gravité à partir de laquelle une URL est dite risquée (défaut : faible)")
    p.add_argument("--resoudre", action="store_true",
                   help="résoudre les noms par DNS (accès réseau ; désactivé par défaut)")
    p.add_argument("--stdlib", action="store_true", help="ignorer idna et validators même installés")
    return p


def _lire_liste(chemin: Path) -> list[str]:
    """URL d'un fichier, une par ligne ; lève ValueError pour un binaire ou trop d'URL."""
    brut = chemin.read_bytes()
    if b"\x00" in brut[:8192]:
        raise ValueError("fichier binaire (octet nul)")
    lignes = [l.strip() for l in brut.decode("utf-8", errors="replace").splitlines()]
    urls = [l for l in lignes if l and not l.startswith("#")]
    if len(urls) > URLS_MAX:
        raise ValueError(f"plus de {URLS_MAX} URL")
    return urls


def _urls(o: argparse.Namespace) -> list[str]:
    """URL données en argument et dans --fichier ; lève ValueError ou OSError."""
    urls = list(o.urls)
    if o.fichier is not None:
        base = o.racine if o.racine is not None else Path.cwd()
        chemin = o.fichier if o.fichier.is_absolute() else base / o.fichier
        if not chemin.is_file():
            raise ValueError(f"--fichier introuvable ou n'est pas un fichier : {chemin}")
        urls += _lire_liste(chemin)
    return urls


def _avis_validators(a: Analyse) -> None:
    """Avis syntaxique de validators (True, False), joint à l'analyse."""
    a.avis_validators = validators.url(a.url) is True


def _message_degrade() -> str:
    """Une ligne : quelle bibliothèque manque et quel repli est pris."""
    morceaux = []
    if idna is None:
        morceaux.append(f"idna absent — codec {CODEC_IDNA} de la bibliothèque standard (IDNA 2003)")
    if validators is None:
        morceaux.append("validators absent — pas d'avis syntaxique tiers")
    return " ; ".join(morceaux)


def _moteur(o: argparse.Namespace) -> str:
    """Bibliothèques utilisées, ou stdlib."""
    noms = [n for n, m in (("idna", idna), ("validators", validators)) if m is not None and not o.stdlib]
    return "+".join(noms) or "stdlib"


def _rapport(o: argparse.Namespace, urls: list[str]) -> dict[str, object]:
    """Analyse chaque URL (résolution et avis tiers si demandés) et assemble le rapport."""
    schemas = tuple(s.strip().lower() for s in o.schemas.split(",") if s.strip())
    analyses = [analyser_url(u, schemas) for u in urls]
    for a in analyses:
        if o.resoudre:
            resoudre(a)
        if validators is not None and not o.stdlib:
            _avis_validators(a)
    resultats = [_resultat(a, o.gravite_min) for a in analyses]
    risquees = sum(1 for r in resultats if r["verdict"] == "RISQUÉE")
    return {"denominateur": len(analyses), "examines": [u[:200] for u in urls[:200]],
            "moteur": _moteur(o), "verdict": "RISQUE" if risquees else "AUCUN RISQUE",
            "gravite_min": o.gravite_min, "resolution_dns": o.resoudre,
            "nombre_urls_risquees": risquees, "resultats": resultats,
            "contrat": extraire_contrat(__doc__ or "")}


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : 0 (aucune URL risquée), 1 (risque), 2 (usage), 3 (aucune URL)."""
    o = _parseur().parse_args(argv)
    try:
        urls = _urls(o)
    except (OSError, ValueError) as exc:
        print(f"valider_url_sure : {exc}", file=sys.stderr)
        return CODE_USAGE
    if not urls:
        print("valider_url_sure : dénominateur nul — rien à examiner (aucune URL donnée).", file=sys.stderr)
        if o.json:
            afficher_json({"denominateur": 0, "examines": [], "resultats": []})
        return CODE_VIDE
    if not o.stdlib and (idna is None or validators is None):
        print(f"valider_url_sure : {_message_degrade()}", file=sys.stderr)
    rapport = _rapport(o, urls)
    if o.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return CODE_TROUVE if rapport["nombre_urls_risquees"] else CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
