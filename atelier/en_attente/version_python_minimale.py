"""Un agent écrit volontiers du Python 3.12 pour un serveur resté en 3.9, et
l'échec n'apparaît qu'au déploiement. Mesuré dans cette session :
sur 56 extraits datés par la doc officielle et, de 3.8 à 3.14, par compilation
réelle (CPython 3.8.20 à 3.14.7), ast.parse(feature_version=(3, 7)) accepte 18
des 36 constructions postérieures à 3.7, et vermin 1.8.0 n'en date juste que 29.

QUESTION
    Quelle version minimale de Python ce code exige-t-il, et à cause de quelle
    ligne ? À partir de quelle version cesse-t-il de fonctionner ?
MESURE
    Par fichier puis globalement : (1) constructions syntaxiques repérées dans
    l'arbre ast et dans le flux tokenize (morse 3.8, positionnels seuls 3.8,
    match 3.10, except* 3.11, paramètres de type et instruction type 3.12,
    f-strings PEP 701 3.12, défauts de paramètres de type 3.13, t-strings et
    except sans parenthèses 3.14…) ; (2) modules et objets de la stdlib
    importés ou lus par attribut, confrontés à une table embarquée ; (3) plancher
    grammatical rendu par ast.parse(feature_version=(3, x)) pour x de 7 à 14.
    Chaque exigence porte sa version, sa raison, sa ligne et sa vérification.
    Les usages placés sous `if sys.version_info >= …`, sous un bloc if
    TYPE_CHECKING, sous try/except ImportError ou dans une annotation différée sont rapportés à
    part (exigences gardées). Les retraits de la stdlib (imp, distutils,
    ast.Num…) donnent la version à partir de laquelle le code casse.
    Si vermin est installé, son minimum est rapporté à côté, pour comparaison.
HYPOTHÈSES
    L'outil tourne sous CPython 3.14 (tokenize 3.12+ découpe les f-strings).
    Le code est du Python 3 que la grammaire 3.14 accepte. Les noms importés
    désignent la stdlib (pas un module local homonyme). Table stdlib : extraite
    des directives versionadded de la doc officielle (branche 3.14) ; pour les
    versions 3.9 à 3.14, chaque entrée a été confirmée par essai réel (absente
    sous CPython 3.(v-1), présente sous 3.v, de 3.8.20 à 3.14.7, Linux) ; les
    entrées 3.5 à 3.8 sont documentaires et présentes sous 3.8.20.
LIMITES
    Les méthodes d'objets (str.removeprefix 3.9, int.bit_count 3.10…) et les
    nouveaux paramètres de fonctions existantes ne sont pas vus ; les objets
    propres à Windows ou macOS sont absents de la table ; les rétroportages
    (sys.set_int_max_str_digits existe en 3.10.20) sont exclus de la table, pas
    signalés. Un code valide seulement avant 3.14 (async comme identifiant,
    print sans parenthèses) est déclaré non analysable. Une garde de version
    écrite autrement (fonction, variable intermédiaire) n'est pas reconnue.
CONTRE-EXEMPLES
    `return s.removeprefix("v")` rend « Python >= 3.0 » alors que CPython
    3.8.20 lève AttributeError (méthode de str ajoutée en 3.9, invisible ici).
    `with (open(a) as f, open(b) as g):` rend 3.10 (doc), or CPython 3.9.25
    le compile déjà : l'outil surestime pour 3.9.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Sources Python 3 (fichiers ou dossiers de .py) ; versions 3.5 à 3.14 de
    CPython ; décision de compatibilité avant déploiement ou en CI (--cible).
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import sys
import tokenize
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import vermin  # bibliothèque tierce optionnelle, pour comparaison seulement
except ImportError:
    vermin = None

RACINE = Path(__file__).resolve().parent

SECTION_HYPOTHESES = "HYPOTHÈSES"
SECTION_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", SECTION_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", SECTION_INVOCATION, "DOMAINE")

TAILLE_MAX_DEFAUT = 5_000_000
MAX_FICHIERS_DEFAUT = 20_000
MAX_EXAMINES = 200
DOSSIERS_IGNORES = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".tox", ".nox", ".venv", "venv",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
EXCEPTIONS_GARDE = frozenset({
    "ImportError", "ModuleNotFoundError", "AttributeError", "Exception",
    "BaseException", "NameError",
})
MINEUR_MAX = 14
GARDE_TYPAGE = "TYPE_CHECKING"

# (code, version, intitulé, vérification) — chaque version a été confirmée
# par la doc officielle (whatsnew) et/ou un essai réel sur CPython 3.8.20 à 3.14.7.
REGLES_SYNTAXE: tuple[tuple[str, tuple[int, int], str, str], ...] = (
    ("async", (3, 5), "async/await (PEP 492)", "doc whatsnew 3.5"),
    ("matmul", (3, 5), "opérateur @ (PEP 465)", "doc whatsnew 3.5"),
    ("depaquetage", (3, 5), "dépaquetages généralisés * et ** (PEP 448)",
     "doc whatsnew 3.5"),
    ("generator_stop", (3, 5), "from __future__ import generator_stop (PEP 479)",
     "doc whatsnew 3.5"),
    ("fstring", (3, 6), "f-string (PEP 498)", "doc whatsnew 3.6"),
    ("annotation_variable", (3, 6), "annotation de variable (PEP 526)",
     "doc whatsnew 3.6"),
    ("souligne_numerique", (3, 6), "souligné dans un littéral numérique (PEP 515)",
     "doc whatsnew 3.6"),
    ("generateur_async", (3, 6), "générateur asynchrone (PEP 525)",
     "doc whatsnew 3.6"),
    ("comprehension_async", (3, 6), "compréhension asynchrone (PEP 530)",
     "doc whatsnew 3.6"),
    ("future_annotations", (3, 7), "from __future__ import annotations (PEP 563)",
     "doc whatsnew 3.7"),
    ("morse", (3, 8), "expression d'affectation := (PEP 572)", "doc whatsnew 3.8"),
    ("positionnels_seuls", (3, 8), "paramètres positionnels seuls / (PEP 570)",
     "doc whatsnew 3.8"),
    ("fstring_egal", (3, 8), "spécificateur = dans une f-string",
     "doc whatsnew 3.8"),
    ("etoile_return", (3, 8), "return/yield *x sans parenthèses",
     "doc whatsnew 3.8"),
    ("continue_finally", (3, 8), "continue dans un bloc finally",
     "doc whatsnew 3.8"),
    ("decorateur_libre", (3, 9), "décorateur fait d'une expression quelconque (PEP 614)",
     "doc whatsnew 3.9 ; essai : 3.8.20 refuse, 3.9.25 accepte"),
    ("etoile_for", (3, 9), "for x in *a, *b sans parenthèses",
     "essai : 3.8.20 refuse, 3.9.25 accepte"),
    ("with_parentheses", (3, 10), "gestionnaires de contexte entre parenthèses",
     "doc whatsnew 3.10 ; essai : CPython 3.9.25 l'accepte déjà de fait"),
    ("morse_nu", (3, 10), ":= sans parenthèses dans un ensemble ou un indice",
     "doc whatsnew 3.10 ; essai : 3.9.25 accepte l'ensemble, refuse l'indice"),
    ("match", (3, 10), "match/case (PEP 634)", "doc whatsnew 3.10 ; essai 3.9.25/3.10.20"),
    ("except_etoile", (3, 11), "except* (PEP 654)", "doc whatsnew 3.11 ; essai 3.10.20/3.11.15"),
    ("etoile_indice", (3, 11), "* dans un indice ou une annotation *args (PEP 646)",
     "doc whatsnew 3.11 ; essai 3.10.20/3.11.15"),
    ("parametres_type", (3, 12), "paramètres de type [T] (PEP 695)",
     "doc whatsnew 3.12 ; essai 3.11.15/3.12.3"),
    ("instruction_type", (3, 12), "instruction type X = … (PEP 695)",
     "doc whatsnew 3.12 ; essai 3.11.15/3.12.3"),
    ("fstring_701", (3, 12), "f-string hors grammaire d'avant 3.12 (PEP 701)",
     "doc whatsnew 3.12 ; essai 3.11.15/3.12.3"),
    ("defaut_type", (3, 13), "valeur par défaut d'un paramètre de type (PEP 696)",
     "doc whatsnew 3.13 ; essai 3.12.3/3.13.14"),
    ("tstring", (3, 14), "t-string (PEP 750)", "doc whatsnew 3.14 ; essai 3.13.14/3.14.7"),
    ("except_sans_parentheses", (3, 14), "except A, B sans parenthèses (PEP 758)",
     "doc whatsnew 3.14 ; essai 3.13.14/3.14.7"),
)

VERIFICATION_RETRAITS = ("doc whatsnew + essai : présent sous 3.(v-1), absent sous 3.v "
                         "(msilib, nis, lib2to3, tkinter.tix : doc seule)")

# Tables vérifiées dans la session de construction : directives versionadded de la doc
# officielle (branche 3.14) croisées avec un essai réel d'import/getattr sous CPython
# 3.8.20, 3.9.25, 3.10.20, 3.11.15, 3.12.3, 3.13.14 et 3.14.7 (Linux).
AJOUTS_STDLIB: tuple[tuple[str, str], ...] = (
    ("3.5", (
        "typing zipapp "
    )),
    ("3.6", (
        "builtins.ModuleNotFoundError cmath.inf cmath.infj cmath.nan "
        "cmath.nanj cmath.tau collections.abc.AsyncGenerator "
        "collections.abc.Collection collections.abc.Reversible "
        "contextlib.AbstractContextManager hashlib.scrypt "
        "inspect.CO_ASYNC_GENERATOR inspect.isasyncgen "
        "inspect.isasyncgenfunction math.tau os.PathLike os.fspath "
        "pkgutil.ModuleInfo random.choices readline.set_auto_history secrets "
        "socket.AF_ALG ssl.AlertDescription ssl.OP_NO_TICKET ssl.PROTOCOL_TLS "
        "ssl.PROTOCOL_TLS_CLIENT ssl.PROTOCOL_TLS_SERVER ssl.SSLErrorNumber "
        "ssl.VerifyFlags ssl.VerifyMode statistics.harmonic_mean "
        "sys.getfilesystemencodeerrors tkinter.EventType "
        "tracemalloc.DomainFilter types.AsyncGeneratorType typing.Collection "
        "zlib.Z_BLOCK zlib.Z_FIXED zlib.Z_NO_COMPRESSION zlib.Z_NO_FLUSH "
        "zlib.Z_RLE zlib.Z_TREES "
    )),
    ("3.7", (
        "asyncio.all_tasks asyncio.create_task asyncio.current_task "
        "asyncio.get_running_loop asyncio.run builtins.breakpoint "
        "concurrent.futures.BrokenExecutor "
        "concurrent.futures.thread.BrokenThreadPool "
        "contextlib.AbstractAsyncContextManager "
        "contextlib.asynccontextmanager contextlib.nullcontext contextvars "
        "dataclasses gc.freeze gc.get_freeze_count gc.unfreeze hmac.digest "
        "http.server.ThreadingHTTPServer importlib.resources "
        "importlib.util.source_hash math.remainder os.preadv os.pwritev "
        "os.register_at_fork py_compile.PycInvalidationMode queue.SimpleQueue "
        "socket.CAN_ISOTP socket.close ssl.HAS_NEVER_CHECK_COMMON_NAME "
        "ssl.HAS_SSLv2 ssl.HAS_SSLv3 ssl.HAS_TLSv1 ssl.HAS_TLSv1_1 "
        "ssl.HAS_TLSv1_2 ssl.HAS_TLSv1_3 ssl.OP_NO_RENEGOTIATION "
        "ssl.SSLCertVerificationError ssl.TLSVersion sys.breakpointhook "
        "sys.get_coroutine_origin_tracking_depth "
        "sys.set_coroutine_origin_tracking_depth time.CLOCK_BOOTTIME "
        "time.clock_gettime_ns time.clock_settime_ns time.monotonic_ns "
        "time.perf_counter_ns time.process_time_ns time.pthread_getcpuclockid "
        "time.thread_time time.thread_time_ns time.time_ns "
        "types.ClassMethodDescriptorType types.MethodDescriptorType "
        "types.MethodWrapperType types.WrapperDescriptorType "
        "types.resolve_bases unittest.mock.seal uuid.SafeUUID "
    )),
    ("3.8", (
        "ast.FunctionType ast.NamedExpr ast.PyCF_ALLOW_TOP_LEVEL_AWAIT "
        "ast.PyCF_TYPE_COMMENTS ast.TypeIgnore ast.get_source_segment "
        "concurrent.futures.InvalidStateError curses.ncurses_version "
        "functools.cached_property functools.singledispatchmethod "
        "gettext.dnpgettext gettext.dpgettext gettext.npgettext "
        "gettext.pgettext gzip.BadGzipFile importlib.metadata io.open_code "
        "math.comb math.dist math.isqrt math.perm math.prod mmap.MADV_NORMAL "
        "multiprocessing.parent_process multiprocessing.shared_memory "
        "os.posix_spawn os.posix_spawnp pickle.PickleBuffer "
        "platform.win32_edition platform.win32_is_iot pprint.pp shlex.join "
        "signal.raise_signal signal.strsignal signal.valid_signals "
        "socket.create_server socket.has_dualstack_ipv6 "
        "ssl.OP_ENABLE_MIDDLEBOX_COMPAT statistics.fmean "
        "statistics.geometric_mean statistics.multimode statistics.quantiles "
        "sys.addaudithook sys.audit sys.pycache_prefix sys.unraisablehook "
        "threading.excepthook threading.get_native_id tkinter.ttk.Spinbox "
        "token.EXACT_TOKEN_TYPES types.CellType typing.Final typing.Literal "
        "typing.Protocol typing.SupportsIndex typing.final typing.get_args "
        "typing.get_origin typing.runtime_checkable unicodedata.is_normalized "
        "unittest.IsolatedAsyncioTestCase unittest.addModuleCleanup "
        "unittest.mock.AsyncMock xml.etree.ElementTree.C14NWriterTarget "
        "xml.etree.ElementTree.canonicalize zipfile.Path "
    )),
    ("3.9", (
        "argparse.BooleanOptionalAction ast.unparse asyncio.to_thread "
        "curses.get_escdelay curses.get_tabsize curses.set_escdelay "
        "curses.set_tabsize functools.cache gc.is_finalized graphlib "
        "importlib.resources.as_file importlib.resources.files "
        "keyword.issoftkeyword keyword.softkwlist math.lcm math.nextafter "
        "math.ulp os.waitstatus_to_exitcode pkgutil.resolve_name "
        "random.randbytes socket.recv_fds socket.send_fds sys.platlibdir "
        "tracemalloc.reset_peak types.GenericAlias typing.Annotated "
        "xml.etree.ElementTree.indent zoneinfo "
    )),
    ("3.10", (
        "abc.update_abstractmethods ast.Match ast.MatchAs ast.MatchClass "
        "ast.MatchMapping ast.MatchOr ast.MatchSequence ast.MatchSingleton "
        "ast.MatchStar ast.MatchValue ast.match_case base64.b32hexdecode "
        "base64.b32hexencode builtins.EncodingWarning builtins.aiter "
        "builtins.anext codecs.unregister contextlib.AsyncContextDecorator "
        "contextlib.aclosing curses.has_extended_color_support "
        "dataclasses.KW_ONLY inspect.get_annotations io.text_encoding "
        "itertools.pairwise locale.localize os.EFD_CLOEXEC os.EFD_NONBLOCK "
        "os.EFD_SEMAPHORE os.RWF_APPEND os.SPLICE_F_MOVE os.eventfd "
        "os.eventfd_read os.eventfd_write os.splice "
        "platform.freedesktop_os_release ssl.VERIFY_ALLOW_PROXY_CERTS "
        "ssl.VERIFY_X509_PARTIAL_CHAIN statistics.correlation "
        "statistics.covariance statistics.linear_regression sys.orig_argv "
        "sys.stdlib_module_names sysconfig.get_default_scheme "
        "sysconfig.get_preferred_scheme threading.getprofile "
        "threading.gettrace types.EllipsisType types.NoneType "
        "types.NotImplementedType types.UnionType typing.Concatenate "
        "typing.ParamSpecArgs typing.TypeAlias typing.TypeGuard "
        "typing.is_typeddict "
    )),
    ("3.11", (
        "ast.TryStar asyncio.Barrier asyncio.Runner asyncio.TaskGroup "
        "asyncio.timeout asyncio.timeout_at contextlib.chdir "
        "ctypes.BigEndianUnion ctypes.LittleEndianUnion datetime.UTC "
        "dis.Positions enum.EnumType enum.ReprEnum enum.StrEnum enum.bin "
        "enum.global_enum enum.member enum.nonmember enum.property "
        "enum.show_flag_values enum.verify hashlib.file_digest "
        "http.HTTPMethod importlib.machinery.NamespaceLoader "
        "importlib.resources.abc inspect.getmembers_static "
        "inspect.ismethodwrapper locale.getencoding "
        "logging.getLevelNamesMapping math.cbrt math.exp2 operator.call "
        "os.login_tty re.NOFLAG signal.SIGSTKFLT socket.SO_INCOMING_CPU "
        "sqlite3.Blob sys.exception termios.tcgetwinsize termios.tcsetwinsize "
        "tomllib typing.LiteralString typing.Never typing.NotRequired "
        "typing.Required typing.Self typing.Unpack typing.assert_never "
        "typing.assert_type typing.clear_overloads typing.dataclass_transform "
        "typing.get_overloads typing.reveal_type unittest.enterModuleContext "
        "wsgiref.types zipfile.Path.stem zipfile.Path.suffix "
        "zipfile.Path.suffixes "
    )),
    ("3.12", (
        "ast.ParamSpec ast.TypeAlias ast.TypeVar ast.TypeVarTuple "
        "asyncio.create_eager_task_factory asyncio.eager_task_factory "
        "calendar.Day calendar.JANUARY calendar.Month collections.abc.Buffer "
        "csv.QUOTE_NOTNULL csv.QUOTE_STRINGS ctypes.c_time_t dis.hasarg "
        "dis.hasexc inspect.getasyncgenlocals inspect.getasyncgenstate "
        "inspect.markcoroutinefunction itertools.batched "
        "logging.getHandlerByName logging.getHandlerNames math.sumprod "
        "os.path.isjunction os.path.splitroot os.setns os.unshare "
        "random.binomialvariate socket.ETHERTYPE_ARP socket.ETH_P_ALL "
        "sqlite3.SQLITE_DBCONFIG_DEFENSIVE ssl.OP_ENABLE_KTLS "
        "ssl.OP_LEGACY_SERVER_CONNECT sys.activate_stack_trampoline "
        "sys.deactivate_stack_trampoline sys.getunicodeinternedsize "
        "sys.is_stack_trampoline_active sys.monitoring "
        "threading.setprofile_all_threads threading.settrace_all_threads "
        "tty.cfmakecbreak tty.cfmakeraw types.get_original_bases "
        "typing.TypeAliasType typing.override "
    )),
    ("3.13", (
        "ast.PyCF_OPTIMIZED_AST asyncio.QueueShutDown base64.z85decode "
        "base64.z85encode builtins.PythonFinalizationError "
        "configparser.MultilineContinuationError copy.replace dbm.sqlite3 "
        "dis.hasjump glob.translate importlib.machinery.AppleFrameworkLoader "
        "math.fma mimetypes.guess_file_type os.TFD_CLOEXEC os.TFD_NONBLOCK "
        "os.TFD_TIMER_ABSTIME os.TFD_TIMER_CANCEL_ON_SET os.grantpt "
        "os.posix_openpt os.process_cpu_count os.ptsname os.timerfd_create "
        "os.timerfd_gettime os.timerfd_gettime_ns os.timerfd_settime "
        "os.timerfd_settime_ns os.unlockpt pathlib.UnsupportedOperation "
        "platform.android_ver queue.ShutDown readline.backend ssl.HAS_PSK "
        "stat.SF_DATALESS stat.SF_FIRMLINK stat.SF_RESTRICTED "
        "stat.SF_SETTABLE stat.UF_DATAVAULT stat.UF_SETTABLE stat.UF_TRACKED "
        "statistics.kde statistics.kde_random types.CapsuleType "
        "typing.NoDefault typing.ReadOnly typing.TypeIs "
        "typing.get_protocol_members typing.is_protocol warnings.deprecated "
    )),
    ("3.14", (
        "annotationlib annotationlib.annotations_to_string "
        "annotationlib.call_annotate_function "
        "annotationlib.call_evaluate_function "
        "annotationlib.get_annotate_from_class_namespace "
        "annotationlib.get_annotations annotationlib.type_repr "
        "ast.Interpolation ast.TemplateStr ast.compare compression "
        "compression.zstd "
        "concurrent.futures.interpreter.BrokenInterpreterPool "
        "concurrent.interpreters configparser.InvalidWriteError ctypes.CField "
        "ctypes.c_double_complex ctypes.c_float_complex "
        "ctypes.c_longdouble_complex ctypes.memoryview_at "
        "curses.assume_default_colors decimal.IEEEContext errno.EHWPOISON "
        "fnmatch.filterfalse functools.Placeholder heapq.heapify_max "
        "heapq.heappop_max heapq.heappush_max heapq.heappushpop_max "
        "heapq.heapreplace_max http.server.HTTPSServer "
        "http.server.ThreadingHTTPSServer inspect.CO_HAS_DOCSTRING "
        "inspect.CO_METHOD inspect.ispackage io.Reader io.Writer "
        "operator.is_none operator.is_not_none os.SCHED_DEADLINE "
        "os.SCHED_NORMAL os.readinto os.reload_environ pathlib.types "
        "pdb.get_default_backend pdb.set_default_backend pdb.set_trace_async "
        "platform.invalidate_caches ssl.HAS_PHA string.templatelib "
        "sys.monitoring.clear_tool_id sys.remote_exec token.TSTRING_END "
        "token.TSTRING_MIDDLE token.TSTRING_START turtle.fill "
        "turtle.no_animation turtle.poly turtle.save "
        "typing.evaluate_forward_ref uuid.MAX uuid.NIL uuid.uuid6 uuid.uuid7 "
        "uuid.uuid8 xml.parsers.expat.errors.XML_ERROR_NOT_STARTED "
        "zipfile.ZIP_ZSTANDARD "
    )),
)

RETRAITS_STDLIB: tuple[tuple[str, str], ...] = (
    ("3.9", (
        "aifc.openfp base64.decodestring base64.encodestring fractions.gcd "
        "sunau.openfp sys.callstats sys.getcheckinterval sys.setcheckinterval "
        "wave.openfp "
    )),
    ("3.10", (
        "bz2.RLock formatter parser unicodedata.ucnhash_CAPI "
    )),
    ("3.11", (
        "asyncio.coroutine binascii.a2b_hqx binascii.b2a_hqx binhex "
        "gettext.bind_textdomain_codeset gettext.ldgettext gettext.ldngettext "
        "gettext.lgettext gettext.lngettext inspect.formatargspec "
        "inspect.getargspec smtpd.MailmanProxy "
    )),
    ("3.12", (
        "asynchat asyncore configparser.SafeConfigParser distutils imp "
        "importlib.abc.Finder importlib.find_loader "
        "importlib.util.module_for_loader importlib.util.set_loader "
        "importlib.util.set_package locale.format pkgutil.ImpImporter "
        "pkgutil.ImpLoader smtpd sqlite3.OptimizedUnicode "
        "sqlite3.enable_shared_cache ssl.RAND_pseudo_bytes ssl.match_hostname "
        "ssl.wrap_socket "
    )),
    ("3.13", (
        "aifc audioop cgi cgitb chunk configparser.LegacyInterpolation crypt "
        "imghdr lib2to3 locale.resetlocale mailcap msilib nis nntplib "
        "opcode.MAX_PSEUDO_OPCODE opcode.MIN_PSEUDO_OPCODE opcode.is_pseudo "
        "ossaudiodev pipes re.T re.TEMPLATE re.template sndhdr spwd sunau "
        "telnetlib tkinter.tix typing.io typing.re unittest.findTestCases "
        "unittest.getTestCaseNames unittest.makeSuite uu xdrlib "
    )),
    ("3.14", (
        "ast.Bytes ast.Ellipsis ast.NameConstant ast.Num ast.Str "
        "asyncio.AbstractChildWatcher asyncio.get_child_watcher "
        "asyncio.set_child_watcher dis.RETURN_CONST "
        "importlib.abc.ResourceReader pkgutil.find_loader pkgutil.get_loader "
        "pty.master_open pty.slave_open urllib.request.FancyURLopener "
        "urllib.request.URLopener "
    )),
)


class EntreeInvalide(Exception):
    """Entrée refusée : chemin absent, fichier illisible, option malformée."""


@dataclass(frozen=True)
class Exigence:
    """Une exigence de version, rattachée à une ligne et à sa justification."""

    version: tuple[int, int]
    regle: str
    ligne: int
    raison: str
    nature: str
    verification: str
    garde: str | None = None


@dataclass
class Rapport:
    """Résultat de l'analyse d'un fichier."""

    chemin: str
    exigences: list[Exigence] = field(default_factory=list)
    gardees: list[Exigence] = field(default_factory=list)
    retraits: list[Exigence] = field(default_factory=list)
    plancher_ast: tuple[int, int] | None = None
    vermin: str | None = None


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

def charger_table(brute: tuple[tuple[str, str], ...]) -> dict[str, tuple[int, int]]:
    """Déplie une table (version, noms séparés par des espaces) en dictionnaire."""
    table: dict[str, tuple[int, int]] = {}
    for version, noms in brute:
        majeur, mineur = version.split(".")
        for nom in noms.split():
            table[nom] = (int(majeur), int(mineur))
    return table


def verification_ajout(version: tuple[int, int]) -> str:
    """Décrit comment l'entrée de table a été vérifiée."""
    if version >= (3, 9):
        return f"doc 3.14 versionadded + essai : absent sous 3.{version[1] - 1}, présent sous 3.{version[1]}"
    if version == (3, 8):
        return "doc 3.14 versionadded + présent sous 3.8.20"
    return "doc 3.14 versionadded (antériorité non éprouvée)"


def texte_version(version: tuple[int, int] | None) -> str | None:
    """Rend (3, 12) sous la forme « 3.12 »."""
    return None if version is None else f"{version[0]}.{version[1]}"


def lire_version(texte: str) -> tuple[int, int]:
    """Lit « 3.10 » ; lève EntreeInvalide sinon."""
    morceaux = texte.strip().split(".")
    if len(morceaux) != 2 or not all(m.isdigit() for m in morceaux):
        raise EntreeInvalide(f"version cible illisible : {texte!r} (attendu par exemple 3.10)")
    return int(morceaux[0]), int(morceaux[1])


# --------------------------------------------------------------------------- #
# Lecture des fichiers
# --------------------------------------------------------------------------- #

def lister_python(dossier: Path, maximum: int) -> list[Path]:
    """Liste les .py d'un dossier, récursivement, sans suivre les liens."""
    trouves: list[Path] = []
    for base, dossiers, fichiers in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if d not in DOSSIERS_IGNORES)
        for nom in sorted(fichiers):
            if nom.endswith(".py"):
                trouves.append(Path(base) / nom)
                if len(trouves) >= maximum:
                    print(f"avertissement : arrêt à {maximum} fichiers (--max-fichiers)", file=sys.stderr)
                    return trouves
    return trouves


def collecter_cibles(cibles: list[Path], maximum: int) -> tuple[list[Path], set[Path]]:
    """Rend les fichiers à examiner et l'ensemble de ceux donnés explicitement."""
    fichiers: list[Path] = []
    explicites: set[Path] = set()
    for cible in cibles:
        if cible.is_dir():
            fichiers.extend(lister_python(cible, maximum - len(fichiers)))
        elif cible.is_file():
            fichiers.append(cible)
            explicites.add(cible)
        elif cible.exists():
            raise EntreeInvalide(f"ni fichier ni dossier : {cible}")
        else:
            raise EntreeInvalide(f"chemin introuvable : {cible}")
    return fichiers, explicites


def lire_source(chemin: Path, taille_max: int) -> str:
    """Lit un source Python en respectant son encodage déclaré ; refuse le binaire."""
    try:
        taille = chemin.stat().st_size
        if taille > taille_max:
            raise EntreeInvalide(f"{chemin} : {taille} octets, au-delà de --taille-max {taille_max}")
        donnees = chemin.read_bytes()
    except OSError as erreur:
        raise EntreeInvalide(f"{chemin} : lecture impossible ({erreur.strerror})") from erreur
    if b"\x00" in donnees:
        raise EntreeInvalide(f"{chemin} : contenu binaire (octet nul), pas un source Python")
    try:
        encodage, _ = tokenize.detect_encoding(io.BytesIO(donnees).readline)
        return donnees.decode(encodage)
    except (SyntaxError, UnicodeDecodeError, LookupError) as erreur:
        raise EntreeInvalide(f"{chemin} : décodage impossible ({erreur})") from erreur


def analyser_arbre(source: str, nom: str) -> ast.Module:
    """Analyse le source avec la grammaire de l'interpréteur courant."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(source, filename=nom)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as erreur:
            ligne = getattr(erreur, "lineno", None)
            lieu = f" ligne {ligne}" if ligne else ""
            raise EntreeInvalide(f"{nom}{lieu} : non analysable par Python "
                                 f"{sys.version_info[0]}.{sys.version_info[1]} ({erreur})") from erreur


# --------------------------------------------------------------------------- #
# Jetons : index des parenthèses et règles des chaînes formatées
# --------------------------------------------------------------------------- #

@dataclass
class IndexJetons:
    """Jetons significatifs, positions et appariement des parenthèses."""

    jetons: list[tokenize.TokenInfo]
    debuts: dict[tuple[int, int], int]
    fins: dict[tuple[int, int], int]
    paires: dict[int, int]


def lister_jetons(source: str) -> list[tokenize.TokenInfo]:
    """Découpe le source en jetons (grammaire de l'interpréteur courant)."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return []


def indexer_jetons(jetons: list[tokenize.TokenInfo]) -> IndexJetons:
    """Indexe les jetons significatifs et apparie les parenthèses."""
    ignores = {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT,
               tokenize.DEDENT, tokenize.ENDMARKER}
    utiles = [j for j in jetons if j.type not in ignores]
    paires: dict[int, int] = {}
    ouvertes: list[int] = []
    for rang, jeton in enumerate(utiles):
        if jeton.type == tokenize.OP and jeton.string in "([{":
            ouvertes.append(rang)
        elif jeton.type == tokenize.OP and jeton.string in ")]}" and ouvertes:
            paires[ouvertes.pop()] = rang
    return IndexJetons(utiles, {j.start: r for r, j in enumerate(utiles)},
                       {j.end: r for r, j in enumerate(utiles)}, paires)


def position(lignes: list[str], ligne: int, colonne_octets: int) -> tuple[int, int]:
    """Convertit une colonne ast (octets UTF-8) en colonne tokenize (caractères)."""
    if not 0 < ligne <= len(lignes):
        return ligne, colonne_octets
    texte = lignes[ligne - 1].encode("utf-8")[:colonne_octets]
    return ligne, len(texte.decode("utf-8", errors="replace"))


def bornes(noeud: ast.AST, lignes: list[str]) -> tuple[tuple[int, int], tuple[int, int]]:
    """Positions (en caractères) de début et de fin d'un nœud."""
    debut = position(lignes, noeud.lineno, noeud.col_offset)
    fin = position(lignes, noeud.end_lineno or noeud.lineno, noeud.end_col_offset or 0)
    return debut, fin


def entoure_de_parentheses(index: IndexJetons, noeud: ast.AST, lignes: list[str]) -> bool:
    """Vrai si le nœud est directement encadré par « ( » et sa « ) » appariée."""
    debut, fin = bornes(noeud, lignes)
    rang = index.debuts.get(debut)
    if rang is None or rang == 0:
        return False
    avant = index.jetons[rang - 1]
    if avant.string != "(":
        return False
    fermante = index.paires.get(rang - 1)
    return fermante is not None and index.fins.get(fin, -2) + 1 == fermante


def tuple_parenthese(index: IndexJetons, noeud: ast.AST, lignes: list[str]) -> bool:
    """Vrai si un tuple est écrit avec ses propres parenthèses englobantes."""
    debut, fin = bornes(noeud, lignes)
    rang = index.debuts.get(debut)
    if rang is None or index.jetons[rang].string != "(":
        return entoure_de_parentheses(index, noeud, lignes)
    fermante = index.paires.get(rang)
    return fermante is not None and index.jetons[fermante].end == fin


@dataclass
class ContexteChaine:
    """Une f-string (ou t-string) ouverte pendant le parcours des jetons."""

    guillemet: str
    triple: bool
    pile: list[str] = field(default_factory=list)


def guillemet_de(texte: str) -> tuple[str, bool]:
    """Extrait le guillemet d'ouverture d'une chaîne (préfixe retiré)."""
    corps = texte.lstrip("rRbBuUfFtT")
    triple = corps[:3] in ("'''", '"""')
    return (corps[:1] or "'"), triple


def conflit_guillemet(contextes: list[ContexteChaine], texte: str) -> bool:
    """Avant 3.12, une chaîne imbriquée ne pouvait réutiliser un guillemet englobant."""
    interne, triple_interne = guillemet_de(texte)
    for ctx in contextes:
        if ctx.guillemet == interne and (not ctx.triple or triple_interne):
            return True
    return False


class AnalyseurChaines:
    """Machine à états sur les jetons : règles PEP 701, f-string « = », soulignés."""

    def __init__(self, lignes: list[str]) -> None:
        self.lignes = lignes
        self.contextes: list[ContexteChaine] = []
        self.trouvees: list[tuple[str, int, str]] = []

    def noter(self, code: str, jeton: tokenize.TokenInfo, detail: str) -> None:
        self.trouvees.append((code, jeton.start[0], detail))

    def dans_expression(self) -> bool:
        return bool(self.contextes) and bool(self.contextes[-1].pile)

    def traiter(self, jeton: tokenize.TokenInfo, precedent: tokenize.TokenInfo | None,
                suivant: tokenize.TokenInfo | None) -> None:
        nom = tokenize.tok_name.get(jeton.type, "")
        if jeton.type == tokenize.NUMBER and "_" in jeton.string:
            self.noter("souligne_numerique", jeton, jeton.string)
        if nom in ("FSTRING_START", "TSTRING_START"):
            self.ouvrir(jeton)
        elif nom in ("FSTRING_END", "TSTRING_END"):
            if self.contextes:
                self.contextes.pop()
        elif nom in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
            if len(self.contextes) > 1 and "\\" in jeton.string:
                self.noter("fstring_701", jeton, "barre oblique inverse dans une chaîne imbriquée")
        elif self.contextes:
            self.traiter_interieur(jeton, precedent, suivant)

    def ouvrir(self, jeton: tokenize.TokenInfo) -> None:
        if self.dans_expression() and conflit_guillemet(self.contextes, jeton.string):
            self.noter("fstring_701", jeton, "chaîne imbriquée réutilisant le guillemet englobant")
        guillemet, triple = guillemet_de(jeton.string)
        self.contextes.append(ContexteChaine(guillemet, triple))

    def traiter_interieur(self, jeton: tokenize.TokenInfo, precedent: tokenize.TokenInfo | None,
                          suivant: tokenize.TokenInfo | None) -> None:
        ctx = self.contextes[-1]
        if ctx.pile and precedent is not None and jeton.start[0] > precedent.end[0]:
            self.verifier_saut_de_ligne(ctx, jeton, precedent)
        if jeton.type == tokenize.STRING:
            if conflit_guillemet(self.contextes, jeton.string):
                self.noter("fstring_701", jeton, "chaîne imbriquée réutilisant le guillemet englobant")
            if "\\" in jeton.string:
                self.noter("fstring_701", jeton, "barre oblique inverse dans l'expression")
        elif jeton.type == tokenize.COMMENT and ctx.pile:
            self.noter("fstring_701", jeton, "commentaire dans l'expression")
        elif jeton.type == tokenize.OP:
            self.traiter_operateur(ctx, jeton, suivant)

    def verifier_saut_de_ligne(self, ctx: ContexteChaine, jeton: tokenize.TokenInfo,
                               precedent: tokenize.TokenInfo) -> None:
        fin_precedente = self.lignes[precedent.end[0] - 1].rstrip("\r\n")
        if fin_precedente.endswith("\\"):
            self.noter("fstring_701", jeton, "continuation \\ dans l'expression")
        elif not ctx.triple:
            self.noter("fstring_701", jeton, "expression sur plusieurs lignes dans une chaîne à guillemet simple")

    def traiter_operateur(self, ctx: ContexteChaine, jeton: tokenize.TokenInfo,
                          suivant: tokenize.TokenInfo | None) -> None:
        texte = jeton.string
        if texte in "([{":
            ctx.pile.append(texte)
        elif texte in ")]}" and ctx.pile:
            ctx.pile.pop()
        elif texte == "=" and len(ctx.pile) == 1 and suivant is not None and suivant.string in ("}", "!", ":"):
            self.noter("fstring_egal", jeton, "f'{x=}'")


def exigences_jetons(jetons: list[tokenize.TokenInfo], lignes: list[str]) -> list[tuple[str, int, str]]:
    """Applique la machine à états des chaînes à tout le flux de jetons."""
    analyseur = AnalyseurChaines(lignes)
    significatifs = [j for j in jetons if j.type not in (tokenize.NL, tokenize.NEWLINE)]
    for rang, jeton in enumerate(significatifs):
        precedent = significatifs[rang - 1] if rang else None
        suivant = significatifs[rang + 1] if rang + 1 < len(significatifs) else None
        analyseur.traiter(jeton, precedent, suivant)
    return analyseur.trouvees


# --------------------------------------------------------------------------- #
# Règles syntaxiques sur l'arbre
# --------------------------------------------------------------------------- #

def nom_pointe(expr: ast.expr) -> bool:
    """Vrai pour a, a.b, a.b.c (grammaire des décorateurs d'avant 3.9)."""
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    return isinstance(expr, ast.Name)


def contient_etoile(noeud: ast.AST) -> bool:
    """Vrai si le tuple contient un élément étoilé."""
    return isinstance(noeud, ast.Tuple) and any(isinstance(e, ast.Starred) for e in noeud.elts)


class VisiteurSyntaxe:
    """Parcourt l'arbre et note (code de règle, ligne, détail)."""

    def __init__(self, index: IndexJetons, lignes: list[str]) -> None:
        self.index = index
        self.lignes = lignes
        self.trouvees: list[tuple[str, int, str]] = []
        self.fonctions: list[str] = []

    def noter(self, code: str, noeud: ast.AST, detail: str = "") -> None:
        self.trouvees.append((code, getattr(noeud, "lineno", 0), detail))

    def parcourir(self, noeud: ast.AST) -> None:
        methode = getattr(self, "voir_" + type(noeud).__name__, None)
        if methode is not None and methode(noeud):
            return
        for enfant in ast.iter_child_nodes(noeud):
            self.parcourir(enfant)

    def descendre_fonction(self, noeud: ast.AST, genre: str) -> bool:
        self.fonctions.append(genre)
        for enfant in ast.iter_child_nodes(noeud):
            self.parcourir(enfant)
        self.fonctions.pop()
        return True

    def voir_FunctionDef(self, noeud: ast.FunctionDef) -> bool:
        self.verifier_definition(noeud)
        return self.descendre_fonction(noeud, "def")

    def voir_AsyncFunctionDef(self, noeud: ast.AsyncFunctionDef) -> bool:
        self.noter("async", noeud, "async def")
        self.verifier_definition(noeud)
        return self.descendre_fonction(noeud, "async")

    def voir_Lambda(self, noeud: ast.Lambda) -> bool:
        if noeud.args.posonlyargs:
            self.noter("positionnels_seuls", noeud, "lambda")
        return self.descendre_fonction(noeud, "lambda")

    def voir_ClassDef(self, noeud: ast.ClassDef) -> bool:
        self.verifier_decorateurs(noeud)
        self.verifier_parametres_type(noeud)
        return self.descendre_fonction(noeud, "class")

    def verifier_definition(self, noeud: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.verifier_decorateurs(noeud)
        self.verifier_parametres_type(noeud)
        if noeud.args.posonlyargs:
            self.noter("positionnels_seuls", noeud, noeud.name)
        vararg = noeud.args.vararg
        if vararg is not None and isinstance(vararg.annotation, ast.Starred):
            self.noter("etoile_indice", vararg, f"*{vararg.arg}: *…")

    def verifier_decorateurs(self, noeud: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        for deco in noeud.decorator_list:
            simple = nom_pointe(deco.func if isinstance(deco, ast.Call) else deco)
            if not simple or entoure_de_parentheses(self.index, deco, self.lignes):
                self.noter("decorateur_libre", deco, ast.unparse(deco)[:60])

    def verifier_parametres_type(self, noeud: ast.AST) -> None:
        for param in getattr(noeud, "type_params", None) or ():
            self.noter("parametres_type", param, getattr(param, "name", ""))
            if getattr(param, "default_value", None) is not None:
                self.noter("defaut_type", param, getattr(param, "name", ""))

    def voir_TypeAlias(self, noeud: ast.AST) -> None:
        self.noter("instruction_type", noeud)
        self.verifier_parametres_type(noeud)

    def voir_Await(self, noeud: ast.Await) -> None:
        self.noter("async", noeud, "await")

    def voir_AsyncFor(self, noeud: ast.AsyncFor) -> None:
        self.noter("async", noeud, "async for")

    def voir_AsyncWith(self, noeud: ast.AsyncWith) -> None:
        self.noter("async", noeud, "async with")
        self.verifier_with(noeud)

    def voir_With(self, noeud: ast.With) -> None:
        self.verifier_with(noeud)

    def verifier_with(self, noeud: ast.With | ast.AsyncWith) -> None:
        premier = noeud.items[0].context_expr
        debut, _ = bornes(premier, self.lignes)
        rang = self.index.debuts.get(debut)
        if rang is None or rang == 0 or self.index.jetons[rang - 1].string != "(":
            return
        fermante = self.index.paires.get(rang - 1)
        apres = self.index.jetons[fermante + 1] if fermante is not None and fermante + 1 < len(self.index.jetons) else None
        plusieurs = len(noeud.items) > 1 or noeud.items[0].optional_vars is not None
        if apres is not None and apres.string == ":" and plusieurs:
            self.noter("with_parentheses", noeud)

    def voir_BinOp(self, noeud: ast.BinOp) -> None:
        if isinstance(noeud.op, ast.MatMult):
            self.noter("matmul", noeud)

    def voir_AugAssign(self, noeud: ast.AugAssign) -> None:
        if isinstance(noeud.op, ast.MatMult):
            self.noter("matmul", noeud, "@=")

    def voir_Call(self, noeud: ast.Call) -> None:
        etoiles = sum(isinstance(a, ast.Starred) for a in noeud.args)
        doubles = sum(k.arg is None for k in noeud.keywords)
        if etoiles > 1 or doubles > 1:
            self.noter("depaquetage", noeud, "plusieurs * ou ** dans un appel")

    def voir_Dict(self, noeud: ast.Dict) -> None:
        if any(cle is None for cle in noeud.keys):
            self.noter("depaquetage", noeud, "{**a}")

    def voir_List(self, noeud: ast.List) -> None:
        self.verifier_affichage(noeud)

    def voir_Set(self, noeud: ast.Set) -> None:
        self.verifier_affichage(noeud)
        for element in noeud.elts:
            self.verifier_morse_nu(element, "ensemble")

    def voir_Tuple(self, noeud: ast.Tuple) -> None:
        self.verifier_affichage(noeud)

    def verifier_affichage(self, noeud: ast.List | ast.Set | ast.Tuple) -> None:
        charge = isinstance(getattr(noeud, "ctx", ast.Load()), ast.Load)
        if charge and any(isinstance(e, ast.Starred) for e in noeud.elts):
            self.noter("depaquetage", noeud, "*x dans un affichage")

    def verifier_morse_nu(self, expr: ast.AST, lieu: str) -> None:
        if isinstance(expr, ast.NamedExpr) and not entoure_de_parentheses(self.index, expr, self.lignes):
            self.noter("morse_nu", expr, lieu)

    def voir_SetComp(self, noeud: ast.SetComp) -> None:
        self.verifier_morse_nu(noeud.elt, "compréhension d'ensemble")
        self.verifier_comprehensions(noeud)

    def voir_ListComp(self, noeud: ast.ListComp) -> None:
        self.verifier_comprehensions(noeud)

    def voir_DictComp(self, noeud: ast.DictComp) -> None:
        self.verifier_comprehensions(noeud)

    def voir_GeneratorExp(self, noeud: ast.GeneratorExp) -> None:
        self.verifier_comprehensions(noeud)

    def verifier_comprehensions(self, noeud: ast.AST) -> None:
        if any(c.is_async for c in getattr(noeud, "generators", ())):
            self.noter("comprehension_async", noeud)

    def voir_Subscript(self, noeud: ast.Subscript) -> None:
        indice = noeud.slice
        if isinstance(indice, ast.Starred) or contient_etoile(indice):
            self.noter("etoile_indice", noeud, "a[*b]")
        self.verifier_morse_nu(indice, "indice")

    def voir_JoinedStr(self, noeud: ast.JoinedStr) -> None:
        self.noter("fstring", noeud)

    def voir_TemplateStr(self, noeud: ast.AST) -> None:
        self.noter("tstring", noeud)

    def voir_AnnAssign(self, noeud: ast.AnnAssign) -> None:
        self.noter("annotation_variable", noeud)

    def voir_NamedExpr(self, noeud: ast.NamedExpr) -> None:
        self.noter("morse", noeud)

    def voir_ImportFrom(self, noeud: ast.ImportFrom) -> None:
        if noeud.module != "__future__":
            return
        for alias in noeud.names:
            if alias.name == "annotations":
                self.noter("future_annotations", noeud)
            elif alias.name == "generator_stop":
                self.noter("generator_stop", noeud)

    def voir_Yield(self, noeud: ast.Yield) -> None:
        self.verifier_yield(noeud)

    def voir_YieldFrom(self, noeud: ast.YieldFrom) -> None:
        self.verifier_yield(noeud)

    def verifier_yield(self, noeud: ast.Yield | ast.YieldFrom) -> None:
        if self.fonctions and self.fonctions[-1] == "async":
            self.noter("generateur_async", noeud)
        valeur = noeud.value
        if contient_etoile(valeur) and not tuple_parenthese(self.index, valeur, self.lignes):
            self.noter("etoile_return", noeud, "yield")

    def voir_Return(self, noeud: ast.Return) -> None:
        valeur = noeud.value
        if contient_etoile(valeur) and not tuple_parenthese(self.index, valeur, self.lignes):
            self.noter("etoile_return", noeud, "return")

    def voir_For(self, noeud: ast.For) -> None:
        if contient_etoile(noeud.iter) and not tuple_parenthese(self.index, noeud.iter, self.lignes):
            self.noter("etoile_for", noeud)

    def voir_Match(self, noeud: ast.AST) -> None:
        self.noter("match", noeud)

    def voir_Try(self, noeud: ast.Try) -> None:
        self.verifier_try(noeud)

    def voir_TryStar(self, noeud: ast.AST) -> None:
        self.noter("except_etoile", noeud)
        self.verifier_try(noeud)

    def verifier_try(self, noeud: ast.Try) -> None:
        for gestionnaire in noeud.handlers:
            genre = gestionnaire.type
            if isinstance(genre, ast.Tuple) and not tuple_parenthese(self.index, genre, self.lignes):
                self.noter("except_sans_parentheses", gestionnaire, ast.unparse(genre)[:60])
        for instruction in noeud.finalbody:
            for continu in continues_directs(instruction):
                self.noter("continue_finally", continu)


def continues_directs(instruction: ast.stmt) -> Iterator[ast.Continue]:
    """Les continue d'un bloc finally qui ne sont pas dans une boucle interne."""
    if isinstance(instruction, ast.Continue):
        yield instruction
        return
    if isinstance(instruction, (ast.For, ast.AsyncFor, ast.While, ast.FunctionDef,
                                ast.AsyncFunctionDef, ast.ClassDef)):
        return
    for enfant in ast.iter_child_nodes(instruction):
        if isinstance(enfant, ast.stmt):
            yield from continues_directs(enfant)


def exigences_syntaxe(arbre: ast.Module, source: str) -> list[Exigence]:
    """Toutes les exigences syntaxiques (arbre + jetons) d'un source."""
    lignes = source.split("\n")
    jetons = lister_jetons(source)
    index = indexer_jetons(jetons)
    visiteur = VisiteurSyntaxe(index, lignes)
    visiteur.parcourir(arbre)
    regles = {code: (version, intitule, verif) for code, version, intitule, verif in REGLES_SYNTAXE}
    exigences = []
    for code, ligne, detail in visiteur.trouvees + exigences_jetons(jetons, lignes):
        version, intitule, verif = regles[code]
        exigences.append(Exigence(version, intitule, ligne, detail or intitule, "syntaxe", verif))
    return exigences


def plancher_grammatical(source: str) -> tuple[tuple[int, int] | None, Exigence | None]:
    """Plus petite feature_version acceptée par ast.parse, et la cause du refus juste en dessous."""
    refus: SyntaxError | None = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for mineur in range(7, MINEUR_MAX + 1):
            try:
                ast.parse(source, feature_version=(3, mineur))
            except (SyntaxError, ValueError) as erreur:
                refus = erreur if isinstance(erreur, SyntaxError) else refus
                continue
            if refus is None or mineur == 7:
                return (3, mineur), None
            raison = f"ast.parse(feature_version=(3, {mineur - 1})) refuse : {refus.msg}"
            return (3, mineur), Exigence((3, mineur), "grammaire selon ast.parse(feature_version)",
                                         refus.lineno or 0, raison, "grammaire",
                                         "ast.parse de l'interpréteur courant")
    return None, None


# --------------------------------------------------------------------------- #
# Stdlib : usages, gardes, confrontation aux tables
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Garde:
    """Raison pour laquelle un usage n'est pas exécuté partout."""

    genre: str
    minimum: tuple[int, int] | None = None
    maximum: tuple[int, int] | None = None


@dataclass(frozen=True)
class Usage:
    """Un nom qualifié de la stdlib lu par le code."""

    nom: str
    ligne: int
    gardes: tuple[Garde, ...]


def est_version_info(expr: ast.expr) -> bool:
    """Reconnaît sys.version_info, version_info et leurs tranches."""
    if isinstance(expr, ast.Subscript):
        expr = expr.value
    if isinstance(expr, ast.Attribute):
        return expr.attr == "version_info" and isinstance(expr.value, ast.Name) and expr.value.id == "sys"
    return isinstance(expr, ast.Name) and expr.id == "version_info"


def version_comparee(expr: ast.expr) -> tuple[int, int] | None:
    """Lit (3, x) dans une comparaison à sys.version_info."""
    if not isinstance(expr, ast.Tuple) or len(expr.elts) < 2:
        return None
    valeurs = [e.value for e in expr.elts[:2] if isinstance(e, ast.Constant)]
    if len(valeurs) == 2 and all(isinstance(v, int) for v in valeurs):
        return valeurs[0], valeurs[1]
    return None


def gardes_du_if(test: ast.expr) -> tuple[Garde | None, Garde | None]:
    """Gardes posées par un if sur sa branche vraie et sa branche sinon."""
    if (isinstance(test, ast.Name) and test.id == GARDE_TYPAGE) or (
            isinstance(test, ast.Attribute) and test.attr == GARDE_TYPAGE):
        return Garde(GARDE_TYPAGE), None
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "hasattr":
        return Garde("hasattr"), None
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and est_version_info(test.left):
        version = version_comparee(test.comparators[0])
        if version is None:
            return None, None
        haut, bas = Garde("version", minimum=version), Garde("version", maximum=version)
        if isinstance(test.ops[0], (ast.GtE, ast.Gt)):
            return haut, bas
        if isinstance(test.ops[0], (ast.Lt, ast.LtE)):
            return bas, haut
    return None, None


def attrape_import(gestionnaire: ast.ExceptHandler) -> bool:
    """Vrai si le gestionnaire rattrape l'absence d'un module ou d'un attribut."""
    genre = gestionnaire.type
    if genre is None:
        return True
    noms = genre.elts if isinstance(genre, ast.Tuple) else [genre]
    for nom in noms:
        texte = nom.id if isinstance(nom, ast.Name) else getattr(nom, "attr", "")
        if texte in EXCEPTIONS_GARDE:
            return True
    return False


def noms_lies(arbre: ast.Module) -> frozenset[str]:
    """Tous les noms liés quelque part dans le module (affectation, def, import…)."""
    lies: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Name) and not isinstance(noeud.ctx, ast.Load):
            lies.add(noeud.id)
        elif isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lies.add(noeud.name)
        elif isinstance(noeud, ast.arg):
            lies.add(noeud.arg)
        elif isinstance(noeud, ast.alias):
            lies.add((noeud.asname or noeud.name).split(".")[0])
        elif isinstance(noeud, (ast.Global, ast.Nonlocal)):
            lies.update(noeud.names)
        elif isinstance(noeud, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and noeud.name:
            lies.add(noeud.name)
    return frozenset(lies)


class VisiteurStdlib:
    """Relève les noms de la stdlib lus par le code, avec les gardes en vigueur."""

    def __init__(self, lies: frozenset[str], annotations_differees: bool) -> None:
        self.lies = lies
        self.annotations_differees = annotations_differees
        self.gardes: list[Garde] = []
        self.liaisons: dict[str, tuple[str, tuple[Garde, ...]]] = {}
        self.usages: list[Usage] = []

    def noter(self, nom: str, ligne: int, gardes: tuple[Garde, ...] = ()) -> None:
        self.usages.append(Usage(nom, ligne, tuple(self.gardes) + gardes))

    def parcourir(self, noeud: ast.AST) -> None:
        methode = getattr(self, "voir_" + type(noeud).__name__, None)
        if methode is not None and methode(noeud):
            return
        for enfant in ast.iter_child_nodes(noeud):
            self.parcourir(enfant)

    def sous_garde(self, noeuds: list[ast.AST], garde: Garde | None) -> None:
        if garde is not None:
            self.gardes.append(garde)
        for noeud in noeuds:
            self.parcourir(noeud)
        if garde is not None:
            self.gardes.pop()

    def lier(self, local: str, qualifie: str) -> None:
        precedent = self.liaisons.get(local)
        gardes = tuple(self.gardes)
        if precedent is not None and precedent[0] != qualifie:
            gardes = gardes + (Garde("import alternatif"),)
        self.liaisons[local] = (qualifie, gardes)

    def voir_Import(self, noeud: ast.Import) -> bool:
        for alias in noeud.names:
            morceaux = alias.name.split(".")
            for n in range(1, len(morceaux) + 1):
                self.noter(".".join(morceaux[:n]), noeud.lineno)
            if alias.asname:
                self.lier(alias.asname, alias.name)
            else:
                self.lier(morceaux[0], morceaux[0])
        return True

    def voir_ImportFrom(self, noeud: ast.ImportFrom) -> bool:
        if noeud.level or not noeud.module or noeud.module == "__future__":
            return True
        morceaux = noeud.module.split(".")
        for n in range(1, len(morceaux) + 1):
            self.noter(".".join(morceaux[:n]), noeud.lineno)
        for alias in noeud.names:
            if alias.name != "*":
                self.noter(f"{noeud.module}.{alias.name}", noeud.lineno)
                self.lier(alias.asname or alias.name, f"{noeud.module}.{alias.name}")
        return True

    def voir_Attribute(self, noeud: ast.Attribute) -> bool:
        chaine: list[str] = []
        base: ast.expr = noeud
        while isinstance(base, ast.Attribute):
            chaine.append(base.attr)
            base = base.value
        if not isinstance(base, ast.Name) or base.id not in self.liaisons:
            return False
        qualifie, gardes = self.liaisons[base.id]
        for n in range(1, len(chaine) + 1):
            self.noter(qualifie + "." + ".".join(reversed(chaine[-n:])), noeud.lineno, gardes)
        return True

    def voir_Name(self, noeud: ast.Name) -> bool:
        if isinstance(noeud.ctx, ast.Load) and noeud.id not in self.lies:
            self.noter("builtins." + noeud.id, noeud.lineno)
        return True

    def voir_If(self, noeud: ast.If) -> bool:
        vrai, sinon = gardes_du_if(noeud.test)
        self.parcourir(noeud.test)
        self.sous_garde(noeud.body, vrai)
        self.sous_garde(noeud.orelse, sinon)
        return True

    def voir_Try(self, noeud: ast.Try) -> bool:
        protege = any(attrape_import(g) for g in noeud.handlers)
        self.sous_garde(noeud.body, Garde("try/except") if protege else None)
        self.sous_garde(list(noeud.handlers) + noeud.orelse + noeud.finalbody, None)
        return True

    def voir_TryStar(self, noeud: ast.Try) -> bool:
        return self.voir_Try(noeud)

    def voir_arg(self, noeud: ast.arg) -> bool:
        if noeud.annotation is not None:
            self.annotation(noeud.annotation)
        return True

    def voir_FunctionDef(self, noeud: ast.FunctionDef) -> bool:
        if noeud.returns is not None:
            self.annotation(noeud.returns)
        for enfant in ast.iter_child_nodes(noeud):
            if enfant is not noeud.returns:
                self.parcourir(enfant)
        return True

    def voir_AsyncFunctionDef(self, noeud: ast.AsyncFunctionDef) -> bool:
        return self.voir_FunctionDef(noeud)

    def voir_AnnAssign(self, noeud: ast.AnnAssign) -> bool:
        self.annotation(noeud.annotation)
        self.parcourir(noeud.target)
        if noeud.value is not None:
            self.parcourir(noeud.value)
        return True

    def annotation(self, expr: ast.expr) -> None:
        garde = Garde("annotation différée") if self.annotations_differees else None
        self.sous_garde([expr], garde)


def couverte(garde: Garde, ajout: tuple[int, int] | None, retrait: tuple[int, int] | None) -> bool:
    """Vrai si la garde neutralise l'exigence (ajout) ou l'incompatibilité (retrait)."""
    if garde.genre != "version":
        return True
    if ajout is not None:
        return garde.minimum is not None and ajout <= garde.minimum
    return retrait is not None and garde.maximum is not None and garde.maximum <= retrait


def libelle_garde(gardes: tuple[Garde, ...]) -> str:
    """Décrit les gardes d'un usage."""
    morceaux = []
    for garde in gardes:
        if garde.minimum is not None:
            morceaux.append(f"si version >= {texte_version(garde.minimum)}")
        elif garde.maximum is not None:
            morceaux.append(f"si version < {texte_version(garde.maximum)}")
        else:
            morceaux.append(garde.genre)
    return ", ".join(morceaux)


def confronter_usage(usage: Usage, ajouts: dict[str, tuple[int, int]],
                     retraits: dict[str, tuple[int, int]], rapport: Rapport) -> None:
    """Range un usage en exigence, exigence gardée ou retrait."""
    nom_affiche = usage.nom.removeprefix("builtins.")
    ajout = ajouts.get(usage.nom)
    if ajout is not None:
        garde = next((g for g in usage.gardes if couverte(g, ajout, None)), None)
        exigence = Exigence(ajout, f"stdlib : {nom_affiche}", usage.ligne,
                            f"{nom_affiche} ajouté en {texte_version(ajout)}", "stdlib",
                            verification_ajout(ajout), libelle_garde(usage.gardes) if garde else None)
        (rapport.gardees if garde else rapport.exigences).append(exigence)
    retrait = retraits.get(usage.nom)
    if retrait is not None and not any(couverte(g, None, retrait) for g in usage.gardes):
        rapport.retraits.append(Exigence(retrait, f"stdlib : {nom_affiche}", usage.ligne,
                                         f"{nom_affiche} retiré en {texte_version(retrait)}",
                                         "retrait", VERIFICATION_RETRAITS))


def annonce_annotations_differees(arbre: ast.Module) -> bool:
    """Vrai si le module déclare from __future__ import annotations."""
    for noeud in arbre.body:
        if isinstance(noeud, ast.ImportFrom) and noeud.module == "__future__":
            if any(alias.name == "annotations" for alias in noeud.names):
                return True
    return False


# --------------------------------------------------------------------------- #
# Comparaison optionnelle avec vermin
# --------------------------------------------------------------------------- #

def minimum_vermin(source: str, nom: str) -> str | None:
    """Minimum Python 3 selon vermin, ou None si vermin est absent."""
    if vermin is None:
        return None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            resultat = vermin.detect(source, vermin.Config(), nom)
    except Exception as erreur:  # bibliothèque tierce : on rapporte, on ne masque pas
        print(f"{nom} : vermin a échoué ({type(erreur).__name__}: {erreur})", file=sys.stderr)
        return "échec"
    if not isinstance(resultat, list) or len(resultat) < 2:
        return "illisible"
    v3 = resultat[1]
    if v3 is None:
        return None
    if tuple(v3) == (0, 0):
        return "incompatible"
    return f"{v3[0]}.{v3[1]}"


# --------------------------------------------------------------------------- #
# Analyse d'un fichier et synthèse
# --------------------------------------------------------------------------- #

def analyser_fichier(chemin: Path, libelle: str, taille_max: int,
                     ajouts: dict[str, tuple[int, int]], retraits: dict[str, tuple[int, int]]) -> Rapport:
    """Analyse complète d'un fichier ; lève EntreeInvalide s'il n'est pas analysable."""
    source = lire_source(chemin, taille_max)
    arbre = analyser_arbre(source, libelle)
    rapport = Rapport(libelle)
    rapport.exigences.extend(exigences_syntaxe(arbre, source))
    rapport.plancher_ast, exigence_ast = plancher_grammatical(source)
    if exigence_ast is not None:
        rapport.exigences.append(exigence_ast)
    visiteur = VisiteurStdlib(noms_lies(arbre), annonce_annotations_differees(arbre))
    visiteur.parcourir(arbre)
    for usage in visiteur.usages:
        confronter_usage(usage, ajouts, retraits, rapport)
    rapport.vermin = minimum_vermin(source, libelle)
    return rapport


def minimum(rapport: Rapport) -> tuple[int, int]:
    """Version minimale exigée (3.0 si rien de plus récent n'est vu)."""
    return max((e.version for e in rapport.exigences), default=(3, 0))


def maximum_exclu(rapport: Rapport) -> tuple[int, int] | None:
    """Première version où le code casse (retrait), s'il y en a."""
    return min((e.version for e in rapport.retraits), default=None)


def resumer_exigences(exigences: list[Exigence]) -> list[dict[str, object]]:
    """Une ligne par règle (première occurrence), triée par version décroissante."""
    vues: dict[str, dict[str, object]] = {}
    for e in sorted(exigences, key=lambda x: (-x.version[1], x.ligne)):
        if e.regle in vues:
            vues[e.regle]["occurrences"] = int(vues[e.regle]["occurrences"]) + 1
            continue
        vues[e.regle] = {"version": texte_version(e.version), "regle": e.regle, "ligne": e.ligne,
                         "raison": e.raison, "nature": e.nature, "verification": e.verification,
                         "occurrences": 1}
        if e.garde:
            vues[e.regle]["garde"] = e.garde
    return list(vues.values())


def decisives(rapport: Rapport) -> list[Exigence]:
    """Les exigences qui fixent le minimum du fichier."""
    plus_haute = minimum(rapport)
    return sorted((e for e in rapport.exigences if e.version == plus_haute), key=lambda e: e.ligne)


def rapport_vers_dict(rapport: Rapport) -> dict[str, object]:
    """Sérialise le rapport d'un fichier."""
    decisive = decisives(rapport)
    return {
        "chemin": rapport.chemin,
        "minimum": texte_version(minimum(rapport)),
        "a_cause_de": ({"ligne": decisive[0].ligne, "raison": decisive[0].raison,
                        "regle": decisive[0].regle} if decisive else None),
        "maximum_exclu": texte_version(maximum_exclu(rapport)),
        "plancher_ast_feature_version": texte_version(rapport.plancher_ast),
        "vermin": rapport.vermin,
        "exigences": resumer_exigences(rapport.exigences),
        "exigences_gardees": resumer_exigences(rapport.gardees),
        "retraits": resumer_exigences(rapport.retraits),
    }


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait les intitulés du contrat de mesure depuis la docstring."""
    contrat: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if ligne[:1].strip() and tete in INTITULES:
            courant = tete
            contrat[courant] = []
        elif courant is not None and tete:
            contrat[courant].append(tete)
    return {cle: " ".join(valeur) for cle, valeur in contrat.items()}


def synthese(rapports: list[Rapport], cible: tuple[int, int] | None) -> dict[str, object]:
    """Minimum global, maximum exclu global et verdict vis-à-vis de la cible."""
    globale = max((minimum(r) for r in rapports), default=(3, 0))
    responsables = [(r.chemin, e) for r in rapports for e in decisives(r) if e.version == globale]
    casses = [m for m in (maximum_exclu(r) for r in rapports) if m is not None]
    plafond = min(casses, default=None)
    resultat: dict[str, object] = {
        "minimum_global": texte_version(globale),
        "a_cause_de": [{"chemin": c, "ligne": e.ligne, "raison": e.raison} for c, e in responsables[:10]],
        "maximum_exclu_global": texte_version(plafond),
        "incompatible": plafond is not None and globale >= plafond,
    }
    if cible is not None:
        resultat["cible"] = texte_version(cible)
        resultat["cible_satisfaite"] = globale <= cible and (plafond is None or cible < plafond)
    vermins = [r.vermin for r in rapports if r.vermin is not None]
    if vermin is not None:
        resultat["vermin_accords"] = sum(r.vermin == texte_version(minimum(r)) for r in rapports)
        resultat["vermin_ecarts"] = len(vermins) - int(resultat["vermin_accords"])
    return resultat


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #

def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description=("Calcule la version minimale de Python qu'exige un code source, ligne "
                     "par ligne (syntaxe, stdlib, grammaire ast), et la version à partir de "
                     "laquelle il casse (retraits de la stdlib)."),
        epilog=(f"Exemple : python {RACINE / 'version_python_minimale.py'} src/ --cible 3.9 --json\n"
                "Codes : 0 rien à signaler, 1 cible non satisfaite / incompatibilité / fichier "
                "non analysable, 2 entrée invalide, 3 rien à examiner."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="+", type=Path, help="fichiers .py ou dossiers à analyser")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="base des chemins relatifs et de l'affichage (défaut : dossier courant)")
    parseur.add_argument("--cible", default=None, help="version visée, ex. 3.9 : code 1 si le code ne s'y exécute pas")
    parseur.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="octets maximum par fichier")
    parseur.add_argument("--max-fichiers", type=int, default=MAX_FICHIERS_DEFAUT, help="nombre maximum de fichiers")
    return parseur


def libelle_chemin(chemin: Path, base: Path) -> str:
    """Chemin affiché, relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def analyser_tous(fichiers: list[Path], explicites: set[Path], base: Path, taille_max: int
                  ) -> tuple[list[Rapport], list[dict[str, str]]]:
    """Analyse chaque fichier ; un fichier explicite illisible est une entrée invalide."""
    ajouts, retraits = charger_table(AJOUTS_STDLIB), charger_table(RETRAITS_STDLIB)
    rapports: list[Rapport] = []
    rejets: list[dict[str, str]] = []
    for chemin in fichiers:
        libelle = libelle_chemin(chemin, base)
        try:
            rapports.append(analyser_fichier(chemin, libelle, taille_max, ajouts, retraits))
        except EntreeInvalide as erreur:
            if chemin in explicites:
                raise
            print(f"non analysé : {erreur}", file=sys.stderr)
            rejets.append({"chemin": libelle, "raison": str(erreur)})
    return rapports, rejets


def afficher_humain(sortie: dict[str, object]) -> None:
    """Affichage lisible par un humain."""
    print(f"version_python_minimale — {sortie['denominateur']} fichier(s) analysé(s), moteur {sortie['moteur']}")
    for fichier in sortie["fichiers"]:
        cause = fichier["a_cause_de"]
        detail = f"ligne {cause['ligne']} : {cause['raison']}" if cause else "aucune construction postérieure à 3.0 vue"
        casse = f" ; casse à partir de {fichier['maximum_exclu']}" if fichier["maximum_exclu"] else ""
        compare = f" ; vermin : {fichier['vermin']}" if fichier["vermin"] else ""
        print(f"  {fichier['chemin']} : Python >= {fichier['minimum']}  ({detail}){casse}{compare}")
    bilan = sortie["synthese"]
    print(f"Global : Python >= {bilan['minimum_global']}")
    for cause in bilan["a_cause_de"]:
        print(f"  à cause de {cause['chemin']}:{cause['ligne']} — {cause['raison']}")
    if bilan["maximum_exclu_global"]:
        print(f"Casse à partir de Python {bilan['maximum_exclu_global']} (retrait de la stdlib)")
    if "cible" in bilan:
        etat = "satisfaite" if bilan["cible_satisfaite"] else "NON satisfaite"
        print(f"Cible {bilan['cible']} : {etat}")


def afficher_json(sortie: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def code_retour(sortie: dict[str, object]) -> int:
    """0 rien à signaler ; 1 cible manquée, incompatibilité ou fichier non analysable."""
    bilan = sortie["synthese"]
    if bilan.get("cible_satisfaite") is False or bilan["incompatible"] or sortie["non_analyses"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    base = (args.racine or Path.cwd())
    chemins = [c if c.is_absolute() else base / c for c in args.chemins]
    if vermin is None:
        print("vermin absent : comparaison omise, analyse stdlib seule (ast + tokenize + table vérifiée)",
              file=sys.stderr)
    try:
        cible = lire_version(args.cible) if args.cible else None
        fichiers, explicites = collecter_cibles(chemins, max(1, args.max_fichiers))
        rapports, rejets = analyser_tous(fichiers, explicites, base, args.taille_max)
    except EntreeInvalide as erreur:
        print(f"entrée invalide : {erreur}", file=sys.stderr)
        return 2
    examines = [r.chemin for r in rapports]
    sortie: dict[str, object] = {
        "outil": "version_python_minimale",
        "moteur": "stdlib" if vermin is None else "stdlib+vermin",
        "interpreteur": f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}",
        "denominateur": len(rapports),
        "examines": examines[:MAX_EXAMINES],
        "examines_tronques": len(examines) > MAX_EXAMINES,
        "non_analyses": rejets,
        "synthese": synthese(rapports, cible),
        "fichiers": [rapport_vers_dict(r) for r in rapports],
        "contrat": extraire_contrat(__doc__ or ""),
    }
    if args.json:
        afficher_json(sortie)
    if not rapports:
        print("dénominateur nul : aucun fichier Python analysable, rien à examiner", file=sys.stderr)
        return 3
    if not args.json:
        afficher_humain(sortie)
    return code_retour(sortie)


if __name__ == "__main__":
    raise SystemExit(main())
