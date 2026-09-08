"""
QUESTION      où est ce symbole, et cette citation existe-t-elle vraiment ?
MESURE        index SQLite FTS5 construit par os.walk ; empreinte sha256 par fichier ; plus proche voisin par difflib quand la citation échoue
HYPOTHÈSES    l'arbre tient sur le disque local et n'a pas changé depuis l'indexation
LIMITES       un index PÉRIMÉ répond faux sans le dire -- d'où l'empreinte ; FTS5 tokenise, donc une recherche de ponctuation échoue ; os.walk ne suit pas les liens par défaut
CONTRE-EXEMPLE 37 lignes citées introuvables sur 39 : un « introuvable » sans plus proche voisin se lit comme « ce code n'existe pas »
DOMAINE       un arbre de fichiers texte, sur cette machine
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Reconfigure stdout and stderr to UTF‑8 when possible
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent
ÉLAGUÉS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
}
TAILLE_BLOC = 8192
TAILLE_MAX_SNIPPET = 32


def empreinte_fichier(chemin: Path) -> str:
    """Calcule l'empreinte SHA256 d'un fichier en streaming."""
    with chemin.open("rb") as f:
        # hashlib.file_digest signature : (fileobj, digest, /, *, _bufsize=...)
        return hashlib.file_digest(f, "sha256").hexdigest()


def est_texte(chemin: Path) -> bool:
    """Vérifie si un fichier est du texte (pas de nul dans les 8 premiers Ko)."""
    try:
        with chemin.open("rb") as f:
            return b"\0" not in f.read(TAILLE_BLOC)
    except OSError:
        return False


def parcourir_arbre(racine: Path, erreurs: List[OSError]) -> Tuple[List[Path], int]:
    """Parcourt l'arbre en élaguant les dossiers cachés et rapporte les erreurs."""
    chemins: List[Path] = []
    for répertoire, dirs, fichiers in os.walk(racine, topdown=True, onerror=erreurs.append):
        # élague en place
        dirs[:] = [d for d in dirs if d not in ÉLAGUÉS]
        fichiers.sort()
        for fichier in fichiers:
            chemin = Path(répertoire) / fichier
            if est_texte(chemin):
                chemins.append(chemin)
    chemins.sort()
    return chemins, len(erreurs)


def créer_index(connexion: sqlite3.Connection) -> None:
    """Crée les tables si elles n'existent pas."""
    connexion.execute(
        """
        CREATE TABLE IF NOT EXISTS documents(
            chemin TEXT PRIMARY KEY,
            empreinte TEXT,
            taille INTEGER,
            mtime REAL
        )
        """
    )
    connexion.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS fragments USING fts5(
            chemin UNINDEXED,
            contenu,
            tokenize="trigram"
        )
        """
    )
    connexion.execute(
        """
        CREATE TABLE IF NOT EXISTS fts5_rank(
            rowid INTEGER PRIMARY KEY,
            rank REAL
        )
        """
    )
    connexion.execute(
        """
        CREATE TRIGGER IF NOT EXISTS fragments_after_insert
        AFTER INSERT ON fragments
        BEGIN
            INSERT INTO fts5_rank(rowid, rank)
            VALUES (new.rowid, bm25(fragments));
        END
        """
    )


def indexer_fichier(connexion: sqlite3.Connection, chemin: Path) -> bool:
    """Indexe un fichier si son empreinte a changé. Retourne True si réindexé."""
    stat = chemin.stat()
    curseur = connexion.execute(
        "SELECT empreinte FROM documents WHERE chemin = ?", (str(chemin),)
    )
    ancienne = curseur.fetchone()
    nouvelle_empreinte = empreinte_fichier(chemin)

    if ancienne and ancienne[0] == nouvelle_empreinte:
        return False

    with chemin.open("r", encoding="utf-8", errors="replace") as f:
        contenu = f.read()

    # Gestion du conflit d'empreinte avec INSERT OR REPLACE
    connexion.execute(
        """
        INSERT OR REPLACE INTO documents (chemin, empreinte, taille, mtime)
        VALUES (?, ?, ?, ?)
        """,
        (str(chemin), nouvelle_empreinte, stat.st_size, stat.st_mtime),
    )
    # Pour la table virtuelle FTS5, on utilise également INSERT OR REPLACE
    connexion.execute(
        """
        INSERT OR REPLACE INTO fragments (chemin, contenu)
        VALUES (?, ?)
        """,
        (str(chemin), contenu),
    )
    return True


def indexer_arbre(racine: Path, index: Path, *, forcer: bool = False) -> Dict[str, Any]:
    """Indexe un arbre de fichiers texte dans une base SQLite FTS5."""
    erreurs: List[OSError] = []
    chemins, non_lus = parcourir_arbre(racine, erreurs)
    if non_lus > 0 and not forcer:
        raise RuntimeError(f"{non_lus} chemins non lus (utilisez --forcer pour ignorer)")

    connexion = sqlite3.connect(index)
    créer_index(connexion)

    réindexés = 0
    connexion.execute("BEGIN")
    try:
        for chemin in chemins:
            if indexer_fichier(connexion, chemin):
                réindexés += 1
        connexion.commit()
    except Exception:
        connexion.rollback()
        raise
    finally:
        connexion.close()

    return {
        "documents": len(chemins),
        "réindexés": réindexés,
        "non_lus": non_lus,
        "erreurs": [str(e) for e in erreurs],
        "denominateur": len(chemins),
    }


def chercher(
    index: Path,
    terme: str,
    *,
    limite: int = 10,
    extension: Optional[str] = None,
) -> Dict[str, Any]:
    """Cherche un terme dans l'index et retourne les extraits."""
    assert TAILLE_MAX_SNIPPET <= 64, "TAILLE_MAX_SNIPPET must be <= 64"
    connexion = sqlite3.connect(index)

    if extension:
        requête = """
            SELECT chemin, snippet(fragments, -1, '[', ']', '…', ?)
            FROM fragments
            JOIN documents ON fragments.chemin = documents.chemin
            JOIN fts5_rank ON fragments.rowid = fts5_rank.rowid
            WHERE contenu MATCH ? AND documents.chemin LIKE ?
            ORDER BY fts5_rank.rank
            LIMIT ?
        """
        params = (TAILLE_MAX_SNIPPET, terme, f"%{extension}", limite)
    else:
        requête = """
            SELECT chemin, snippet(fragments, -1, '[', ']', '…', ?)
            FROM fragments
            JOIN documents ON fragments.chemin = documents.chemin
            JOIN fts5_rank ON fragments.rowid = fts5_rank.rowid
            WHERE contenu MATCH ?
            ORDER BY fts5_rank.rank
            LIMIT ?
        """
        params = (TAILLE_MAX_SNIPPET, terme, limite)

    résultats = connexion.execute(requête, params).fetchall()
    connexion.close()
    return {
        "résultats": [{"chemin": r[0], "extrait": r[1]} for r in résultats],
        "denominateur": len(résultats),
    }


def citation(index: Path, fragment: str) -> Dict[str, Any]:
    """Vérifie si un fragment existe exactement dans l'index."""
    connexion = sqlite3.connect(index)
    phrase = f'"{fragment}"'
    curseur = connexion.execute(
        "SELECT chemin, offsets(contenu) FROM fragments WHERE contenu MATCH ?",
        (phrase,),
    )
    correspondance = curseur.fetchone()
    connexion.close()

    if correspondance:
        chemin, offsets = correspondance
        if not offsets:
            return {"trouvé": False, "denominateur": 1}
        offset = int(offsets.split()[1])
        with open(chemin, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if offset <= len(line):
                    return {"trouvé": True, "chemin": chemin, "ligne": i, "denominateur": 1}
                offset -= len(line)
        return {"trouvé": False, "denominateur": 1}

    # Recherche du plus proche voisin
    connexion = sqlite3.connect(index)
    tous_fragments = connexion.execute("SELECT contenu FROM fragments").fetchall()
    connexion.close()

    possibilités = [f[0] for f in tous_fragments]
    proches = difflib.get_close_matches(fragment, possibilités, n=1, cutoff=0.6)
    if proches:
        return {
            "trouvé": False,
            "proche": proches[0],
            "taux": difflib.SequenceMatcher(None, fragment, proches[0]).ratio(),
            "denominateur": 1,
        }
    return {"trouvé": False, "denominateur": 1}


def vérifier(index: Path) -> Dict[str, Any]:
    """Vérifie l'intégrité de l'index en relisant un échantillon."""
    connexion = sqlite3.connect(index)
    curseur = connexion.execute("SELECT chemin FROM documents")
    chemins = [r[0] for r in curseur.fetchall()]
    connexion.close()

    erreurs: List[str] = []
    échantillon = chemins[:10]  # échantillon de 10 fichiers
    for chemin in échantillon:
        try:
            actuelle_empreinte = empreinte_fichier(Path(chemin))
            connexion = sqlite3.connect(index)
            stockée_empreinte = connexion.execute(
                "SELECT empreinte FROM documents WHERE chemin = ?", (chemin,)
            ).fetchone()[0]
            connexion.close()
            if actuelle_empreinte != stockée_empreinte:
                erreurs.append(chemin)
        except Exception as e:
            erreurs.append(f"{chemin}: {e}")

    return {
        "vérifiés": len(chemins),
        "erreurs": erreurs,
        "denominateur": len(échantillon),
    }


def comparer(index_a: Path, index_b: Path) -> Dict[str, Any]:
    """Compare deux index et retourne les différences."""
    def _lire_index(index: Path) -> Set[Tuple[str, str]]:
        connexion = sqlite3.connect(index)
        curseur = connexion.execute("SELECT chemin, empreinte FROM documents")
        entrées = {(r[0], r[1]) for r in curseur.fetchall()}
        connexion.close()
        return entrées

    a = _lire_index(index_a)
    b = _lire_index(index_b)

    communs = a & b
    propres_a = a - b
    propres_b = b - a
    divergents = {
        (c, "a")
        for c, e in a
        if any(c == c2 and e != e2 for c2, e2 in b)
    } | {
        (c, "b")
        for c, e in b
        if any(c == c2 and e != e2 for c2, e2 in a)
    }

    return {
        "a": len(a),
        "b": len(b),
        "communs": len(communs),
        "propres_a": len(propres_a),
        "propres_b": len(propres_b),
        "divergents": sorted(divergents),
        "denominateur": len(a) + len(b),
    }


def main() -> int:
    import argparse

    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine de l'arbre pour les chemins relatifs",
    )
    parent.add_argument(
        "--index",
        type=Path,
        default=argparse.SUPPRESS,
        help="Chemin de l'index SQLite",
    )
    parent.add_argument("--json", action="store_true", help="Sortie JSON sur stdout")

    parser = argparse.ArgumentParser(
        description="Indexer et chercher dans un arbre de fichiers texte."
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    indexer = sous_parsers.add_parser("indexer", parents=[parent], conflict_handler="error")
    indexer.add_argument("racine", type=Path, help="Racine de l'arbre à indexer")
    indexer.add_argument(
        "--forcer", action="store_true", help="Forcer l'indexation même si des chemins sont illisibles"
    )

    chercher_parser = sous_parsers.add_parser("chercher", parents=[parent], conflict_handler="error")
    chercher_parser.add_argument("terme", help="Terme à chercher")
    chercher_parser.add_argument(
        "--limite", type=int, default=10, help="Nombre maximal de résultats"
    )
    chercher_parser.add_argument("--extension", help="Filtrer par extension de fichier")

    citation_parser = sous_parsers.add_parser("citation", parents=[parent], conflict_handler="error")
    citation_parser.add_argument("fragment", help="Fragment à vérifier")

    verifier_parser = sous_parsers.add_parser("verifier", parents=[parent], conflict_handler="error")

    comparer_parser = sous_parsers.add_parser("comparer", parents=[parent], conflict_handler="error")
    comparer_parser.add_argument("index_a", type=Path, help="Premier index à comparer")
    comparer_parser.add_argument("index_b", type=Path, help="Second index à comparer")

    args = parser.parse_args()

    # Fusion explicite des espaces de noms (correction du bug argparse parents=)
    if not hasattr(args, "index"):
        index = RACINE / "index.db"
    else:
        index = args.index
    assert isinstance(index, Path), "L'index doit être un objet Path"

    try:
        if args.commande == "indexer":
            résultat = indexer_arbre(args.racine, index, forcer=args.forcer)
        elif args.commande == "chercher":
            résultat = chercher(index, args.terme, limite=args.limite, extension=args.extension)
        elif args.commande == "citation":
            résultat = citation(index, args.fragment)
        elif args.commande == "verifier":
            résultat = vérifier(index)
        elif args.commande == "comparer":
            résultat = comparer(args.index_a, args.index_b)
        else:
            print("Commande inconnue", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        return 2

    if args.json:
        if résultat.get("denominateur", 0) == 0:
            print("Dénominateur nul : refus de conclure.", file=sys.stderr)
            return 3
        résultat["contrat"] = {
            "QUESTION": "où est ce symbole, et cette citation existe-t-elle vraiment ?",
            "MESURE": "index SQLite FTS5 construit par os.walk ; empreinte sha256 par fichier ; plus proche voisin par difflib quand la citation échoue",
            "HYPOTHÈSES": "l'arbre tient sur le disque local et n'a pas changé depuis l'indexation",
            "LIMITES": "un index PÉRIMÉ répond faux sans le dire -- d'où l'empreinte ; FTS5 tokenise, donc une recherche de ponctuation échoue ; os.walk ne suit pas les liens par défaut",
            "CONTRE-EXEMPLE": "37 lignes citées introuvables sur 39 : un « introuvable » sans plus proche voisin se lit comme « ce code n'existe pas »",
            "DOMAINE": "un arbre de fichiers texte, sur cette machine",
        }
        print(json.dumps(résultat, ensure_ascii=False, indent=2))
    else:
        if args.commande == "indexer":
            print(f"Indexé {résultat['documents']} documents ({résultat['réindexés']} réindexés)")
            if résultat["non_lus"] > 0:
                print(f"Avertissement: {résultat['non_lus']} chemins non lus", file=sys.stderr)
        elif args.commande == "chercher":
            for r in résultat["résultats"]:
                print(f"{r['chemin']}\n{r['extrait']}\n")
        elif args.commande == "citation":
            if résultat.get("trouvé"):
                print(f"Trouvé dans {résultat['chemin']} à la ligne {résultat['ligne']}")
            else:
                if "proche" in résultat:
                    print(f"Introuvable. Proche: {résultat['proche']} (taux: {résultat['taux']:.2f})")
                else:
                    print("Introuvable.")
        elif args.commande == "verifier":
            if résultat["erreurs"]:
                print(f"Erreurs détectées: {résultat['erreurs']}", file=sys.stderr)
                return 3
            print(f"Vérifié {résultat['vérifiés']} documents sans erreur")
        elif args.commande == "comparer":
            print(f"Index A: {résultat['a']} documents")
            print(f"Index B: {résultat['b']} documents")
            print(f"Communs: {résultat['communs']}")
            print(f"Propres à A: {résultat['propres_a']}")
            print(f"Propres à B: {résultat['propres_b']}")
            if résultat["divergents"]:
                print("Divergents:", file=sys.stderr)
                for d in résultat["divergents"]:
                    print(f"  {d[0]} (dans {'A' if d[1] == 'a' else 'B'})", file=sys.stderr)
                return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())