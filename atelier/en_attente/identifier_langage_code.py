"""Un fragment de code collé sans nom de fichier n'a pas d'extension, et l'outil de référence le
devine mal : sur un corpus de fragments réels (40 lignes tirées de fichiers de ce poste, sans
extension), pygments.guess_lexer 2.21.0 a reconnu MESURE_PYGMENTS ; l'analyseur intégré, MESURE_STDLIB
(corpus et chiffres : voir MESURE). Cet outil dit quel langage, avec quels indices, et à quel point il en est sûr.

QUESTION
    Dans quel langage de programmation (ou format de configuration) ce fragment ou ce fichier
    est-il écrit, et avec quelle certitude ?
MESURE
    Pour chaque texte : extension du nom (si fichier), ligne shebang, ligne de mode vim ou emacs,
    puis indices lexicaux pondérés (mots-clés, ponctuation, constructions propres) pour 36
    langages et formats ; TypeScript hérite des indices de JavaScript, C++ et Objective-C de ceux
    de C. Python est confirmé ou infirmé par ast.parse, JSON par json.loads, TOML par tomllib.
    La sortie donne le langage retenu, sa part du score, la certitude, les seconds candidats et
    les indices qui ont décidé. Avec pygments installé, guess_lexer est confronté à chaque texte.
    Corpus de mesure (dans cette session) : fichiers réels trouvés sur ce poste pour 31 langages
    (Go, Ruby, Perl, SQL de PostgreSQL, Tcl, Vim, C, C++, JavaScript, TypeScript, Python…),
    moitié « test » tirée par empreinte du chemin, fragments de 40 lignes sans extension.
HYPOTHÈSES
    Le texte est du code source ou de la configuration en clair (UTF-8 ou compatible) ; un
    fichier mélangeant plusieurs langages (HTML avec script, Markdown avec blocs de code) est
    rangé sous son langage dominant ; une extension dit vrai sauf indice contraire fort.
LIMITES
    Langages reconnus : ceux de la table LANGAGES, et aucun autre (Dart, Elixir, Julia… sont
    rangés sous le plus proche ou déclarés indéterminés). Un fragment de quelques lignes peut
    n'avoir aucun indice : il est alors déclaré indéterminé, pas deviné. Seuls les premiers
    65 536 octets de chaque fichier sont lus.
CONTRE-EXEMPLES
    Un fichier JSON est aussi du Python syntaxiquement valide : ast.parse ne suffit pas, c'est
    pourquoi la confirmation n'est accordée qu'avec des indices propres à Python. Un en-tête .h
    de C pur et un .h de C++ sans template ni std:: sont indiscernables ; une suite de
    déclarations « nom: type; » d'une interface TypeScript peut passer pour du CSS.
INVOCATION
    {outil} {dossier} --json

DOMAINE
    Fragments et fichiers de code d'au moins quelques lignes, dans les langages de la table ;
    tri de dépôts, étiquetage de blocs de code, contrôle d'extensions trompeuses.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys
import textwrap
import tomllib
import warnings
from importlib import metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import pygments
    from pygments.lexers import guess_lexer
    from pygments.util import ClassNotFound
except ImportError:
    pygments = None

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
NOM_TABLE = "LANGAGES"
MESURE_PYGMENTS = "MESURE_PYGMENTS"
MESURE_STDLIB = "MESURE_STDLIB"

LIMITE_OCTETS = 65_536
LIMITE_FICHIERS = 5_000
LIMITE_EXAMINES = 200
LIMITE_PYGMENTS = 16_384
PLAFOND_OCCURRENCES = 50
SEUIL_INDETERMINE = 3.0
PRIOR_EXTENSION = 6.0
PRIOR_EXTENSION_PARTAGEE = 3.0
PRIOR_SHEBANG = 25.0
PRIOR_MODELINE = 25.0
BONUS_CONFIRMATION = 8.0
BONUS_CONFIRMATION_EXTENSION = 25.0
MALUS_INFIRMATION = 6.0
BONUS_JSON = 20.0
HERITAGE_FACTEUR = 0.9
SEUIL_PREUVE_PYTHON = 3.0
LIMITE_FENETRES = 200
# erreurs de syntaxe dues à un fragment coupé, pas à un autre langage
COUPURES_FRAGMENT = ("never closed", "unexpected EOF", "unterminated", "unmatched", "expected an indented block",
                     "does not match", "unindent")
DOSSIERS_IGNORES = frozenset({"__pycache__", "node_modules", ".git", ".hg", ".svn", ".venv", "venv"})

M = re.MULTILINE
I = re.IGNORECASE | re.MULTILINE

# langage -> ((motif, poids, libellé), ...) ; un poids négatif est un contre-indice
INDICES_BRUTS: dict[str, tuple[tuple[str, float, str], ...]] = {
    "Python": (
        (r"^\s*def \w+\(.*\)\s*(->\s*[^:]+)?:\s*(#.*)?$", 4, "def …:"),
        (r"^\s*class \w+(\(.*\))?:\s*$", 4, "class …:"),
        (r"^\s*(from [\w.]+ import [\w., ()*]+|import [\w.]+(\s+as\s+\w+)?(, [\w.]+)*)\s*$", 3, "import"),
        (r"^\s*(elif .*:|except\b.*:|finally:|try:|with .+:)\s*$", 4, "elif/except/with"),
        (r"\bself\.\w+", 2, "self."),
        (r"\b(None|True|False)\b", 1, "None/True/False"),
        (r'^\s*[rbuf]*("""|\'\'\')', 3, "docstring"),
        (r"\blambda\b[\w, ]*:", 3, "lambda"),
        (r"\b__\w+__\b", 2, "__dunder__"),
        (r"^if __name__ == ['\"]__main__['\"]:", 6, "__main__"),
        (r"\bf(\"[^\"\n]*\{|'[^'\n]*\{)", 3, "f-string"),
        (r"\b(not in|is not|is None)\b", 1, "is not/not in"),
        (r"^\s*for \w+(, \w+)* in .+:\s*$", 3, "for … in …:"),
        (r"\br'[^'\n]*'|\br\"[^\"\n]*\"", 2, "r'…'"),
        (r"^\s*(Parameters|Returns|Yields|Raises|Examples|Notes|See Also|Attributes)\n\s*-{3,}\s*$", 5, "numpydoc"),
        (r"^\s*>>> ", 4, ">>> doctest"),
        (r"^\s*(return|raise|yield|assert|pass|continue|break)\b[^;{]*$", 1, "return/raise"),
        (r";\s*$", -0.6, "point-virgule final"),
        (r"^\s*\}\s*$", -1.0, "accolade seule"),
    ),
    "JavaScript": (
        (r"\bfunction\s*\*?\s*\w*\s*\([^)]*\)\s*\{", 2, "function(){"),
        (r"\b(const|let|var)\s+[\w${}\[\], ]+\s*=", 2, "const/let/var"),
        (r"=>", 1, "=>"),
        (r"\bconsole\.\w+\(", 4, "console."),
        (r"\brequire\(\s*['\"][^'\"]+['\"]\s*\)", 4, "require('…')"),
        (r"\bmodule\.exports\b|\bexports\.\w+\s*=", 5, "module.exports"),
        (r"^\s*import\s.+\sfrom\s+['\"]", 3, "import … from"),
        (r"^\s*export\s+(default|const|function|class|async)\b", 2, "export"),
        (r"\s(===|!==)\s", 3, "=== / !=="),
        (r"\b(document|window)\.\w+", 3, "document./window."),
        (r"\bundefined\b", 2, "undefined"),
        (r"\.then\(|\bnew Promise\b|\basync function\b|\bawait\b", 2, "promesses"),
        (r"['\"]use strict['\"]", 5, "use strict"),
        (r"\btypeof\b|\binstanceof\b", 2, "typeof"),
        (r"^\s*/\*\*", 0.5, "/** */"),
        (r"\bthis\.\w+", 1, "this."),
    ),
    "TypeScript": (
        (r"^\s*(export\s+)?(declare\s+)?(interface|type)\s+\w+(<[^>]*>)?\s*(=|\{|extends)", 5, "interface/type"),
        (r"[:<]\s*(string|number|boolean|any|unknown|never)(\[\])?\s*[;,)=>|\]}]|\)\s*:\s*(void|Promise<)", 4, ": string/number"),
        (r"^\s*(export\s+)?declare\s+(module|namespace|const|function|class|global|type|let|var)\b", 5, "declare"),
        (r"\bas\s+(const|string|number|any|unknown)\b", 3, "as const"),
        (r"\bimport\s+type\b|\bexport\s+type\b", 5, "import type"),
        (r"\b(public|private|protected|readonly)\s+\w+\s*[:?(]", 2, "modificateurs"),
        (r"\(\s*\w+\??:\s*[A-Z]\w*(<[^>]*>)?(\[\])?\s*[,)=]", 2, "annotation de type"),
        (r"\bkeyof\b|\binfer\s+\w+|\bextends\s+\w+\s*\?", 5, "keyof/infer"),
        (r"^\s*(readonly\s+)?(?!default\b|case\b|public\b|private\b|protected\b)\w+\??:\s*[\w.<>\[\]|' ]+;\s*$", 2, "champ: type;"),
    ),
    "Java": (
        (r"^\s*package\s+[\w.]+;\s*$", 5, "package …;"),
        (r"^\s*import\s+(static\s+)?[\w.]+(\.\*)?;\s*$", 4, "import …;"),
        (r"\b(public|private|protected)\s+(static\s+)?(final\s+)?(class|interface|enum|void|int|String|boolean|long)\b", 3, "public class/void"),
        (r"\bSystem\.(out|err)\.print", 5, "System.out"),
        (r"\bString\[\]\s+\w+", 3, "String[]"),
        (r"@Override\b", 4, "@Override"),
        (r"\bthrows\s+\w+", 4, "throws"),
        (r"\b(extends|implements)\s+[A-Z]\w*", 2, "extends/implements"),
        (r"\bnew\s+[A-Z]\w*(<[^>]*>)?\(", 1, "new X("),
    ),
    "Kotlin": (
        (r"^\s*(private\s+|override\s+|suspend\s+|inline\s+)*fun\s+(<[^>]*>\s*)?[\w.]+\s*\(", 5, "fun"),
        (r"\bval\s+\w+(\s*:\s*[\w<>?]+)?\s*=", 3, "val"),
        (r"\bdata\s+class\b|\bcompanion\s+object\b|\bwhen\s*\(|\blateinit\b", 5, "data class/when"),
        (r"^\s*package\s+[\w.]+\s*$", 2, "package"),
        (r"\bprintln\(", 2, "println"),
        (r"\?\.|\?:|!!", 2, "?. ?: !!"),
    ),
    "Scala": (
        (r"\bdef\s+\w+(\[[^\]]*\])?(\([^)]*\))*\s*(:\s*[\w\[\], ]+)?\s*=", 5, "def … ="),
        (r"\bcase\s+class\b|\bsealed\s+trait\b|\bcase\s+object\b", 5, "case class"),
        (r"^\s*(object|trait)\s+\w+", 3, "object/trait"),
        (r"^\s*import\s+[\w.]+\.(_|\{[^}]*\})\s*$", 5, "import x._"),
        (r"\bval\s+\w+\s*=", 2, "val"),
        (r"\bimplicit\b|\bextends\s+App\b", 3, "implicit"),
    ),
    "C": (
        (r"^\s*#\s*include\s*<[\w./]+\.h>", 3, "#include <x.h>"),
        (r'^\s*#\s*include\s*"[\w./-]+"', 2, '#include "x"'),
        (r"^\s*#\s*(define|ifdef|ifndef|endif|pragma|undef|elif)\b", 2, "#define"),
        (r"^\s*(static\s+|inline\s+|extern\s+)*(int|void|char|double|float|unsigned|long|short|size_t|struct\s+\w+|[A-Z_]{2,}\w*)\s*\**\s*\w+\s*\([^;{]*\)\s*\{?\s*$", 3, "définition de fonction"),
        (r"\b(printf|fprintf|malloc|calloc|free|sizeof|memcpy|memset|strlen)\s*\(", 2, "printf/malloc"),
        (r"\bNULL\b", 2, "NULL"),
        (r"\w->\w", 1, "->"),
        (r"\b(typedef|unsigned|struct)\b", 1, "typedef/struct"),
        (r"^\s*\}\s*$", 0.3, "accolade seule"),
        (r"^\s*(if|while|for|switch)\s*\(.*\)\s*\{?\s*$", 1, "if (…) {"),
        (r"\b(int|char|double|float|long|short|unsigned|size_t|void|npy_intp|PyObject)\s+\**\w+(\[\w*\])?\s*[=;,]", 2, "déclaration typée"),
        (r"\b(goto|break;|case\s+[\w']+:)", 0.5, "goto/case"),
        (r"/\*|\*/", 0.5, "/* */"),
    ),
    "C++": (
        (r"\bstd::\w+", 5, "std::"),
        (r"#\s*include\s*<(iostream|vector|string|map|memory|algorithm|cmath|cstdlib|cstdio|utility|sstream|iomanip|limits|stdexcept|boost/[\w/.]+)>", 4, "#include <iostream>"),
        (r"\btemplate\s*<", 5, "template<"),
        (r"\bnamespace\s+\w+\s*\{", 3, "namespace {"),
        (r"\busing\s+namespace\s+\w+", 5, "using namespace"),
        (r"^\s*(public|private|protected):\s*$", 4, "public:"),
        (r"\b(cout|cerr)\s*<<|<<\s*(std::)?endl\b", 5, "cout <<"),
        (r"\bnullptr\b|\bconstexpr\b|\bstatic_cast<|\bdecltype\b", 4, "nullptr/constexpr"),
        (r"\bvirtual\b|\bclass\s+\w+\s*(:\s*(public|private)\s+\w+)?\s*\{", 2, "class/virtual"),
        (r"\w::\w+", 1, "::"),
    ),
    "C#": (
        (r"^\s*using\s+(static\s+)?[\w.]+;\s*$", 4, "using …;"),
        (r"^\s*namespace\s+[\w.]+\s*(\{|;)?\s*$", 3, "namespace"),
        (r"\bConsole\.(Write|Read)", 5, "Console."),
        (r"\b(public|private|internal|protected)\s+(static\s+)?(async\s+)?(override\s+|virtual\s+|readonly\s+)?(void|string|int|bool|double|Task|var|object)\b", 3, "public string/void"),
        (r"\{\s*get;\s*(private\s+)?(set;)?\s*\}", 5, "{ get; set; }"),
        (r"\bforeach\s*\(\s*(var|\w+)\s+\w+\s+in\b", 5, "foreach (var x in"),
        (r"^\s*\[[A-Z]\w*(\(.*\))?\]\s*$", 2, "[Attribut]"),
        (r"\bstring\s+\w+\s*[=;,)]", 1, "string x"),
        (r"\bnew\s+[A-Z]\w*(<[^>]*>)?\(", 1, "new X("),
        (r"\b(this|base)\.\w+", 1, "this."),
        (r"^\s*///\s*<(summary|param|returns|remarks)", 5, "/// <summary>"),
        (r"\bSystem\.(Windows|Drawing|Collections|Linq|Text|IO|Threading|ComponentModel)\b", 4, "System.Drawing"),
    ),
    "Objective-C": (
        (r"^\s*@(interface|implementation|end|property|synthesize|protocol|selector)\b", 5, "@interface"),
        (r"^\s*#\s*import\s*[<\"]", 4, "#import"),
        (r"\[\w+\s+\w+(:[^\]]*)?\]", 2, "[obj msg]"),
        (r"\bNS[A-Z]\w+", 3, "NSObject"),
        (r'@"[^"\n]*"', 3, '@"…"'),
    ),
    "Go": (
        (r"^package\s+\w+\s*$", 4, "package x"),
        (r"^import\s+\(\s*$", 5, "import ("),
        (r'^import\s+"[\w/.]+"', 5, 'import "x"'),
        (r"^func\s+(\(\w+\s+\*?[\w\[\]]+\)\s+)?\w+(\[[^\]]*\])?\(", 5, "func"),
        (r":=", 3, ":="),
        (r"\bfmt\.\w+\(", 5, "fmt."),
        (r"\berr\s*!=\s*nil\b", 5, "err != nil"),
        (r"^type\s+\w+(\[[^\]]*\])?\s+(struct|interface)\s*\{", 5, "type … struct"),
        (r"\b(chan|defer|go\s+func)\b", 3, "chan/defer"),
        (r"\bnil\b", 1, "nil"),
        (r"^\s*(if|for|switch|select)\s+[^(\s][^\n]*\{\s*$", 2, "if x {"),
        (r"\w+\s+(string|int|int64|uint8|bool|error|byte|rune|float64|\[\]byte)\s*[,)]", 3, "x string,"),
        (r"\)\s+(\(?[\w*\[\]]+(,\s*[\w*\[\]]+)*\)?)\s*\{\s*$", 1, ") type {"),
    ),
    "Rust": (
        (r"^\s*(pub(\([\w ]+\))?\s+)?(async\s+)?(unsafe\s+)?fn\s+\w+(<[^>]*>)?\s*\(", 5, "fn"),
        (r"\blet\s+mut\b", 5, "let mut"),
        (r"\blet\s+\w+(\s*:\s*[\w<>&\[\]]+)?\s*=", 2, "let"),
        (r"^\s*(pub\s+)?use\s+[\w:]+(::\{[^}]*\}|::\*)?;", 4, "use x::y;"),
        (r"\bimpl(<[^>]*>)?\s+[\w:<>]+(\s+for\s+\w+)?\s*\{", 5, "impl"),
        (r"\b(println|format|vec|panic|assert_eq|assert|eprintln|write|writeln)!\s*[\[(]", 5, "macro!"),
        (r"&mut\s+\w+|&self\b|&'\w+", 5, "&mut/&self"),
        (r"#!?\[(derive|cfg|test|allow|deny|inline)\b", 5, "#[derive]"),
        (r"\b(Some|Ok|Err)\(", 3, "Some/Ok/Err"),
        (r"^\s*(if|while|match|for)\s+[^(\s][^\n]*\{\s*$", 1, "if x {"),
        (r"\w::\w+", 1, "::"),
    ),
    "Swift": (
        (r"^\s*import\s+(Foundation|UIKit|SwiftUI|Cocoa|XCTest)\b", 5, "import Foundation"),
        (r"\bfunc\s+\w+\s*(<[^>]*>)?\([^)]*\)\s*(throws\s+)?->\s*\S+", 4, "func … ->"),
        (r"\bguard\s+let\b|\bif\s+let\b", 5, "guard let"),
        (r"\\\(\w+", 4, "\\(x)"),
        (r"@(objc|IBOutlet|IBAction|State|Published|escaping|MainActor)\b", 5, "@objc"),
        (r"\b(fileprivate|inout|weak\s+var|mutating)\b", 5, "fileprivate/inout"),
        (r"\b(let|var)\s+\w+\s*:\s*[A-Z]\w*[?!]?\s*(=|$)", 2, "let x: Type"),
    ),
    "Ruby": (
        (r"^\s*def\s+(self\.)?[\w?!]+(\(.*\))?\s*$", 3, "def"),
        (r"^\s*end\s*$", 2, "end"),
        (r"^\s*require(_relative)?\s+['\"]", 4, "require '…'"),
        (r"\bputs\b", 4, "puts"),
        (r"\bdo\s*\|[\w, *]+\|", 5, "do |x|"),
        (r"\.(each|map|select|reject|each_with_index|inject)\s*(\{|do\b)", 4, ".each do"),
        (r"^\s*(module|class)\s+[A-Z]\w*(::\w+)*(\s*<\s*[A-Z][\w:]*)?\s*$", 4, "class X < Y"),
        (r"#\{[^}\n]+\}", 4, "#{…}"),
        (r"\battr_(accessor|reader|writer)\b", 5, "attr_accessor"),
        (r"\b(unless|elsif)\b", 2, "unless/elsif"),
        (r"@\w+", 1, "@var"),
        (r"\bnil\b", 1, "nil"),
        (r"\w+\?(\s|$|\))", 1, "méthode?"),
    ),
    "PHP": (
        (r"<\?php", 12, "<?php"),
        (r"\$this->", 5, "$this->"),
        (r"\bfunction\s+\w+\s*\([^)]*\$", 4, "function(… $x"),
        (r"^\s*namespace\s+[\w\\]+;", 5, "namespace x\\y;"),
        (r"^\s*use\s+[\w\\]+\\[\w\\]+;", 4, "use x\\y;"),
        (r"\$\w+\s*=", 2, "$x ="),
        (r"\barray\s*\(\s*['\"$\d)]", 2, "array("),
        (r"->\w+\(", 1, "->m("),
    ),
    "Perl": (
        (r"^\s*use\s+(strict|warnings|vars|constant|utf8|lib|parent|base)\b", 5, "use strict"),
        (r"\bmy\s+[\$@%]\w+", 5, "my $x"),
        (r"^\s*sub\s+\w+\s*\{", 5, "sub x {"),
        (r"\$_\b|@_\b|\$[0-9]\b|\$@", 3, "$_ @_"),
        (r"=~\s*[ms]?/|!~", 4, "=~ /…/"),
        (r"\bqw[(/{\[]", 5, "qw()"),
        (r"^\s*package\s+[\w:]+;", 4, "package X;"),
        (r"->\{|->\[", 3, "->{ }"),
        (r"^=(head\d|pod|cut|item|over|back)\b", 5, "POD"),
        (r"\b(unless|elsif|foreach)\s*\(", 2, "unless ("),
        (r"\$\w+\{", 2, "$h{…}"),
        (r"^\s*use\s+\w+(::\w+)+", 2, "use X::Y"),
    ),
    "Lua": (
        (r"\blocal\s+function\b", 5, "local function"),
        (r"\blocal\s+\w+(\s*,\s*\w+)*\s+=", 4, "local x ="),
        (r"\b(ipairs|pairs)\(", 4, "ipairs/pairs"),
        (r"~=", 4, "~="),
        (r"\belseif\b", 3, "elseif"),
        (r"--\[\[", 4, "--[["),
        (r"\bfunction\s+[\w.:]+\s*\(", 1, "function x("),
        (r"\bthen\s*$", 1, "then"),
        (r"^\s*end\s*$", 1, "end"),
        (r"\bnil\b", 1, "nil"),
    ),
    "R": (
        (r"\w\s*<-\s*\S", 3, "x <- …"),
        (r"\blibrary\(\w+\)|\brequire\(\w+\)", 5, "library(x)"),
        (r"\bc\(", 2, "c("),
        (r"\b(data\.frame|matrix|nrow|ncol|paste0|rep|seq_len|sapply|lapply|rbind|cbind|is\.na)\(", 3, "data.frame/sapply"),
        (r"%>%|%in%|%<>%", 5, "%>% %in%"),
        (r"\b(TRUE|FALSE|NA|NULL)\b", 1, "TRUE/NA"),
        (r"\bfunction\s*\(", 1, "function("),
    ),
    "MATLAB": (
        (r"^\s*function\s+(\[[\w, ~]*\]|\w+)\s*=\s*\w+", 5, "function y = f"),
        (r"^\s*%(?!%)", 2, "% commentaire"),
        (r"^\s*%%", 4, "%% cellule"),
        (r"\b(zeros|ones|size|numel|disp|isempty|length|fprintf|nargin|nargout|repmat|cellfun)\(", 3, "zeros/size"),
        (r"\.\*|\./|\.\^", 2, ".* ./"),
        (r"^\s*end\s*;?\s*$", 1, "end"),
    ),
    "Haskell": (
        (r"^\s*module\s+[\w.]+(\s*\([^)]*\))?\s+where\b", 5, "module … where"),
        (r"^\w+\s+::\s+\S", 4, "f :: type"),
        (r"\s::\s+[^\n]*->", 4, "signature ->"),
        (r"^\s*import\s+(qualified\s+)?[A-Z][\w.]*(\s+as\s+[A-Z]\w*)?\s*(\(.*\))?\s*$", 2, "import Data.X"),
        (r"^\s*(data|newtype)\s+[A-Z]\w*[^=\n]*=|\bderiving\s*\(|^\s*instance\s+[A-Z]", 4, "data/instance"),
        (r"^\s*where\s*$", 1, "where"),
    ),
    "Shell": (
        (r"^\s*(if|elif|while|until)\s+\[\[?\s", 5, "if [ … ]"),
        (r"^\s*(then|fi|done|esac|do)\s*$|;\s*then\s*$|;\s*do\s*$", 4, "then/fi/done"),
        (r"^\s*export\s+\w+=", 4, "export X="),
        (r"^\s*(local\s+)?[A-Za-z_]\w*=(\$\(|\"|'|\$\{|\w)", 2, "X=…"),
        (r"\becho\s+[\"$\w-]", 2, "echo"),
        (r"\$\{\w+(:?[-=?+][^}]*)?\}", 2, "${X}"),
        (r"\$\(", 1, "$("),
        (r"\bset\s+-[euxo]+|\|\|\s*exit\b", 4, "set -e"),
        (r"^\s*(function\s+)?\w+\s*\(\)\s*\{", 4, "f() {"),
        (r"2>&1|>\s*/dev/null|&>", 4, "2>&1"),
        (r"\bcase\s+\S+\s+in\s*$", 5, "case … in"),
    ),
    "PowerShell": (
        (r"\b(Get|Set|New|Remove|Write|Invoke|Test|Import|Export|Start|Stop|Add|Out|Select|Where|ForEach)-[A-Z]\w+", 5, "Verbe-Nom"),
        (r"\bparam\s*\(", 4, "param("),
        (r"\s-(eq|ne|lt|gt|le|ge|like|match|and|or|not|contains)\s", 4, "-eq -ne"),
        (r"\[(string|int|switch|bool|Parameter|CmdletBinding)\b[^\]]*\]", 4, "[string]"),
        (r"\$_\.|\$PSScriptRoot|\$env:|\$PSVersionTable|\$null\b", 5, "$env: $null"),
        (r"^\s*#Requires|<#|#>", 3, "<# #>"),
        (r"\$\w+\s*=", 1, "$x ="),
    ),
    "SQL": (
        (r"^\s*(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+(OR\s+REPLACE\s+)?(TABLE|VIEW|INDEX|FUNCTION|EXTENSION|SCHEMA|TYPE|TRIGGER|PROCEDURE|AGGREGATE|OPERATOR|CAST)|ALTER\s+(TABLE|FUNCTION|EXTENSION|TYPE|OPERATOR)|DROP\s+(TABLE|FUNCTION|VIEW|TYPE|INDEX)|GRANT|REVOKE|COMMENT\s+ON)\b", 4, "SELECT/CREATE"),
        (r"^\s*(select\s.+\sfrom\s|insert\s+into\s+\w+|create\s+(or\s+replace\s+)?(table|view|function|index)\s+[\w.]+\s*\()", 3, "select … from"),
        (r"\bFROM\s+[\w.]+(\s+\w+)?\s+(WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|LIMIT)\b", 3, "FROM … WHERE"),
        (r"(?i:\blanguage\s+(c|sql|plpgsql|internal))\b|\bRETURNS\s+(SETOF\s+)?\w+|\bAS\s+'\$libdir|\$\$\s*;?\s*$", 4, "LANGUAGE/RETURNS"),
        (r"\b(NOT\s+NULL|PRIMARY\s+KEY|REFERENCES|VARCHAR|BIGINT|SERIAL|IMMUTABLE|STRICT|PARALLEL\s+SAFE)\b", 3, "NOT NULL/PRIMARY KEY"),
        (r"^\s*--\s", 1, "-- commentaire"),
    ),
    "HTML": (
        (r"(?i)<!doctype\s+html", 12, "<!DOCTYPE html>"),
        (r"(?i)<(html|head|body|div|span|p|ul|li|table|tr|td|meta|link|title|h[1-6]|br|img|form|input|nav|section)\b[^>]*>", 2, "<div>"),
        (r"(?i)</(div|span|p|a|li|td|html|body|head|ul|table|section)>", 2, "</div>"),
        (r"&(nbsp|amp|lt|gt|quot|#\d+);", 1, "&nbsp;"),
        (r"(?i)<a\s+href=", 2, "<a href>"),
    ),
    "CSS": (
        (r"^\s*(color|background(-color|-image)?|margin(-\w+)?|padding(-\w+)?|font(-\w+)?|border(-\w+)?|width|height|display|position|top|left|right|bottom|text-\w+|line-height|z-index|float|overflow|opacity|cursor|content|transition|transform|flex(-\w+)?|align-items|justify-content|grid(-\w+)?|box-sizing|(min|max)-(width|height)|white-space|vertical-align|outline|list-style)\s*:", 3, "propriété CSS"),
        (r"\b\d*\.?\d+(px|em|rem|vh|vw|pt)\b", 2, "10px"),
        (r"#[0-9a-fA-F]{3,6}\s*[;}!]", 2, "#fff"),
        (r"@(media|import|font-face|keyframes|charset|supports)\b", 4, "@media"),
        (r"!important", 4, "!important"),
        (r"^\s*[.#][\w-]+[^{;]*\{", 2, ".classe {"),
    ),
    "JSON": (
        (r'^\s*"[^"\n]+"\s*:\s*', 2, '"clé":'),
        (r'^\s*"[^"\n]+"\s*:\s*("[^"\n]*"|-?\d[\d.eE+-]*|true|false|null|\[|\{)\s*,?\s*$', 3, '"clé": valeur,'),
        (r"^\s*[{\[]\s*$|^\s*[}\]],?\s*$", 1, "{ ["),
    ),
    "YAML": (
        (r"^---\s*$", 3, "---"),
        (r"^\s*[\w.-]+:\s*$", 1, "clé:"),
        (r"^\s*[\w.-]+:\s+[^{}\[\];=]+$", 1, "clé: valeur"),
        (r"^\s*-\s+[\w\"'][^:]*:\s", 2, "- clé: valeur"),
        (r"^\s*[\w.-]+:\s*[|>][-+]?\s*$", 4, "clé: |"),
        (r"^\s*[\w.-]+:\s+(true|false|null|~|\d+|\"[^\"]*\"|'[^']*')\s*$", 1, "clé: scalaire"),
        (r";\s*$", -1, "point-virgule final"),
    ),
    "TOML": (
        (r"^\s*\[\[[\w.\-]+\]\]\s*$", 5, "[[table]]"),
        (r"^\s*\[[\w.\-\"]+\]\s*$", 3, "[table]"),
        (r'^[\w.\-"]+\s*=\s*("|\[|\{|\d|true\b|false\b)', 2, "clé = valeur"),
    ),
    "XML": (
        (r"^\s*<\?xml", 12, "<?xml"),
        (r"xmlns(:\w+)?=", 5, "xmlns"),
        (r"</[\w:.-]+>", 0.5, "</balise>"),
        (r"<[\w:.-]+(\s+[\w:.-]+=\"[^\"]*\")+\s*/?>", 1, "<balise attr>"),
    ),
    "Markdown": (
        (r"^#{1,6}\s+\S.*\n\s*\n", 2, "# titre"),
        (r"^\s*\|[^|\n]+\|[^|\n]+\|", 2, "| tableau |"),
        (r"\[[^\]\n]+\]\([^)\s]+\)", 3, "[lien](url)"),
        (r"^```", 4, "```"),
        (r"\*\*[^*\n]+\*\*", 2, "**gras**"),
        (r"^\s*>\s", 1, "> citation"),
        (r"`[^`\n]+`", 1, "`code`"),
        (r"^\s*[-*]\s+\S", 0.5, "- puce"),
    ),
    "Dockerfile": (
        (r"^(FROM|RUN|CMD|COPY|ADD|ENTRYPOINT|WORKDIR|ENV|EXPOSE|ARG|LABEL|USER|VOLUME|HEALTHCHECK)\s", 4, "FROM/RUN"),
    ),
    "Makefile": (
        (r"^[\w.%/$(){}-][\w.%/$(){} -]*:(\s+[\w.%/$(){} -]+)?\s*$", 1, "cible:"),
        (r"^\t[@-]?(\$\(\w+\)|echo|cd|rm|mkdir|cp|mv|install|test|python3?|sh|gcc|cc|ln|touch|sed|for|if)\b", 3, "recette tabulée"),
        (r"\$\(\w+\)|\$@|\$<|\$\^", 4, "$(VAR) $@"),
        (r"^\.PHONY\s*:", 6, ".PHONY"),
        (r"^\w+\s*[:?+]?=", 1, "VAR ="),
    ),
    "Vim script": (
        (r"^\s*(let|setlocal|syn(tax)?|hi(ghlight)?|augroup|autocmd|au|command!?|nnoremap|noremap|nmap|endfunction|endif|endwhile|endfor|function!?)\b", 2, "let/syn/endif"),
        (r'^\s*"[^"\n]*$', 2, '" commentaire'),
        (r"\b[gsbwlv]:\w+", 4, "g:var"),
        (r"\b(endfunction|endif|endwhile|endfor)\b", 4, "endfunction"),
    ),
    "Tcl": (
        (r"^\s*proc\s+[\w:]+\s*\{", 5, "proc x {"),
        (r"^\s*set\s+\w+(\(\w+\))?\s+", 3, "set x"),
        (r"^\s*(package\s+require|namespace\s+eval|foreach\s+\w+\s|puts\s|bind\s|pack\s)", 4, "package require"),
        (r"\bexpr\s*\{|\[\w+\s", 2, "expr { [cmd"),
        (r"\$\w+", 0.5, "$x"),
    ),
    "Fortran": (
        (r"(?i)^\s*(program|module|subroutine|end\s+(program|module|subroutine|function|do|if|interface|type))\b", 4, "subroutine/end"),
        (r"(?i)^\s*(integer|real|double\s+precision|character|logical|complex|type\s*\(\w+\))\b[^!\n]*::", 5, "integer ::"),
        (r"(?i)\bimplicit\s+none\b", 5, "implicit none"),
        (r"(?i)^\s*(call|use|contains)\s", 2, "call/use"),
        (r"^\s*!", 1, "! commentaire"),
    ),
    "Emacs Lisp": (
        (r"^\s*\((defun|defvar|defcustom|defmacro|defconst|defgroup|require|provide|setq|eval-when-compile)\b", 5, "(defun"),
        (r"\)\)\)", 2, ")))"),
        (r"^\s*;;", 3, ";; commentaire"),
    ),
}
HERITAGE = MappingProxyType({"TypeScript": "JavaScript", "C++": "C", "Objective-C": "C"})
LANGAGES = tuple(INDICES_BRUTS)
INDICES = MappingProxyType({
    langage: tuple((re.compile(motif, M), poids, libelle) for motif, poids, libelle in indices)
    for langage, indices in INDICES_BRUTS.items()})
del INDICES_BRUTS

EXTENSIONS = MappingProxyType({
    ".py": ("Python",), ".pyw": ("Python",), ".pyi": ("Python",),
    ".js": ("JavaScript",), ".mjs": ("JavaScript",), ".cjs": ("JavaScript",), ".jsx": ("JavaScript",),
    ".ts": ("TypeScript",), ".tsx": ("TypeScript",), ".mts": ("TypeScript",), ".cts": ("TypeScript",),
    ".java": ("Java",), ".kt": ("Kotlin",), ".kts": ("Kotlin",), ".scala": ("Scala",), ".sc": ("Scala",),
    ".c": ("C",), ".h": ("C", "C++", "Objective-C"), ".cpp": ("C++",), ".cc": ("C++",),
    ".cxx": ("C++",), ".hpp": ("C++",), ".hh": ("C++",), ".hxx": ("C++",), ".ipp": ("C++",),
    ".cs": ("C#",), ".m": ("Objective-C", "MATLAB"), ".mm": ("Objective-C",), ".go": ("Go",),
    ".rs": ("Rust",), ".swift": ("Swift",), ".rb": ("Ruby",), ".rake": ("Ruby",),
    ".gemspec": ("Ruby",), ".php": ("PHP",), ".pl": ("Perl",), ".pm": ("Perl",), ".t": ("Perl",),
    ".lua": ("Lua",), ".r": ("R",), ".hs": ("Haskell",), ".lhs": ("Haskell",),
    ".sh": ("Shell",), ".bash": ("Shell",), ".zsh": ("Shell",), ".ksh": ("Shell",),
    ".ps1": ("PowerShell",), ".psm1": ("PowerShell",), ".psd1": ("PowerShell",), ".sql": ("SQL",),
    ".html": ("HTML",), ".htm": ("HTML",), ".xhtml": ("HTML",), ".css": ("CSS",),
    ".json": ("JSON",), ".yaml": ("YAML",), ".yml": ("YAML",), ".toml": ("TOML",),
    ".xml": ("XML",), ".xsd": ("XML",), ".xsl": ("XML",), ".svg": ("XML",), ".plist": ("XML",),
    ".md": ("Markdown",), ".markdown": ("Markdown",), ".mk": ("Makefile",), ".vim": ("Vim script",),
    ".tcl": ("Tcl",), ".tk": ("Tcl",), ".f90": ("Fortran",), ".f95": ("Fortran",),
    ".f03": ("Fortran",), ".f": ("Fortran",), ".for": ("Fortran",), ".el": ("Emacs Lisp",),
})
NOMS_FICHIERS = MappingProxyType({
    "dockerfile": "Dockerfile", "containerfile": "Dockerfile", "makefile": "Makefile",
    "gnumakefile": "Makefile", ".vimrc": "Vim script", "gemfile": "Ruby", "rakefile": "Ruby",
    ".bashrc": "Shell", ".profile": "Shell", ".zshrc": "Shell",
})
INTERPRETES = MappingProxyType({
    "python": "Python", "pypy": "Python", "node": "JavaScript", "nodejs": "JavaScript",
    "deno": "TypeScript", "ts-node": "TypeScript", "ruby": "Ruby", "perl": "Perl", "php": "PHP",
    "sh": "Shell", "bash": "Shell", "zsh": "Shell", "ksh": "Shell", "dash": "Shell", "ash": "Shell",
    "pwsh": "PowerShell", "powershell": "PowerShell", "rscript": "R", "lua": "Lua",
    "luajit": "Lua", "tclsh": "Tcl", "wish": "Tcl", "runhaskell": "Haskell", "runghc": "Haskell",
    "make": "Makefile", "scala": "Scala", "kotlin": "Kotlin", "swift": "Swift",
})
NOMS_MODELINE = MappingProxyType({
    "python": "Python", "ruby": "Ruby", "perl": "Perl", "sh": "Shell", "bash": "Shell",
    "zsh": "Shell", "javascript": "JavaScript", "js": "JavaScript", "typescript": "TypeScript",
    "c": "C", "cpp": "C++", "c++": "C++", "java": "Java", "go": "Go", "rust": "Rust", "lua": "Lua",
    "r": "R", "haskell": "Haskell", "sql": "SQL", "html": "HTML", "css": "CSS", "json": "JSON",
    "yaml": "YAML", "toml": "TOML", "xml": "XML", "markdown": "Markdown", "make": "Makefile",
    "makefile": "Makefile", "vim": "Vim script", "tcl": "Tcl", "fortran": "Fortran",
    "f90": "Fortran", "emacs-lisp": "Emacs Lisp", "elisp": "Emacs Lisp", "php": "PHP",
    "cs": "C#", "objc": "Objective-C", "kotlin": "Kotlin", "scala": "Scala", "swift": "Swift",
    "matlab": "MATLAB", "octave": "MATLAB", "ps1": "PowerShell", "powershell": "PowerShell",
    "dockerfile": "Dockerfile",
})
# noms de lexeurs pygments -> langage de la table
NOMS_PYGMENTS = MappingProxyType({
    "Python": "Python", "Python 2.x": "Python", "Python console session": "Python",
    "JavaScript": "JavaScript", "TypeScript": "TypeScript", "Java": "Java", "Kotlin": "Kotlin",
    "Scala": "Scala", "C": "C", "C++": "C++", "C#": "C#", "Objective-C": "Objective-C",
    "Go": "Go", "Rust": "Rust", "Swift": "Swift", "Ruby": "Ruby", "PHP": "PHP", "Perl": "Perl",
    "Perl6": "Perl", "Lua": "Lua", "S": "R", "R": "R", "Matlab": "MATLAB", "Octave": "MATLAB",
    "Haskell": "Haskell", "Bash": "Shell", "Bash Session": "Shell", "PowerShell": "PowerShell",
    "SQL": "SQL", "PostgreSQL SQL dialect": "SQL", "PL/pgSQL": "SQL", "MySQL": "SQL",
    "Transact-SQL": "SQL", "HTML": "HTML", "CSS": "CSS", "JSON": "JSON", "YAML": "YAML",
    "TOML": "TOML", "XML": "XML", "Markdown": "Markdown", "Docker": "Dockerfile",
    "Makefile": "Makefile", "Base Makefile": "Makefile", "VimL": "Vim script", "Tcl": "Tcl",
    "Fortran": "Fortran", "FortranFixed": "Fortran", "EmacsLisp": "Emacs Lisp",
    "GoogleSQL": "SQL", "SQLite3con": "SQL", "NumPy": "Python", "Shell Session": "Shell",
    "Scilab": "MATLAB", "Arduino": "C++", "Emacs Lisp": "Emacs Lisp",
})
PREFIXES_PYGMENTS = (("CSS+", "CSS"), ("HTML+", "HTML"), ("XML+", "XML"), ("JavaScript+", "JavaScript"))
SHEBANG = re.compile(r"^#!\s*(\S+)(?:\s+(?:-\S+\s+)*(\S+))?")
MODELINE_VIM = re.compile(r"(?:vi|vim|ex)\s*:.*?\b(?:ft|filetype|syntax)\s*=\s*([\w+-]+)")
MODELINE_EMACS = re.compile(r"-\*-\s*(?:.*?\bmode:\s*)?([\w+-]+)\s*(?:;.*?)?-\*-")


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


# --------------------------------------------------------------------------- indices

def scores_lexicaux(texte: str) -> dict[str, tuple[float, list[tuple[str, int, float]]]]:
    """Score de chaque langage : somme pondérée de (1 + ln n) par indice présent n fois."""
    resultat: dict[str, tuple[float, list[tuple[str, int, float]]]] = {}
    for langage, indices in INDICES.items():
        total = 0.0
        details: list[tuple[str, int, float]] = []
        for motif, poids, libelle in indices:
            n = sum(1 for _ in zip(range(PLAFOND_OCCURRENCES), motif.finditer(texte)))
            if n:
                apport = poids * (1 + math.log(n))
                total += apport
                details.append((libelle, n, apport))
        resultat[langage] = (total, details)
    return resultat


def appliquer_heritage(scores: dict[str, float]) -> dict[str, float]:
    """TypeScript reprend une part de JavaScript, C++ et Objective-C une part de C."""
    resultat = dict(scores)
    for enfant, parent in HERITAGE.items():
        resultat[enfant] = scores[enfant] + HERITAGE_FACTEUR * max(scores[parent], 0.0)
    return resultat


def langages_extension(nom: str | None) -> tuple[str, ...]:
    """Langages qu'annonce le nom de fichier (extension ou nom connu)."""
    if not nom:
        return ()
    base = Path(nom).name.lower()
    if base in NOMS_FICHIERS:
        return (NOMS_FICHIERS[base],)
    return EXTENSIONS.get(Path(base).suffix, ())


def langage_shebang(texte: str) -> str | None:
    """Langage de l'interpréteur nommé par la ligne #! (env compris)."""
    m = SHEBANG.match(texte)
    if not m:
        return None
    programme = Path(m[1]).name
    if programme == "env" and m[2]:
        programme = Path(m[2]).name
    programme = re.sub(r"[\d.]+$", "", programme.lower())
    return INTERPRETES.get(programme)


def langage_modeline(texte: str) -> str | None:
    """Langage déclaré par une ligne de mode vim ou emacs (5 premières ou dernières lignes)."""
    lignes = texte.splitlines()
    for ligne in lignes[:5] + lignes[-5:]:
        m = MODELINE_VIM.search(ligne) or MODELINE_EMACS.search(ligne)
        if m and m[1].lower() in NOMS_MODELINE:
            return NOMS_MODELINE[m[1].lower()]
    return None


def confirmer_python(texte: str) -> bool | None:
    """ast.parse : vrai si le texte (ou, pour un fragment court, une fenêtre couvrant au moins
    la moitié de ses lignes) est du Python non vide ; faux si rien ne passe ; None si indécidable."""
    lignes = texte.splitlines()
    if len(lignes) > LIMITE_FENETRES:
        return analyser_python(texte)
    for debut in range(0, min(9, len(lignes))):
        for fin in range(len(lignes), max(debut, len(lignes) // 2 + debut) - 1, -1):
            if fin - debut < max(1, len(lignes) // 2):
                break
            verdict = analyser_python("\n".join(lignes[debut:fin]))
            if verdict:
                return True
    return analyser_python(texte)


def analyser_python(texte: str) -> bool | None:
    """Un essai d'ast.parse (texte désindenté) : vrai, faux, ou None si coupé ou vide."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            arbre = ast.parse(textwrap.dedent(texte))
            return True if arbre.body else None
        except IndentationError:
            return None
        except SyntaxError as exc:
            message = str(exc.msg)
            return None if any(m in message for m in COUPURES_FRAGMENT) else False
        except (ValueError, RecursionError, MemoryError):
            return None


def confirmer_json(texte: str) -> bool:
    """json.loads sur un objet ou un tableau."""
    debut = texte.lstrip()[:1]
    if debut not in ("{", "["):
        return False
    try:
        json.loads(texte)
        return True
    except (ValueError, RecursionError):
        return False


def confirmer_toml(texte: str) -> bool:
    """tomllib.loads réussit et le texte contient au moins une affectation."""
    if not re.search(r"^\s*[\w.\-\"]+\s*=", texte, M):
        return False
    try:
        return bool(tomllib.loads(texte))
    except (tomllib.TOMLDecodeError, ValueError, RecursionError):
        return False


# --------------------------------------------------------------------------- décision

def ajuster(scores: dict[str, float], texte: str, nom: str | None) -> tuple[list[str], set[str]]:
    """Ajoute extension, shebang, mode et confirmations syntaxiques ; rend notes et langages prouvés."""
    notes: list[str] = []
    prouves: set[str] = set()
    par_extension = langages_extension(nom)
    for langage in par_extension:
        scores[langage] += PRIOR_EXTENSION if len(par_extension) == 1 else PRIOR_EXTENSION_PARTAGEE
    if par_extension:
        notes.append("extension : " + "/".join(par_extension))
    for source, langage, bonus in (("shebang", langage_shebang(texte), PRIOR_SHEBANG),
                                   ("ligne de mode", langage_modeline(texte), PRIOR_MODELINE)):
        if langage:
            scores[langage] += bonus
            notes.append(f"{source} : {langage}")
            prouves.add(langage)
    confirmations(scores, texte, "Python" in par_extension, notes, prouves)
    return notes, prouves


def confirmations(scores: dict[str, float], texte: str, extension_python: bool,
                  notes: list[str], prouves: set[str]) -> None:
    """Python par ast.parse (s'il a des indices propres), JSON et TOML par leurs analyseurs."""
    if scores["Python"] >= SEUIL_PREUVE_PYTHON or extension_python:
        valide = confirmer_python(texte)
        if valide:
            scores["Python"] += BONUS_CONFIRMATION_EXTENSION if extension_python else BONUS_CONFIRMATION
            notes.append("Python confirmé par ast.parse")
            prouves.add("Python")
        elif valide is False:
            scores["Python"] -= MALUS_INFIRMATION
            notes.append("Python infirmé par ast.parse")
    if confirmer_json(texte):
        scores["JSON"] += BONUS_JSON
        notes.append("JSON confirmé par json.loads")
        prouves.add("JSON")
    if scores["TOML"] > 0 and confirmer_toml(texte):
        scores["TOML"] += BONUS_CONFIRMATION
        notes.append("TOML confirmé par tomllib")
        prouves.add("TOML")


def certitude(classement: list[tuple[str, float]], prouves: set[str]) -> str:
    """forte, moyenne, faible ou indeterminee, selon l'écart au second et les preuves."""
    premier, score1 = classement[0]
    score2 = classement[1][1] if len(classement) > 1 else 0.0
    if score1 < SEUIL_INDETERMINE:
        return "indeterminee"
    ecart = (score1 - max(score2, 0.0)) / score1
    if premier in prouves or ecart > 0.5:
        return "forte"
    return "moyenne" if ecart > 0.2 else "faible"


def analyser_texte(texte: str, nom: str | None) -> dict[str, Any]:
    """Langage d'un texte (nom de fichier facultatif), candidats et indices décisifs."""
    lexical = scores_lexicaux(texte)
    scores = appliquer_heritage({lang: total for lang, (total, _) in lexical.items()})
    notes, prouves = ajuster(scores, texte, nom)
    classement = sorted(((l, s) for l, s in scores.items() if s > 0), key=lambda x: -x[1])
    if not classement:
        classement = [("indetermine", 0.0)]
    niveau = certitude(classement, prouves)
    positif = sum(s for _, s in classement) or 1.0
    langage = classement[0][0] if niveau != "indeterminee" else None
    par_extension = langages_extension(nom)
    return {
        "langage": langage, "certitude": niveau,
        "part_du_score": round(classement[0][1] / positif, 3),
        "candidats": [{"langage": l, "points": round(s, 2), "part": round(s / positif, 3)}
                      for l, s in classement[:4]],
        "indices": [f"{lib} ×{n}" for lib, n, _ in sorted(lexical.get(classement[0][0], (0, []))[1],
                                                            key=lambda d: -d[2])[:6]],
        "notes": notes,
        "discordance": bool(langage and par_extension and langage not in par_extension and niveau == "forte"),
    }


# --------------------------------------------------------------------------- entrées

def est_binaire(octets: bytes) -> bool:
    """Octet nul, ou plus de 5 % d'octets de contrôle hors tabulations et fins de ligne."""
    if b"\x00" in octets:
        return True
    controles = sum(1 for o in octets if o < 9 or 13 < o < 32 or o == 127)
    return bool(octets) and controles / len(octets) > 0.05


def lire_debut(chemin: Path, limite: int) -> tuple[str | None, str | None]:
    """Texte des premiers octets (lecture bornée) ou raison du refus."""
    try:
        with chemin.open("rb") as flux:
            octets = flux.read(limite)
    except OSError as exc:
        return None, f"illisible : {exc.strerror or exc}"
    if est_binaire(octets[:8192]):
        return None, "binaire"
    texte = octets.decode("utf-8", errors="replace")
    if texte.count("�") > max(3, len(texte) // 100):
        return None, "ni UTF-8 ni texte"
    return texte, None


def parcourir(dossier: Path, limite: int) -> Iterator[Path]:
    """Fichiers d'un dossier (récursif), dossiers cachés et caches ignorés, triés."""
    compte = 0
    for racine, sous, fichiers in os.walk(dossier):
        sous[:] = sorted(s for s in sous if s not in DOSSIERS_IGNORES and not s.startswith("."))
        for nom in sorted(fichiers):
            if compte >= limite:
                return
            chemin = Path(racine) / nom
            if chemin.is_file() and not chemin.is_symlink():
                compte += 1
                yield chemin


def retenir_dans_dossier(chemin: Path) -> bool:
    """Dans un dossier : fichiers à extension ou nom connus, ou sans extension (scripts)."""
    return bool(langages_extension(chemin.name)) or chemin.suffix == ""


def cibles(chemins: list[Path], limite: int) -> tuple[list[tuple[Path, bool]], list[str]]:
    """(chemin, donné explicitement) pour chaque fichier à examiner ; erreurs d'usage."""
    trouves: list[tuple[Path, bool]] = []
    erreurs: list[str] = []
    for chemin in chemins:
        if not chemin.exists():
            erreurs.append(f"introuvable : {chemin}")
        elif chemin.is_dir():
            trouves.extend((f, False) for f in parcourir(chemin, limite) if retenir_dans_dossier(f))
        elif chemin.is_file():
            trouves.append((chemin, True))
        else:
            erreurs.append(f"ni fichier ni dossier : {chemin}")
    return trouves[:limite], erreurs


# --------------------------------------------------------------------------- témoin pygments

def nom_pygments(texte: str) -> str | None:
    """Lexeur que pygments devine sur le contenu seul, ramené aux noms de la table."""
    try:
        lexeur = guess_lexer(texte[:LIMITE_PYGMENTS])
    except (ClassNotFound, ValueError):
        return None
    for prefixe, langage in PREFIXES_PYGMENTS:
        if lexeur.name.startswith(prefixe):
            return langage
    return NOMS_PYGMENTS.get(lexeur.name, lexeur.name)


def comparer_pygments(resultats: list[dict[str, Any]], textes: list[str]) -> dict[str, Any]:
    """guess_lexer sur chaque texte examiné ; accords avec l'analyseur intégré."""
    accords = 0
    for res, texte in zip(resultats, textes):
        devine = nom_pygments(texte)
        res["pygments"] = devine
        accords += devine == res["langage"]
    return {"bibliotheque": f"pygments {metadata.version('pygments')}", "textes": len(textes),
            "accords": accords, "taux_accord": round(accords / len(textes), 3) if textes else None}


# --------------------------------------------------------------------------- orchestration

def examiner(args: argparse.Namespace, racine: Path) -> tuple[dict[str, Any], list[str]]:
    """Cœur : lit, analyse, compare ; rend la sortie et les erreurs d'usage."""
    resultats: list[dict[str, Any]] = []
    textes: list[str] = []
    ignores: list[dict[str, str]] = []
    chemins = [p if p.is_absolute() else racine / p for p in args.chemins]
    trouves, erreurs = cibles(chemins, args.max_fichiers)
    if args.texte is not None:
        resultats.append({"source": "--texte", **analyser_texte(args.texte, args.nom)})
        textes.append(args.texte)
    for chemin, explicite in trouves:
        texte, refus = lire_debut(chemin, args.max_octets)
        if texte is None:
            ignores.append({"chemin": str(chemin), "raison": refus or ""})
            if explicite:
                erreurs.append(f"{chemin} : {refus}")
            continue
        resultats.append({"source": str(chemin), **analyser_texte(texte, chemin.name)})
        textes.append(texte)
    return construire_sortie(args, resultats, textes, ignores), erreurs


def construire_sortie(args: argparse.Namespace, resultats: list[dict[str, Any]], textes: list[str],
                      ignores: list[dict[str, str]]) -> dict[str, Any]:
    """Objet de sortie : dénominateur, répartition, résultats, comparaison."""
    repartition: dict[str, int] = {}
    for res in resultats:
        cle = res["langage"] or "indetermine"
        repartition[cle] = repartition.get(cle, 0) + 1
    sortie: dict[str, Any] = {
        "moteur": "stdlib", "denominateur": len(resultats),
        "examines": [r["source"] for r in resultats[:LIMITE_EXAMINES]],
        "examines_tronques": len(resultats) > LIMITE_EXAMINES,
        "repartition": dict(sorted(repartition.items(), key=lambda kv: -kv[1])),
        "discordances": [r["source"] for r in resultats if r["discordance"]],
        "ignores": ignores[:LIMITE_EXAMINES], "nombre_ignores": len(ignores),
        "resultats": resultats, "comparaison": None, "langages_reconnus": list(LANGAGES)}
    if pygments is not None and args.moteur != "stdlib" and textes:
        sortie["comparaison"] = comparer_pygments(resultats, textes)
    return sortie


def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible."""
    for r in res["resultats"]:
        candidats = ", ".join(f"{c['langage']} {c['part']:.0%}" for c in r["candidats"][1:3])
        marque = "  DISCORDANCE avec l'extension" if r["discordance"] else ""
        print(f"{r['source']} : {r['langage'] or 'indéterminé'} ({r['certitude']}, "
              f"{r['part_du_score']:.0%}){marque}" + (f" ; ensuite {candidats}" if candidats else ""))
        if r["indices"]:
            print(f"    indices : {', '.join(r['indices'])}")
        if "pygments" in r:
            print(f"    pygments : {r['pygments']}")
    print(f"{res['denominateur']} texte(s) examiné(s), {res['nombre_ignores']} ignoré(s) ; "
          + ", ".join(f"{k} {v}" for k, v in res["repartition"].items()))
    if res["comparaison"]:
        c = res["comparaison"]
        print(f"{c['bibliotheque']} : {c['accords']}/{c['textes']} accords")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Identifie le langage de programmation d'un fichier, d'un dossier de fichiers "
                    "ou d'un fragment de texte, avec certitude, seconds candidats et indices.",
        epilog="Exemples : identifier_langage_code.py src/ --json\n"
               "           identifier_langage_code.py --texte 'fn main() { println!(\"x\"); }'\n"
               "Codes : 0 rien à signaler ; 1 extension contredite par le contenu ; "
               "2 entrée invalide ; 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemins", nargs="*", type=Path, help="fichiers ou dossiers à examiner")
    parseur.add_argument("--texte", help="fragment de code à identifier (sans nom de fichier)")
    parseur.add_argument("--nom", help="nom de fichier supposé pour --texte (son extension compte)")
    parseur.add_argument("--max-octets", type=int, default=LIMITE_OCTETS, help="octets lus par fichier")
    parseur.add_argument("--max-fichiers", type=int, default=LIMITE_FICHIERS, help="fichiers examinés au plus")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : confronter à pygments s'il est installé ; stdlib : jamais")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    if pygments is None:
        print("pygments absente : identification par l'analyseur intégré (stdlib), sans comparaison",
              file=sys.stderr)
    if args.max_octets < 1 or args.max_fichiers < 1:
        print("erreur : --max-octets et --max-fichiers doivent être positifs", file=sys.stderr)
        return 2
    racine = args.racine if args.racine is not None else Path.cwd()
    res, erreurs = examiner(args, racine)
    for erreur in erreurs:
        print(f"erreur : {erreur}", file=sys.stderr)
    if res["denominateur"] == 0:
        print("dénominateur nul : rien à examiner (aucun fichier texte ni --texte)", file=sys.stderr)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 2 if erreurs else 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        imprimer_humain(res)
    if erreurs:
        return 2
    return 1 if res["discordances"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
