r"""Un courriel se lit mal « en clair » : en-têtes encodés RFC 2047, liens dont
le texte ment, Reply-To détourné. Avant cet outil, aucun des 142 outils du
dépôt n'importait mailbox ni email.policy (grep : 0 fichier), et mailbox.mbox
ouvre la boîte en lecture-écriture « rb+ » (source de Python 3.14.7) : les
.mbox sont donc découpés ici en flux, en lecture seule.

QUESTION
    Que contient ce courriel, d'où vient-il vraiment, et présente-t-il des
    signes d'hameçonnage ?
MESURE
    Analyse locale (aucune requête réseau) avec email.policy.default :
    en-têtes décodés, Date, Authentication-Results (spf, dkim, dmarc du
    premier en-tête, posé par le serveur destinataire), sauts Received
    remis dans l'ordre chronologique, pièces jointes (nom, type, taille,
    sha256, extension dangereuse, double extension, exécutable déguisé),
    liens dont le domaine affiché diffère du domaine cible, Reply-To d'un
    autre domaine que From, corps texte (HTML converti par html.parser, ou
    par beautifulsoup4 si installée). Fichiers .eml, boîtes .mbox (découpe
    en flux), dossiers Maildir (mailbox.Maildir en lecture seule).
HYPOTHÈSES
    Le premier Authentication-Results et le premier Received sont écrits par
    le serveur de réception de confiance ; ceux du dessous peuvent être
    forgés. Un domaine est comparé sur ses deux derniers labels (trois pour
    les suffixes doubles connus comme co.uk).
LIMITES
    Pas de vérification cryptographique DKIM ni de requête DNS : l'outil lit
    les verdicts déjà posés. Pas de liste publique des suffixes complète.
    Les liens raccourcis ou de suivi ne sont pas suivis. Pièces Outlook .msg
    non lues. Les indices sont des signaux, pas une preuve.
CONTRE-EXEMPLES
    Une lettre commerciale légitime dont le texte affiche le site de la
    marque mais dont le lien passe par le domaine de suivi de son routeur
    est signalée « lien trompeur » (constaté sur le témoin newsletter.eml de
    la session). À l'inverse, un lien « Cliquez ici » vers un faux site
    n'est pas comparé : aucun domaine n'est affiché.
INVOCATION
    {outil} --texte 'From: "Service client" <support@banque.example>\nReply-To: <recuperation@autre.example>\nTo: client@exemple.fr\nSubject: =?utf-8?q?V=C3=A9rification_urgente?=\nDate: Thu, 01 Oct 2026 10:00:00 +0200\nContent-Type: text/html; charset=utf-8\n\n<p>Connectez-vous : <a href="http://203.0.113.9/login">https://banque.example/compte</a></p>' --json
DOMAINE
    Tri de courriels signalés, analyse d'une boîte exportée, revue d'un
    message suspect avant clic, sur fichiers locaux.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import mailbox
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from email import policy
from email.errors import HeaderParseError, MessageError
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator, NamedTuple
from urllib.parse import urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import bs4
except ImportError:
    bs4 = None

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
FORMAT_HTML = "HTML"
METHODE_DKIM = "DKIM"

LIMITE_EXAMINES = 50
LIMITE_MESSAGES_SORTIE = 200
LIMITE_LIENS = 200
TAILLE_MAX_MESSAGE = 64 * 1024 * 1024
LIGNE_MAX = 1024 * 1024
ENTETES_ESSENTIELS = ("from", "to", "subject", "date", "message-id", "received")
EXTENSIONS_COURRIEL = (".eml", ".mbox", ".mbx")
EXTENSIONS_DANGEREUSES = frozenset({
    "exe", "scr", "com", "pif", "bat", "cmd", "js", "jse", "vbs", "vbe", "wsf", "wsh", "hta",
    "msi", "msp", "ps1", "psm1", "jar", "lnk", "iso", "img", "vhd", "vhdx", "cpl", "dll", "reg",
    "chm", "xll", "docm", "xlsm", "pptm", "dotm", "xlam", "ppam", "one", "html", "htm", "shtml",
    "svg", "url", "appx", "msix", "application",
})
EXTENSIONS_CONTENEURS = frozenset({"zip", "rar", "7z", "gz", "tgz", "ace", "cab", "arj", "xz", "bz2"})
EXTENSIONS_DOCUMENTS = frozenset({"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "jpg",
                                  "jpeg", "png", "gif", "csv", "rtf", "odt", "ods"})
SUFFIXES_DOUBLES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "ne.jp",
    "or.jp", "com.br", "com.cn", "co.nz", "co.za", "com.mx", "gouv.fr", "co.in", "com.tr",
    "com.ar", "co.kr", "com.sg", "com.hk", "co.il",
})
TLD_COURANTS = frozenset({
    "com", "net", "org", "fr", "de", "uk", "io", "info", "biz", "be", "ch", "eu", "us", "ca",
    "it", "es", "nl", "ru", "cn", "jp", "br", "au", "gov", "edu", "co", "app", "dev", "ai",
    "me", "tv", "lu", "pt", "pl", "se", "no", "dk", "fi", "at", "ie", "in", "mx", "example",
})
BALISES_BLOC = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                          "table", "ul", "ol", "blockquote", "hr", "section", "article"})
MOTIF_HOTE_AFFICHE = re.compile(
    r"^(?:(?P<schema>https?)://)?(?:[^\s/@]+@)?(?P<hote>(?:[\w](?:[\w-]{0,61}[\w])?\.)+[\w-]{2,63})"
    r"(?::\d+)?(?:[/?#]\S*)?$", re.I)
MOTIF_URL_TEXTE = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.I)
MOTIF_ADRESSE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
MOTIF_RESULTAT_AUTH = re.compile(r"\b(spf|dkim|dmarc|arc|iprev)\s*=\s*([a-z]+)", re.I)
MOTIF_PROPRIETE_AUTH = re.compile(r"\b(header\.d|header\.i|header\.from|smtp\.mailfrom)\s*=\s*([^\s;]+)", re.I)
MOTIF_IPV4 = re.compile(r"\[?(\d{1,3}(?:\.\d{1,3}){3})\]?")
MOTIF_IPV6 = re.compile(r"\[(?:IPv6:)?([0-9a-fA-F:]{3,})\]")


class ErreurEntree(Exception):
    """Entrée invalide : code 2."""


class Brut(NamedTuple):
    """Un message brut à analyser."""
    nom: str
    octets: bytes
    tronque: bool


# ---------------------------------------------------------------- contrat --

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


# ---------------------------------------------------------------- sources --

def lignes_bornees(flux: Any) -> Iterator[bytes]:
    """Lignes d'un flux binaire, coupées à LIGNE_MAX octets."""
    return iter(lambda: flux.readline(LIGNE_MAX), b"")


def messages_mbox(chemin: Path) -> Iterator[Brut]:
    """Découpe une boîte mbox en flux (« From » en tête de ligne après une ligne
    vide), retire une « > » des lignes >From (mboxrd), borne chaque message."""
    tampon: list[bytes] = []
    taille, numero, tronque, vide_avant = 0, 0, False, True
    with chemin.open("rb") as flux:
        for ligne in lignes_bornees(flux):
            if ligne.startswith(b"From ") and vide_avant:
                if numero:
                    yield Brut(f"{chemin.name}#{numero}", b"".join(tampon), tronque)
                numero, tampon, taille, tronque, vide_avant = numero + 1, [], 0, False, False
                continue
            if re.match(rb">+From ", ligne):
                ligne = ligne[1:]
            if taille + len(ligne) <= TAILLE_MAX_MESSAGE:
                tampon.append(ligne)
                taille += len(ligne)
            else:
                tronque = True
            vide_avant = not ligne.strip()
    if numero:
        yield Brut(f"{chemin.name}#{numero}", b"".join(tampon), tronque)


def est_mbox(chemin: Path) -> bool:
    """Une boîte mbox commence par « From » suivi d'une espace."""
    with chemin.open("rb") as flux:
        return flux.read(5) == b"From "


def messages_fichier(chemin: Path) -> Iterator[Brut]:
    """Un .eml (borné) ou une boîte mbox."""
    with chemin.open("rb") as flux:
        tete = flux.read(8)
    if tete.startswith(b"\xd0\xcf\x11\xe0"):
        raise ErreurEntree(f"{chemin} : format Outlook .msg (OLE) non pris en charge")
    if est_mbox(chemin):
        yield from messages_mbox(chemin)
        return
    with chemin.open("rb") as flux:
        octets = flux.read(TAILLE_MAX_MESSAGE + 1)
    yield Brut(chemin.name, octets[:TAILLE_MAX_MESSAGE], len(octets) > TAILLE_MAX_MESSAGE)


def est_maildir(dossier: Path) -> bool:
    """Un Maildir a des sous-dossiers cur et new."""
    return (dossier / "cur").is_dir() and (dossier / "new").is_dir()


def messages_maildir(dossier: Path) -> Iterator[Brut]:
    """Messages d'un Maildir, lus par mailbox.Maildir (create=False : aucune écriture)."""
    boite = mailbox.Maildir(str(dossier), factory=None, create=False)
    for cle in sorted(boite.iterkeys()):
        octets = boite.get_bytes(cle)
        yield Brut(f"{dossier.name}/{cle}", octets[:TAILLE_MAX_MESSAGE], len(octets) > TAILLE_MAX_MESSAGE)


def messages_dossier(dossier: Path) -> Iterator[Brut]:
    """Maildir, ou fichiers .eml/.mbox trouvés récursivement."""
    if est_maildir(dossier):
        yield from messages_maildir(dossier)
        return
    for chemin in sorted(p for p in dossier.rglob("*") if p.suffix.lower() in EXTENSIONS_COURRIEL):
        if chemin.is_file():
            yield from messages_fichier(chemin)


def texte_en_ligne(texte: str) -> bytes:
    """--texte : sans vrai saut de ligne, la séquence « \\n » en tient lieu."""
    if "\n" not in texte and "\r" not in texte:
        texte = texte.replace("\\n", "\n")
    return texte.encode("utf-8")


def sources(args: argparse.Namespace) -> Iterator[Brut]:
    """Tous les messages bruts des chemins et de --texte."""
    for brut in args.chemins:
        chemin = Path(brut)
        if args.racine is not None and not chemin.is_absolute():
            chemin = args.racine / chemin
        if chemin.is_dir():
            yield from messages_dossier(chemin)
        elif chemin.is_file():
            yield from messages_fichier(chemin)
        else:
            raise ErreurEntree(f"chemin introuvable : {chemin}")
    if args.texte is not None:
        yield Brut("--texte", texte_en_ligne(args.texte), False)


# --------------------------------------------------------------- en-têtes --

def decoder_entete(valeur: str) -> str:
    """Décodage RFC 2047 tolérant (charset inconnu ou mot mal formé : brut)."""
    try:
        return str(make_header(decode_header(valeur)))
    except (HeaderParseError, UnicodeDecodeError, LookupError, ValueError):
        return valeur


def entete(msg: EmailMessage, nom: str) -> str | None:
    """En-tête décodé par la politique, avec repli tolérant."""
    try:
        valeur = msg.get(nom)
        return None if valeur is None else " ".join(str(valeur).split())
    except (HeaderParseError, MessageError, IndexError, ValueError, TypeError, AttributeError):
        brut = next((v for k, v in msg.raw_items() if k.lower() == nom.lower()), None)
        return None if brut is None else " ".join(decoder_entete(brut).split())


def entetes_bruts(msg: EmailMessage, nom: str) -> list[str]:
    """Toutes les valeurs brutes d'un en-tête répété, dans l'ordre du fichier."""
    return [" ".join(str(v).split()) for k, v in msg.raw_items() if k.lower() == nom.lower()]


def adresses(valeur: str | None) -> list[tuple[str, str]]:
    """(nom affiché, adresse) d'un en-tête d'adresses."""
    if not valeur:
        return []
    return [(n, a.lower()) for n, a in getaddresses([valeur]) if a]


def domaine_adresse(adresse: str) -> str:
    """Domaine enregistré d'une adresse courriel."""
    return domaine_enregistre(adresse.rpartition("@")[2]) if "@" in adresse else ""


def domaine_enregistre(hote: str) -> str:
    """Approximation du domaine enregistré (2 labels, 3 pour co.uk & co)."""
    labels = [l for l in hote.lower().strip(".").split(".") if l]
    if len(labels) >= 3 and ".".join(labels[-2:]) in SUFFIXES_DOUBLES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def lire_date(msg: EmailMessage) -> dict[str, Any]:
    """Date déclarée, normalisée ISO ; invalide ou future signalée."""
    brut = entete(msg, "date")
    if brut is None:
        return {"brut": None, "iso": None, "valide": False}
    try:
        moment = parsedate_to_datetime(brut)
    except (TypeError, ValueError, IndexError):
        return {"brut": brut, "iso": None, "valide": False}
    future = moment.tzinfo is not None and moment > datetime.now(timezone.utc) + timedelta(days=1)
    return {"brut": brut, "iso": moment.isoformat(), "valide": True, "future": future}


# --------------------------------------------------------- authentification --

def lire_authentification(msg: EmailMessage) -> list[dict[str, Any]]:
    """Chaque Authentication-Results : serveur, verdicts, propriétés."""
    resultats = []
    for valeur in entetes_bruts(msg, "authentication-results"):
        sans_commentaires = re.sub(r"\([^()]*\)", " ", valeur)
        serveur = sans_commentaires.split(";", 1)[0].strip().split()[0] if sans_commentaires.strip() else ""
        verdicts: dict[str, list[str]] = {}
        for methode, verdict in MOTIF_RESULTAT_AUTH.findall(sans_commentaires):
            verdicts.setdefault(methode.lower(), []).append(verdict.lower())
        proprietes = {k.lower(): v.strip('"') for k, v in MOTIF_PROPRIETE_AUTH.findall(sans_commentaires)}
        resultats.append({"serveur": serveur, "verdicts": verdicts, "proprietes": proprietes})
    return resultats


def echecs_authentification(auth: list[dict[str, Any]]) -> list[str]:
    """Échecs lus dans le premier Authentication-Results (le plus fiable)."""
    if not auth:
        return []
    verdicts = auth[0]["verdicts"]
    echecs = []
    if set(verdicts.get("spf", [])) & {"fail", "softfail"}:
        echecs.append(f"spf={verdicts['spf'][0]}")
    dkim = verdicts.get(METHODE_DKIM.lower(), [])
    if dkim and "pass" not in dkim and set(dkim) & {"fail", "permerror"}:
        echecs.append(f"{METHODE_DKIM.lower()}=fail")
    if "fail" in verdicts.get("dmarc", []):
        echecs.append("dmarc=fail")
    return echecs


# ---------------------------------------------------------------- Received --

def lire_saut(valeur: str) -> dict[str, Any]:
    """Un en-tête Received : de, par, IP, date."""
    tete, _, date_brute = valeur.rpartition(";")
    tete = tete or valeur
    de = re.search(r"\bfrom\s+(\S+)", tete, re.I)
    par = re.search(r"\bby\s+(\S+)", tete, re.I)
    ip = MOTIF_IPV6.search(tete) or MOTIF_IPV4.search(tete)
    try:
        moment = parsedate_to_datetime(date_brute.strip()).isoformat() if date_brute.strip() else None
    except (TypeError, ValueError, IndexError):
        moment = None
    adresse_ip = ip.group(1) if ip else None
    return {"de": de.group(1) if de else None, "par": par.group(1) if par else None,
            "ip": adresse_ip, "ip_privee": ip_privee(adresse_ip), "date": moment}


def ip_privee(texte: str | None) -> bool | None:
    """Vrai si l'adresse est privée, réservée ou de bouclage ; None si illisible."""
    if not texte:
        return None
    try:
        adresse = ipaddress.ip_address(texte)
    except ValueError:
        return None
    return adresse.is_private or adresse.is_loopback or adresse.is_reserved


def lire_trajet(msg: EmailMessage) -> dict[str, Any]:
    """Sauts Received remis dans l'ordre chronologique (origine d'abord)."""
    sauts = [lire_saut(v) for v in entetes_bruts(msg, "received")]
    sauts.reverse()
    dernier = sauts[-1] if sauts else None
    return {"nombre_sauts": len(sauts), "sauts": sauts[:50],
            "remis_par": {"de": dernier["de"], "ip": dernier["ip"]} if dernier else None,
            "origine_declaree": {"de": sauts[0]["de"], "ip": sauts[0]["ip"]} if sauts else None}


# ---------------------------------------------------------------- HTML --

class LecteurHtml(HTMLParser):
    """Texte, liens (cible + texte affiché) et formulaires d'un corps HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self.liens: list[tuple[str, str]] = []
        self.formulaires: list[str] = []
        self.scripts = 0
        self.lien_courant: tuple[str, list[str]] | None = None
        self.ignorer = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Ouvre un lien, un bloc ou une zone ignorée."""
        attributs = {k: v or "" for k, v in attrs}
        if tag in ("script", "style", "head", "title"):
            self.ignorer += 1
            self.scripts += tag == "script"
        elif tag == "a" and attributs.get("href"):
            self.lien_courant = (attributs["href"].strip(), [])
        elif tag == "form":
            self.formulaires.append(attributs.get("action", ""))
        if tag in BALISES_BLOC:
            self.morceaux.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Ferme un lien ou une zone ignorée."""
        if tag in ("script", "style", "head", "title") and self.ignorer:
            self.ignorer -= 1
        elif tag == "a" and self.lien_courant is not None:
            cible, texte = self.lien_courant
            self.liens.append((cible, " ".join("".join(texte).split())))
            self.lien_courant = None

    def handle_data(self, data: str) -> None:
        """Texte visible."""
        if self.ignorer:
            return
        self.morceaux.append(data)
        if self.lien_courant is not None:
            self.lien_courant[1].append(data)

    def texte(self) -> str:
        """Texte visible, lignes vides compactées."""
        lignes = [" ".join(l.split()) for l in "".join(self.morceaux).splitlines()]
        return "\n".join(l for l in lignes if l)


def lire_html_stdlib(source: str) -> tuple[str, list[tuple[str, str]], list[str], int]:
    """(texte, liens, formulaires, scripts) par html.parser."""
    lecteur = LecteurHtml()
    lecteur.feed(source)
    lecteur.close()
    return lecteur.texte(), lecteur.liens, lecteur.formulaires, lecteur.scripts


def lire_html_bs4(source: str) -> tuple[str, list[tuple[str, str]], list[str]]:
    """(texte, liens, formulaires) par beautifulsoup4."""
    soupe = bs4.BeautifulSoup(source, "html.parser")
    liens = [(a["href"].strip(), " ".join(a.get_text(" ").split())) for a in soupe.find_all("a", href=True)]
    formulaires = [f.get("action", "") for f in soupe.find_all("form")]
    for balise in soupe(["script", "style", "head", "title"]):
        balise.decompose()
    lignes = [" ".join(l.split()) for l in soupe.get_text("\n").splitlines()]
    return "\n".join(l for l in lignes if l), liens, formulaires


# ---------------------------------------------------------------- corps --

def contenu_texte(partie: EmailMessage) -> str:
    """Contenu texte d'une partie, charset inconnu toléré."""
    try:
        return partie.get_content()
    except (LookupError, UnicodeDecodeError, KeyError, ValueError, AssertionError):
        octets = partie.get_payload(decode=True) or b""
        return octets.decode("utf-8", errors="replace")


def lire_corps(msg: EmailMessage) -> tuple[str | None, str | None]:
    """(texte brut, HTML) du corps principal."""
    try:
        partie_texte = msg.get_body(preferencelist=("plain",))
        partie_html = msg.get_body(preferencelist=("html",))
    except (KeyError, ValueError, AttributeError):
        partie_texte = partie_html = None
    texte = contenu_texte(partie_texte) if partie_texte is not None else None
    source_html = contenu_texte(partie_html) if partie_html is not None else None
    return texte, source_html


def analyser_html(source: str | None, moteur: str) -> dict[str, Any]:
    """Texte, liens et formulaires du HTML, plus contrôle croisé si bs4."""
    if source is None:
        return {"texte": None, "liens": [], "formulaires": [], "ecart_liens": None, "scripts": 0}
    texte, liens, formulaires, scripts = lire_html_stdlib(source)
    ecart = None
    if moteur == "beautifulsoup4":
        texte_b, liens_b, formulaires = lire_html_bs4(source)
        ecart = len(liens_b) - len(liens)
        texte, liens = texte_b, liens_b
    return {"texte": texte, "liens": liens, "formulaires": formulaires, "ecart_liens": ecart,
            "scripts": scripts}


# ---------------------------------------------------------------- liens --

def hote_affiche(texte: str) -> str | None:
    """Domaine que le texte d'un lien prétend montrer, s'il en montre un."""
    trouve = MOTIF_HOTE_AFFICHE.match(texte.strip())
    if trouve is None:
        return None
    hote = trouve.group("hote").lower()
    explicite = trouve.group("schema") or hote.startswith("www.")
    return hote if explicite or hote.rsplit(".", 1)[-1] in TLD_COURANTS else None


def examiner_lien(cible: str, texte: str) -> dict[str, Any]:
    """Un lien : hôte cible, hôte affiché, défauts constatés."""
    try:
        morceaux = urlsplit(html.unescape(cible))
        hote = (morceaux.hostname or "").lower()
    except ValueError:
        morceaux, hote = None, ""
    affiche = hote_affiche(texte)
    defauts = []
    if morceaux is not None and morceaux.scheme.lower() in ("javascript", "data", "vbscript"):
        defauts.append(f"lien {morceaux.scheme.lower()}:")
    if hote and ip_privee(hote) is not None:
        defauts.append("cible en adresse IP")
    if morceaux is not None and "@" in (morceaux.netloc or ""):
        defauts.append("identifiants dans l'URL (texte@hôte)")
    if affiche and hote and domaine_enregistre(affiche) != domaine_enregistre(hote):
        defauts.append(f"texte affiche {affiche}, cible {hote}")
    idn = any(l.startswith("xn--") for l in hote.split(".")) or not hote.isascii()
    return {"cible": cible[:500], "texte": texte[:200], "hote_cible": hote or None,
            "hote_affiche": affiche, "idn": idn, "defauts": defauts}


def liens_texte_brut(texte: str | None) -> list[tuple[str, str]]:
    """URL écrites en clair dans le corps texte."""
    if not texte:
        return []
    return [(url, "") for url in MOTIF_URL_TEXTE.findall(texte)]


# --------------------------------------------------------- pièces jointes --

def est_piece_jointe(partie: EmailMessage) -> bool:
    """Partie feuille portant un nom de fichier ou une disposition attachment."""
    if partie.is_multipart():
        return False
    return partie.get_content_disposition() == "attachment" or bool(partie.get_filename())


def examiner_piece(partie: EmailMessage) -> dict[str, Any]:
    """Nom, type, taille, sha256 et défauts d'une pièce jointe."""
    try:
        nom = partie.get_filename() or ""
    except (HeaderParseError, ValueError, IndexError):
        nom = ""
    octets = partie.get_payload(decode=True)
    if not isinstance(octets, bytes):
        octets = partie.as_bytes()
    return {"nom": nom, "type": partie.get_content_type(), "taille": len(octets),
            "sha256": hashlib.sha256(octets).hexdigest(), "defauts": defauts_piece(nom, octets),
            "conteneur": nom.lower().rsplit(".", 1)[-1] in EXTENSIONS_CONTENEURS}


def defauts_piece(nom: str, octets: bytes) -> list[str]:
    """Extension dangereuse, double extension, renversement RTL, exécutable déguisé."""
    defauts = []
    morceaux = nom.lower().strip().rstrip(".").split(".")
    extension = morceaux[-1] if len(morceaux) > 1 else ""
    if extension in EXTENSIONS_DANGEREUSES:
        defauts.append(f"extension dangereuse .{extension}")
    if len(morceaux) > 2 and morceaux[-2] in EXTENSIONS_DOCUMENTS and extension in EXTENSIONS_DANGEREUSES:
        defauts.append(f"double extension .{morceaux[-2]}.{extension}")
    if "‮" in nom:
        defauts.append("caractère de renversement droite-gauche dans le nom")
    executable = octets.startswith((b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"))
    if executable and extension not in EXTENSIONS_DANGEREUSES:
        defauts.append("contenu exécutable sous une extension anodine")
    return defauts


def lire_pieces(msg: EmailMessage) -> list[dict[str, Any]]:
    """Toutes les pièces jointes, messages transférés compris."""
    pieces = []
    for partie in msg.walk():
        if partie is not msg and partie.get_content_type() == "message/rfc822":
            pieces.append(examiner_piece(partie))
        elif partie is not msg and est_piece_jointe(partie):
            pieces.append(examiner_piece(partie))
    return pieces


# --------------------------------------------------------------- analyse --

def verifier_message(msg: EmailMessage, brut: Brut) -> None:
    """Refuse ce qui n'a aucun en-tête de courriel."""
    noms = {k.lower() for k, _ in msg.raw_items()}
    if not noms & set(ENTETES_ESSENTIELS):
        raise ErreurEntree(f"{brut.nom} : aucun en-tête From/To/Subject/Date/Received — "
                           "ce n'est pas un courriel")


def indices_entetes(exp: list[tuple[str, str]], rep: list[tuple[str, str]],
                    auth: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Indices tirés des en-têtes."""
    indices = []
    for echec in echecs_authentification(auth):
        indices.append({"code": "auth_echec", "detail": f"{echec} selon {auth[0]['serveur'] or '?'}"})
    domaine_de = domaine_adresse(exp[0][1]) if exp else ""
    for _, adresse in rep:
        if domaine_de and domaine_adresse(adresse) != domaine_de:
            indices.append({"code": "reponse_ailleurs", "detail": f"Reply-To {adresse} ≠ From {exp[0][1]}"})
    for nom, adresse in exp:
        cache = MOTIF_ADRESSE.search(nom or "")
        if cache and domaine_enregistre(cache.group(1)) != domaine_adresse(adresse):
            indices.append({"code": "nom_affiche_trompeur", "detail": f"« {nom} » <{adresse}>"})
    return indices


def indices_contenu(liens: list[dict[str, Any]], pieces: list[dict[str, Any]],
                    formulaires: list[str]) -> list[dict[str, str]]:
    """Indices tirés des liens, pièces jointes et formulaires."""
    indices = [{"code": "lien_suspect", "detail": f"{d} ({l['cible'][:120]})"}
               for l in liens for d in l["defauts"]]
    indices += [{"code": "piece_dangereuse", "detail": f"{p['nom'] or p['type']} : {d}"}
                for p in pieces for d in p["defauts"]]
    indices += [{"code": "formulaire", "detail": f"formulaire vers « {a[:120]} »"} for a in formulaires]
    return indices


def notes_message(exp: list[tuple[str, str]], retour: str | None, auth: list[dict[str, Any]],
                  date: dict[str, Any], lecture: dict[str, Any], liens: list[dict[str, Any]]) -> list[str]:
    """Signaux faibles, non comptés comme indices."""
    notes = [f"{lecture['scripts']} balise(s) script dans le HTML"] if lecture["scripts"] else []
    if not auth:
        notes.append("aucun Authentication-Results : authenticité inconnue")
    retour_adr = adresses(retour)
    if exp and retour_adr and domaine_adresse(retour_adr[0][1]) != domaine_adresse(exp[0][1]):
        notes.append(f"Return-Path {retour_adr[0][1]} d'un autre domaine que From (routeur d'envoi ?)")
    if not date["valide"]:
        notes.append("Date absente ou illisible")
    elif date.get("future"):
        notes.append("Date dans le futur")
    notes += [f"domaine internationalisé : {l['hote_cible']}" for l in liens if l["idn"]]
    return notes


def analyser_message(brut: Brut, moteur_html: str, corps_max: int) -> dict[str, Any]:
    """Analyse complète d'un message brut."""
    msg = BytesParser(policy=policy.default).parsebytes(brut.octets)
    verifier_message(msg, brut)
    exp, rep = adresses(entete(msg, "from")), adresses(entete(msg, "reply-to"))
    auth, date = lire_authentification(msg), lire_date(msg)
    texte, source_html = lire_corps(msg)
    lecture = analyser_html(source_html, moteur_html)
    liens = [examiner_lien(c, t) for c, t in lecture["liens"] + liens_texte_brut(texte)][:LIMITE_LIENS]
    pieces = lire_pieces(msg)
    indices = indices_entetes(exp, rep, auth) + indices_contenu(liens, pieces, lecture["formulaires"])
    corps = texte if texte is not None else lecture["texte"]
    return {
        "nom": brut.nom, "tronque": brut.tronque,
        "entetes": {n: entete(msg, n) for n in ("from", "to", "cc", "reply-to", "return-path",
                                                 "sender", "subject", "message-id")},
        "date": date, "authentification": auth, "trajet": lire_trajet(msg),
        "pieces_jointes": pieces, "liens": liens, "ecart_liens_moteurs": lecture["ecart_liens"],
        "corps_type": "texte" if texte is not None else ("html" if source_html is not None else None),
        "corps_extrait": corps[:corps_max] if corps else "",
        "defauts_structure": len(msg.defects),
        "indices": indices, "notes": notes_message(exp, entete(msg, "return-path"), auth, date, lecture, liens),
    }


def analyser(args: argparse.Namespace) -> dict[str, Any]:
    """Cœur : parcourt toutes les sources en flux."""
    moteur = "beautifulsoup4" if bs4 is not None and args.html != "stdlib" else "stdlib"
    resultats, erreurs, noms, total, suspects = [], [], [], 0, 0
    for brut in sources(args):
        total += 1
        noms.append(brut.nom)
        try:
            res = analyser_message(brut, moteur, args.corps_max)
        except ErreurEntree as exc:
            erreurs.append({"nom": brut.nom, "erreur": str(exc)})
            continue
        suspects += bool(res["indices"])
        if len(resultats) < LIMITE_MESSAGES_SORTIE:
            resultats.append(res)
    return {"denominateur": total, "examines": noms[:LIMITE_EXAMINES],
            "examines_tronques": len(noms) > LIMITE_EXAMINES, "moteur": moteur,
            "messages_suspects": suspects, "messages": resultats,
            "messages_tronques": total - len(erreurs) > len(resultats), "erreurs": erreurs}


# ---------------------------------------------------------------- sortie --

def sans_substituts(texte: str) -> str:
    """Octets non décodables (substituts U+DC80…) rendus en \\udcXX imprimables."""
    return texte.encode("utf-8", "backslashreplace").decode("utf-8")


def visible(texte: Any) -> str:
    """Neutralise caractères de contrôle et de mise en forme (séquences ANSI,
    U+202E) avant affichage dans un terminal."""
    return sans_substituts("".join(f"<U+{ord(c):04X}>" if unicodedata.category(c) in ("Cc", "Cf")
                                   and c not in "\n\t" else c for c in str(texte)))


def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible (champs du message neutralisés par visible)."""
    print(f"{res['denominateur']} message(s) examiné(s), {res['messages_suspects']} avec indice(s) "
          f"d'hameçonnage (moteur HTML : {res['moteur']})")
    for msg in res["messages"]:
        ent = msg["entetes"]
        print(f"\n== {visible(msg['nom'])}\n  De : {visible(ent['from'])}\n  À : {visible(ent['to'])}"
              f"\n  Objet : {visible(ent['subject'])}\n  Date : {visible(msg['date']['iso'] or msg['date']['brut'])}")
        for auth in msg["authentification"][:1]:
            print(visible(f"  Authentification ({auth['serveur']}) : {auth['verdicts']}"))
        trajet = msg["trajet"]
        print(visible(f"  Sauts Received : {trajet['nombre_sauts']} ; remis par : {trajet['remis_par']}"))
        for piece in msg["pieces_jointes"]:
            print(f"  Pièce : {visible(piece['nom'])} ({visible(piece['type'])}, {piece['taille']} o) "
                  f"sha256 {piece['sha256'][:16]}…")
        for indice in msg["indices"]:
            print(f"  indice {indice['code']} : {visible(indice['detail'])}")
        for note in msg["notes"]:
            print(f"  note : {visible(note)}")
        if msg["corps_extrait"]:
            print("  Corps : " + visible(msg["corps_extrait"][:300].replace("\n", " ⏎ ")))
    for err in res["erreurs"]:
        print(f"erreur {visible(err['nom'])} : {visible(err['erreur'])}")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Analyse des courriels (.eml, .mbox, Maildir) : en-têtes décodés, origine, "
                    "authentification, pièces jointes, liens trompeurs, indices d'hameçonnage.",
        epilog="Exemples : lire_email.py suspect.eml --json\n           lire_email.py boite.mbox\n"
               "           lire_email.py ~/Maildir/INBOX --json\n"
               "Codes : 0 aucun indice ; 1 indice(s) d'hameçonnage ou message illisible ; "
               "2 entrée invalide ; 3 aucun message à examiner. Aucune requête réseau.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemins", nargs="*", help="fichiers .eml/.mbox ou dossiers (Maildir ou arbre)")
    parseur.add_argument("--texte", help="message brut en ligne (« \\n » littéral accepté)")
    parseur.add_argument("--corps-max", type=int, default=1500, help="caractères de corps rendus (défaut 1500)")
    parseur.add_argument("--html", choices=("auto", "stdlib"), default="auto",
                         help=f"conversion {FORMAT_HTML} : auto (beautifulsoup4 si installée) ou stdlib")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    if not args.chemins and args.texte is None:
        parseur.print_usage(sys.stderr)
        print("erreur : donner un fichier, un dossier ou --texte", file=sys.stderr)
        return 2
    if bs4 is None:
        print("beautifulsoup4 absente : HTML converti par html.parser (stdlib)", file=sys.stderr)
    try:
        res = analyser(args)
    except (ErreurEntree, mailbox.Error) as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"erreur : lecture impossible ({exc.strerror or exc}) : {exc.filename or ''}", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : aucun message trouvé, rien à examiner", file=sys.stderr)
        return 3
    if res["erreurs"] and len(res["erreurs"]) == res["denominateur"]:
        for err in res["erreurs"]:
            print(f"erreur : {err['erreur']}", file=sys.stderr)
        return 2
    res["contrat"] = lire_contrat()
    if args.json:
        print(sans_substituts(json.dumps(res, ensure_ascii=False, indent=2)))
    else:
        imprimer_humain(res)
    return 1 if res["messages_suspects"] or res["erreurs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
