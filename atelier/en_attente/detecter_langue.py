"""Dit en quelle langue est un texte, avec quelle confiance, et s'il mêle plusieurs langues ;
refuse de conclure quand le texte est trop court pour en décider.

Mesuré dans cette session (python corpus_detecter_langue.py stdlib,lingua,langdetect
20,30,40,80,160 100) : sur 700 extraits de traductions humaines de Django (fr, en, es, de,
it, pt, nl), ce repli stdlib donne la bonne langue à 98,4 % des extraits de 40 lettres et à
100 % de ceux de 160 lettres (lingua 2.2.0 : 99,0 % et 100 % ; langdetect 1.0.9 : 97,1 % et
100 %), mais à 95,9 % seulement à 20 lettres : d'où le refus de conclure sous 40 lettres.

QUESTION
    En quelle langue est ce texte, avec quelle confiance, et est-il mélangé ?
MESURE
    1) Écriture dominante : chaque lettre est rangée selon le premier mot de son nom
    Unicode (latin, cyrillique, arabe, CJK, hangul, devanagari...) ; une écriture propre
    à une langue la désigne (hangul : ko ; kana : ja ; grec : el...), certaines lettres
    départagent (ukrainien, serbe ; persan, ourdou). 2) En écriture latine, Bayes naïf
    sur les trigrammes de caractères : log-vraisemblance moyenne selon le rang du
    trigramme dans le profil de la langue (loi de Zipf ; 300 trigrammes par langue pour
    19 langues, tirés des profils de langdetect), plus la part de mots-outils fréquents
    pour fr, en, es, de, it, pt, nl ; confiance = part softmax du meilleur score.
    3) Les paragraphes sont regroupés jusqu'au seuil ; une autre langue sûre sur au
    moins 5 % du texte le rend « mélangé ».
HYPOTHÈSES
    Le texte est de la prose ; le code, les listes de noms propres et les tableaux de
    chiffres n'ont pas de langue. Un paragraphe est écrit dans une seule langue.
LIMITES
    Sous 40 lettres (un idéogramme, un kana ou une syllabe hangul en valent trois)
    l'outil refuse de conclure (code 3). Le repli stdlib ne connaît que 19 langues en
    écriture latine : une autre est rendue incertaine ou prise pour une voisine. Les
    langues proches (es/pt/ca, da/no/sv) se confondent sur des blocs courts. Le mélange
    n'est vu qu'entre paragraphes, pas à l'intérieur d'une phrase.
CONTRE-EXEMPLES
    Constaté : « Ingrese dos números enteros. Ingrese dos números. Ingrese dos
    fechas/horas válidas. Ingrese dos fechas válidas. » (espagnol) est rendu pt avec
    une confiance de 0,93 ; sur 50 extraits catalans de 160 lettres, 29 sont pris pour
    de l'espagnol (le catalan n'a pas de liste de mots-outils).
INVOCATION
    {outil} {fichier} --json
    {outil} --texte "Ceci est un court paragraphe écrit en français pour vérifier la détection de la langue." --json
DOMAINE
    Prose en langue naturelle (réponses de LLM, documents, messages) d'au moins le seuil
    de lettres ; fichiers texte UTF-8 jusqu'à quelques dizaines de mégaoctets.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import lingua as _LINGUA
except ImportError:
    _LINGUA = None
try:
    import langdetect as _LANGDETECT
except ImportError:
    _LANGDETECT = None

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
INTITULES = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
             TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)

MOTEUR_STDLIB = "stdlib"
MOTEUR_LINGUA = "lingua"
MOTEUR_LANGDETECT = "langdetect"
ECRITURE_LATINE = "LATIN"
ENCODAGE_ATTENDU = "UTF-8"

CODE_RIEN = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_VIDE = 3

SEUIL_LETTRES = 40
POIDS_DENSE = 3
CONFIANCE_MIN = {"stdlib": 0.60, "lingua": 0.40, "langdetect": 0.60}
PART_MELANGE = 0.05
TEMPERATURE = 8.0
POIDS_MOTS = 1.0
OCTETS_MAX_DEFAUT = 32 * 1024 * 1024
LETTRES_ANALYSEES_MAX = 200_000
EXAMINES_MAX = 50

MOTS_OUTILS = {
    "fr": "de la le et les des l' à en d' un une du est que il pour qui dans a pas au sur ne "
          "plus par se ce n' avec qu' sont elle ou son sa ses mais cette aux s' on nous vous "
          "leur été être c' tout comme fait j' y ces je ont peut entre aussi très sans sous "
          "même deux où donc alors tous autre après avant encore ainsi lors dont chez notre "
          "votre faire si",
    "en": "the of and to a in is that it for was on are as with be by this at have from or "
          "not an they which you he but his we can has had were all their there been will "
          "one would what if more when who so about no she them these its than other into "
          "some could only also should may each how then your any our must does do",
    "es": "de la que el en y a los del se las por un con no una su para es al lo como más "
          "pero sus le ya o este sí porque esta entre cuando muy sin sobre también me hasta "
          "hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante "
          "ellos e esto antes algunos qué unos yo otro otras otra él tanto esa estos mucho "
          "nada muchos cual poco ella estar estas puede son está ser fue han ha",
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als "
          "auch es an werden aus er hat dass sie nach wird bei einer um am sind noch wie "
          "einem über einen so zum war haben nur oder aber vor zur bis mehr durch man sein "
          "wurde sei kann diese dieser wenn wir ich ihr können sehr unter keine kein muss "
          "schon zwischen gibt ob alle wo",
    "it": "di e il la che in a per un del è non una con i le si da della al l' lo gli dei "
          "nel alla come più ma sono anche ha delle se questo o nella ci essere suo sua loro "
          "tra dal degli quando molto questa già solo dopo cui fra sul ne mi era perché "
          "tutto tutti stato hanno ancora quale sempre cosa deve può fare così senza nei "
          "sulla dell' un' all' dall' c'",
    "pt": "de a o que e do da em um para é com não uma os no se na por mais as dos como mas "
          "foi ao ele das tem à seu sua ou ser quando muito há nos já está eu também só "
          "pelo pela até isso ela entre era depois sem mesmo aos ter seus quem nas me esse "
          "eles estão você tinha foram essa num nem suas meu às minha têm numa pelos elas "
          "havia seja qual será nós lhe deles essas esses pelas este fosse dele são pode",
    "nl": "de van een het en in is dat op te zijn voor met die niet aan er om ook als dan "
          "bij of wordt door maar naar uit nog worden over kan hij ze zo wat deze al tot wel "
          "geen heeft moet was waren meer hun wij we je u ik haar onder tegen tussen na "
          "hebben kunnen zal zou werd dit hier nu mijn uw alle veel omdat waar toch alleen zij",
}

# Profils de trigrammes : les 300 trigrammes intra-mot les plus fréquents par langue, par rang
# décroissant, extraits des profils de langdetect 1.0.9 (Apache-2.0, calculés sur Wikipédia).
TRIGRAMMES = {
    "fr": (
        " de|de |es |le | le| un| la|ne |est| es|la |st |nt |on |re |et |ent|ion|en | co| en|"
        " et|un | à |ns |une|que| pa| l |par|ur |ue |tio| du|des|te |lle|les|du |is |ans|ant|"
        " d | pr|ati|men|ran|iqu| au| da|dan|se |eur|er | ma|ée |ie | po|com|ais| so|ce | qu|"
        "eme| fr| dé|our|me |ien|con|ill|art| su|fra| mo|ain| ré|ist| no| ch|it | se|ell|in |"
        "té |omm|ire|ar |au |tre| ca|il |ont| si| in|son|res| an| ét|rs |ale|nce|ine|ons|ise|"
        "ali| sa|qui| re|nte|and|ort| il|us |anc|sit|nne|ts | di| ou|pro|onn|ier|anç|ux | né|"
        "itu|nça|ui | vi|çai| fo|ste|rie| ce|éri|al |né |ter|rti|ou |cha| tr| al| ba|tra| pe|"
        "ers|che|an | ar|int|éta|lis|teu|sur|lan| li|bre|sse| gr|tai|mun|rte|air|ge |ntr| ro|"
        "tem| pl|ait|pou|ita| fa|mar|man| lo|ois|rt |ère|lie|ica|tan|mmu|tué|ssi|ues|str|ond|"
        "ric|all|ver|égi|uni|ari|tiq|ure|ris| do|rat|iti|nis|mme|ité|rég|aut|nom|cti| mi|ut |"
        "el |ite|ess|gio|lit| or|mon|tes|rou| av| fi|nde|ive|ang|age|cie| te|lem|nal|enn| a |"
        "dép|emb|gra|ouv| me|uée|for|he |ori|ect|mbr|tat|née|ass|urs|sti|épa| th|aux|nd |nes|"
        "pe | ha|tal|gne|iss|ren|rd |rit|nat|uve| am|ens|tie| jo| ap|omp| el|sio|éra| br|ona|"
        "nti| bo| to|tri|lus|err|és |oir|ani| cr|ron|ili|ins|ate|ous|act|cou|nie|ieu|ord|nci|"
        "por|uis|ern|mil|ées|mat"
    ),
    "en": (
        " th|the|he | in| of|of |in | an|ed |nd |and|is |on | a |er | is|an |ion|as | co|es |"
        "ing|ng |al |tio|ent| wa|or | to| fo|ati|ter|st |ate| re| ma|for|to |was| pr|th | st|"
        "ted|re |ly | se|nt |ist| on| de| ca|by |en |at | it|ry |ty | as|sta| be|ce | by| fr|"
        "ne |ica|it |all|ts |le |com| pa|ers| ar|ch |ame| so|pro| wh| wi| ch|ver|est|ive| no|"
        " al| he| ba| bo|ian|lan|con|ic |her|ber| di| fi| or|str|oun|te |ric| mo|uni| ha|rom|"
        "rs |eri| un|ia | la| po|ons|nal|nce|res|ine|om |man|men|ns |art|ish| me|ll |tra|ste|"
        "rn | li|ort|se | lo|cal| na|ity|par|iti| si| te|mer|ies|ect|tor|me |can| hi|are|fro|"
        " at| ne|ern|ona|ve |tat|ali|ge |ith|ar | su|ite| s |per|nte|ast|der|int|tic|ere|own|"
        " br|ove| we|us | mi| sp|nat| le|out| ro|ran|ral|nde|ain|era|cti|sh |his|rat|eas|cha|"
        "rin| en|tin|wit|lis|und|cat|ill|sed| tr| gr|ess|mbe|rit|rea|ay |mar| pe|pla|tha|ele|"
        "ear| ho|ser| sh| sc| wo|orn|emb|rt | pl|lle|de | fa| ra|one|ary|ld | ge|wn |lin|ari|"
        "ich|tri|lit|hat|tur|inc|rd | sa|ant| mu|igh|nit|omp|orm|son|ani|age|pre|bor|ide|lat|"
        "nor|red|dis|anc|cou|cia|sti|unt|ass|eve|ase|ina|ard|min|ust| am|ind|uth| au|enc|ren|"
        "wor|tes| bu|ial|rou|eat|rth|use|nti|ese|lea|sio|ord|sin| vi|ss |our|chi| ac|hic|ey |"
        "el |et | ce|tiv|rie|ong"
    ),
    "es": (
        " de|de |es | la|el |la | en| es|en |os | co| un| el|ent| y |as |na |ón |do |ue |nte|"
        "ión|te |con|al |ado| po|una|to |ia |or | ca| se|ra | lo|del|que|aci|est| re|un |ica|"
        " pr|da |ció|ant|com| qu| pa|on |los|sta|ta |par|ist| su|por| ma| di| al|men|se |no |"
        "re |ada|cia| a |io | in|nci|ro |ran|ca |ida|dad|res| fu| pe|ien|nto|co |las|era|ter|"
        " si|pro|ico|per|esp|ion|art|str|mo |tra|ido|ad |fue| no|ero|ici|can|bre|ina|an |ona|"
        "cio|nta|anc|ar |ito|er |and|ali|dos| ba|ara|tor|ene|ntr|lo |uni| sa|ale| fr| me|mun|"
        "les|des|ita| ha|ía |eci|ame|ste|cie|rit|tic|sa |den|eri| so|rte|ari|omo|rio| te|tri|"
        "dis|nes| ar| tr|ano|esa|tam|tad|enc|mar| an|lla| mu|one|man| mi|ria| cu|lia|tal|ili|"
        "fra|tro|ma | ci|ces|mbr|int| o | mo|ana|nal|cid|su |inc|nic|lan|sti|rta| gr|reg| or|"
        "ura|nti|tan| na|egi|ori|ten|pre| ju|tes|nda|ort|ndo|ner| vi|orm|lac| fa|car|ert|spa|"
        "ill|nce|cal|rma|mer|año|rad|for|pri|ont|pañ| ta|le |tre|omu|fic|pec|ami|nac| ch|ovi|"
        "itu|gra|ne |gen|ide|oci|iza|ial|cas|tos|rec|nde| le| ac|gió|tua|mil|ier|dor|ric|err|"
        "go | li|ral|ono|ian|ino|ers|bla|cad|spe|ren|end|nid|min|dep|edi|obl|ons|ras|der| pu|"
        " ro|sto| do| ve| to|nom|us |ast|und|arr|lic|ore|ros|sit|qui|dic|son| ce|epa|ani|ula|"
        "lle|ens|uer|tiv|esi|ie "
    ),
    "de": (
        "er |en | de|der|sch|ein|in | ei|che|ist|nd |ie | in|st | un|ich|und| is| di|isc|die|"
        "ine|ch |es |on | be|nde| au|hen|ste|ter| vo|ung|ne | ge|den|ten|and|ng |gen|ver| st|"
        "te |von|sta|im |ber| im|des|he |rei|her|de | si| da|ent|ers|it | ve| mi|us |aus|eit|"
        "lan|lic|ion| al|ind|em | zu|nte|cht|as |mit|nge|ner| we| er|ren|ach| sc|nis| ma| an|"
        "men|ere|ern|rt |et |rde|eic| wa|das| se|cha|eis|est| re| ha|tsc|an |nen|ar |ien|ige|"
        "ier|ert|eut|is |lle|ell| wi|eri|eme| la|lie| na|tio|ati|chi|ens|auf|end|sse|uch|ls |"
        "ger| gr|um |dem|sen|le |war|ges|el |als|tei|ler|rte|deu|rie| pr|sie|wei| ka|nt | li|"
        "kan| he| ba|ann| fr|mei|ode|zei|ing|uts|tel|len| me|ort| am|ge |art|unt|hei|tte|tra|"
        " en|all|hre|ran|ani|chn|gem| fü|ei |bei|ben|str|iel|ite|alt|ene| sp|ngs|ech|ht |re |"
        " so|tad|eil|tun|haf|pro|ame|tli|aft| ja|adt|hne| bi|erg|urd|ali|auc|für|ür | ko|rn |"
        "lis|ete|ang|ur |mer|ahr|run|se |nst|ass|rch|man| no|sei|ele|rst|uf |lei|chl|wur|ins|"
        "wer| le|bez|rd | wu|rg |hau|chs|geb| od|al |ese|kre|ede|ege|ied|tis|dt |ris|ft |ord|"
        " po| co|lt | sa|erb|spi|ate|ale|ser|lte|pie|lin| te|ess|ebe|rin|rsc|sis| br|tan|ant|"
        "reg|mal|nal|at |era|bes|ika| ne|elt| or| ar|ieg|eze|sic|am |sti|net|the|erl|tor| ch|"
        "nie|ini| pa|for| bu|iti"
    ),
    "it": (
        " di| de|la |to |di |del|ell| un| co|ne |lla|el | è | in|ent|le |ion| ne|ta |un |nel|"
        "re |ato|ia |one| la|te |no | da|na | pr| e |il | al| il|ti |nte|in |con|ica|com|zio|"
        " ca| si|per|ita|ant|ale|all|men|he |ca |che|se |sta|ra |ll | st|io |nti| ma|ter|par|"
        "tto|ett| ch|da | se| re|gio| pe| pa| su|er |li |ese|ata|art|al |ist|una|ran|on |tra|"
        "lo | a |azi|si |tan|ni |ali|eri|co |nto|gli|anc| an|tat|att|pro| so|nta|ati|lle|dal|"
        "rat|pre|ro |tic|ari| tr| te|tor|era| ri|ico| no| po| mo|me |tà |ori|ri |ina|oni| fr|"
        "ore|ess|est|str|ano|rti|olo|res|mun|ono|sti| le|bit|ma | l |omu|reg|fra|ric|and|cia|"
        " pi| qu|so |ona|ces|sit|are|itu| fi|inc|de |abi|ei | me|ome| vi|tal|pri|ont|chi|une|"
        "lia|ime|ito|egi|ipa| fa|ian|tua| sc|ste| ci|col|itt|ass|ici|ene|ssi| ab|do | sa|tti|"
        "nce|int|tro|ond|ria| es|uat|tri|tte|ere|ten| or|ver| ba|ine|man|uni|nci| gr| ar|cat|"
        "ggi|ani|ing|car|ola|nat|nal|zza|ame| li|tta|sa |va |ntr|tim|ost| gi|ers|ità| lo|lic|"
        " ro|ero| fo|sso|ie | mi|ini|dei|sse|son|cen|ndo|llo|ret|ris|sco|ide|sto| ve|izz| i |"
        "mo |mon|ura|dis|lin|fic|can|rit|ino|oli|ce |cit|lit|rte|agg|gra|po |sci|ann| sp|ott|"
        "izi|ven|rio|za |ili|an |qua|dip|ser|ara|ana|min| do|ndi|rim|mar| fu|for|tre|ate|cor|"
        "rin|ort| ra| na|que|pol"
    ),
    "pt": (
        "de | de|do | um| co|os |da |ma |ão | é |com|as |uma| da|ent| do| e |na |ia |es | po|"
        " se| no|nte|ado| a |no | es|um |em |to |te |al |ra |est|ida|dad| re| o | na| pr|or |"
        " em|ro |ade|ica| pa|con| ma|ant|ist| pe|men| ca|ção|por|om | qu| fo|par|que|ada|ste|"
        "sta|ita|io |ens| di|ter|ta | ha|nto|dos|str|ran|tra|ue |ca |se |is |eir|mun|ndo|hab|"
        " in|ame|res|cen| km|ali|açã|cia|cid|tes| su|m² |km²|nci|reg|pro| te|oi |foi|per|co |"
        "nde|sa |art|ou |ico|and| as|den|tan|ano| an|min|ria|ten|ara|ort|tad|mo | ci|und|end|"
        " ce|nce|ina|bit|la | ba| fr|iza| lo| al|egi|ito|rea|ati|ião|ras|er |ntr|iro|uni|tiv|"
        "omu|ona|des|nda|ric| ou|giã|tri|lo |ais| os| br|cal|va |ar |sid| me|ido|egu|liz|era|"
        "tam|anc|re |ela|esp|rte|ea |esa|rio|tal| mu|bra|ura|abi|int|nsi|ide|são|ha |ver|ion|"
        "tic| ár|dia|nic|pos|eri|ini|nta|can|oca|rat|iva|pel|áre|fra|zad|ast| en|das|nal|una|"
        " sa|mar|ua |rta|ont|tro|nis|ira|tor|pri|omo| mo| or| mi|ces|lia|rit|man| si|gun|nos|"
        " tr|for| gr|seg|cio| fa|ora|loc|ula|nha|ici| ex|ana|ond| ar| li| vi|pre|rad| ad| la|"
        "tur|gra|sil|mai| at|ho |tos|ab |rin|dis| am|asi| so|sti|tem|dep|ime| fi| ch| jo|oss|"
        "lan|ele|ons| ve|orm|nso|car|dor|ian|ias|ess|dmi|epa|nor|ome|elo|adm|on |nas|eci|sos|"
        "sen| ta|qui|rma|mer|inc"
    ),
    "nl": (
        "en | de|de |een|an | in| va| ee|et |van| he|is |in | is|het| en|er | ge|ent|te |se |"
        "oor|eme| be|sch|ie |der|ers|uit|aat|and|nde|el | ma|aan|ste|nte| te|ing|sta|eel|it |"
        "men| st|ond| di| ui|ver| vo|ans|lan|nt |mee|den| wa|ter|nse| me|nd |aar|or |laa|ts |"
        " op|at |gen|pla|dee|gem|erd|ten|ijk|rs |ats| we| pl|tel|est| re|as |maa|cht|ng |lt |"
        " ve|eri|one|lij|rd | al|ord|ede|es |ner|voo|ren|akt|on |che|kt |art| co|isc|ran|eer|"
        " do|ere|die|nge| on|sse|won|ber|wer|aak|ens|nwo|rde|ij |was|op |taa|dis|ist|he |end|"
        "inw|par|ame|elt|str|reg|al |ron|egi| pr| na| fr|tie| to|mer|io |le |ati| ar|ijn|ar |"
        "met|ele|st | la| ne|rij|ken| gr|ns |jk | da| wo|ndi|cha|rte|gio|fra|us |doo|ari|lle|"
        " zi|ant|tem|ls |erl|ric|eld|per| pa|eli|len|pro|ich|ge |ne | ho|tri|re |iss|gel| no|"
        " ka| am|eve| aa|rik|ali|nds| bi| li|rt | ha|chi|bes|ch | po| sa|of |ika|ien| sp|ege|"
        " of|epa| hi|naa|jn |ht |als|stu|geb| ro|ige|eke|ind|uur|ier|els|rla|ven|ort|its|kaa|"
        " mo|am | oo|wor|ang|arr| ja| bo|sen| mi| le|ion| ca|ach|ot | br|ill|ned|dep| ba|raa|"
        "ger|erk|rli|ië | du|ke |tal|rie|nne| sc|zij|ad |ges| an|ty |ate|ove|rro|vin| ch|lin|"
        "aal|tuu|sem|lie|gro| wi|ct |eid|dt |the|id |ili| ko|ont|ld |rin|tse|ale|ict|dat| vi|"
        "nen| se|zie|na |orm|sti"
    ),
    "ca": (
        " de|de |es |la |el | la| un| i | el|ent| co|és |al |ia | a |del|na |nt | es|at |en |"
        " és| al|un | l |er | en|que| pe|ls | se| ca|men| qu| d |ió |per| re|est|ant|re | pr|"
        "ue |ts |ta |una| ma|ran|ra |els|va |les| an|com|par|da |ita| va|sta|tat| pa| fo|ica|"
        " di|ici|con|art|ar |ns |ció|res|ca |ist|uni| te|nic|aci| le| si|ame|any| no|ter|amb|"
        "ès | po|tre|des| in|era| mu| fr| am|ona|ada|mun|or |anc|cia|pro|cip|tan|fra|ons|tam|"
        "an |ion|ic |tal|nci|sit|reg|itu| ba|ou | mo|tua|rta|om |bre|str| ha| tr| ll| sa| o |"
        "mb |us |ten|nta|tic|ipi|tra|egi|fou|ina|ria|tor|ny | ar|ser|esp|ell|mar|nia| su|nts|"
        "cès|ncè|sa |pi |lla| so| me|is |als|ari|cat|uat|ues|lle| gr|for|eri|le |ura|dep|int|"
        "os |nte|ana|nom|epa|man|ntr| fi|eni|sti|ori|rs |gió|tri|ric|ste|cio|ers| ci| or| mi|"
        "ver|lit|ata| ta| vi|ali|ill|pre|tes|ren|ord|ll | fa|st |nal|lan|car|ma |ri |ir |orm|"
        "rt |ral|on | ac|esc|ats|ont|gra|eix|one|lia|dis|err|all|eu |tar|te |sen|it |nti|ort|"
        "nes|ect|rat|ial|ara|ner| ge|rma|and|tur|can| ro|nya|dor|ide|ado|abi|ene|cci|nat| ve|"
        "ere|ix |arr|ena|bit|qui|gen|cte| na| fe|ale|seg|omp|mer|ins| to|mon|den|alt|ol |arc|"
        "rre|l·l|hab|pri|seu|qua|nor|tit|uta|act|ula|fic|por| do|cal|ual|bar| oc|lar|ya |ass|"
        "aqu|rti|ret|ost| ex|cul"
    ),
    "ro": (
        " de|de |te | în|ul |est|ste|în |re | di| es|in |are| un|din|şi |le | a | co| şi| ca|"
        "ea |ia |ie | pr| al|al |ui | ma| pe|un |tă |ent| la|lui|or | fo| o |ate|ii |at |la |"
        " re|ei | cu|eri|ele|car|tat|ulu| se|st |ntr|ist|rea|tul| in| ro|ani|tor|ter|ita| ce|"
        "lor|aţi|ori|ale|com| po|pro|con| su|ri | pa|ne |rie| sa|tru|pri|an | st| ac|uni|cu |"
        "că |ost|rom|int|art|par|ica|ce |sta|ali| an|tre|str|nte|nia|au |pre|fos| fi|tic|se |"
        "ru | or| ar|men|nă |ată|ca |pe | si|ile|ric|tra|mân|per| li|uri|ion|ai |mar| tr|lit|"
        "nul|ic |ari| mo|ine|tea|nt | me|une|ra |ici|na |ţie|ică|mai|man|ona|omâ| lo|num|nd |"
        "era| mi|tur|ră | au|ilo|iun|rma|rin|rat|ere| lu|iei|ace|ran|mul|rul|pen|ili|lă | nu|"
        " te|atu|bri|cel|mbr|for| ap|mun|sti|ar |oar|ame|anu|tel|ati|nal| mu|loc|ril|and|ice|"
        "iul|ial|sau|cia|it |ta | sp|ind|lan|ian|on |el |itu|chi| no| do|cal|reg|cul|ite| ge|"
        "ză |tri|ant|rit|ria|scu|rii|er |ora|ina|nit|orm|emb|ect|ini| sc|nic|tiv|ţii|mat|elo|"
        "ut | le|olo|ită|ume| ba|tal|tan|edi|eni|ara|ni |lic| da|sit|lul| ju|ală|să |inc|ţia|"
        " gr|rte|ces|omu|şti|ula|eşt|egi|nie|oli|cur|nci|nea|ast| fa|mit|ări|rti|ons|rop|pul|"
        "erm|min|tar|rep|iza|nde|ito| ve|omp|ers|ver|ura|tin|res|âni|mer|oni| fr|es |înt|nta|"
        "cat|ser|ral|can|rec|lie"
    ),
    "sv": (
        "en |er | i |är | en| de|ch | oc|och| är| fö|ar |om |et |för| so|ing|and|an | av|den|"
        "av |som|de |tt |ter| me|ska|re |ka | va| st|var|sta|nde|are|nsk| ti|ill|sk | ha| in|"
        "ng |lan| fr|on |ll |ens|ade|til|isk| ma|ör |ett|ans|gen|ra |der|eri|es |na |med|ell|"
        "ber| på|dd |ven|era|föd|ödd|nd |att|nin|på |ver|det| an|ed |ion| be|ste| et|ta | sv|"
        " ko|ten|nge|sve|ist|ns | vi|lle| ka|und| sk|ler|rik|ers|lig|des|ati|han|str|lla|st |"
        " se|rna|as |kan|ad |ent|man|sam| sa|mar|mer|del|tio|sto| pr|pel| re|ran|lin| no|ån |"
        "ern| si|spe|frå|rån|tor|tar| ut| dö| gr|ika|all|ari|död|öd |la | li|tra|ela|in |upp|"
        "tal|at |kom| el|lar| at| un|sti|art|änd|tad|gar| fi| sp|har|örs|tta|ren| ba| al|ien|"
        "el |men|or |ger|one|ock| vä|els|son|nor|ser|rad|nte| la| fo|ord|pro|sen|ike| he|ner|"
        "omm|rs |tis| om|est| po|ker| ar|nds| br|te |rin| mo|kt |ort|mbe| tr|id |ige|ara|ete|"
        "kar|rat| pa|tte|al |emb|ts |ga |dan| da|mma| mi|ris|nom|kri|rst|ång| bo|ale|par|iga|"
        "per|oli|tan|ame|nga|ngs| na|len|kal|öre|rt |nda|år |rig|inn| ju|äst|da |ri |lit| bl|"
        "dra| ge| ja|for|ig |vid|ust| up|ons|nst| ta|nis|nar|tet| te|rie|org| fa|ins|ant| ro|"
        "ann|tat|nna|eda|nat|nne|nen| am|ast|lag|ate| pe|gra|lad|us |ali|nt |fra|amm|ds |rka|"
        " bi|ars|ge |iti|itt| th"
    ),
    "da": (
        "er |en |et | de| i | er| en| og|og |den|der|de |for|nde| af| fo|af |ter|and| me|lle|"
        " so|ere|ing|ed |lig|sk |det|re |om |ste|or | st| ti|ke |ne |ler|til|som|ger|lan|ske|"
        " be|ng | et|ind|es |end|il | fr|ar |te | ha|ans|nsk|ge | in|ell|isk|ion|med|nge|els|"
        " ko| på| da|på |ver|del|est|ige|nd |gen|eri|ede|fra|sta|le |dt | ma| bl| ve|at |ens|"
        "on |dan|an |ra |ern|men|ret| va|mme|sti|ati|ist|st | pr|gge|und| li|mer|ill| el|ers|"
        " la| sa|el |var|tio|kom|tte| re|sen| ud|ent|ner|se |lev| si|ord|nin|lse|str| no| ka|"
        " sk| an|rne|ns |ren|omm|ig | fø|ser| at|ang|ive| he|red|pro|ved|rin| gr|ble|ten|ove|"
        "nte|kan| se|ber|ev |eli| ba| op|kke| fi| om|man| br|har|nor| vi|ken|ide|ndt|nne|one|"
        " ho|old| sp|mun|ene|gt |igg|net| al|ien|al |mmu|lde|rik|sto|lin|mar|org|tet| am| mi|"
        "ker|nds|gne|art|tor| mo|tal| tr| un| sy|fte|ete|avn|orm| by|rer|ran|tis|rst|hol|ska|"
        " na| hv|per|tat|spi|age|kri|ale|is |nes|bet|em |nst|rg |sam|ris| ar|han|fød|ngs|ins|"
        "nal|ort|res|ødt| pa|in |ika|pil|rt |rde|ven|ogn| ro|des|rke|ame|ark|met|ate|amm|rie|"
        " po|dre|rig|nen|nis|hav| fa|une|sog|ant| ki|reg|ors| ty| fl|len| te|rte|tid|rd |by |"
        "min|ve |hed|tra|her|før|ade|vær|vet|eds|ore|rre|irk|lem|skr| pe|gel| ge|us |all|kon|"
        "ele| le| ta|rsk|år |egn"
    ),
    "no": (
        "er |en | i |et | de| er| og|og | en|om | so|den|som|for| av| fo|av |ter| me|sk |re |"
        "and|ne |ing|ste| et|det| ti|ke |ar |lle|de | st|lan|lig|ere|te |le |ell|ver|or |nde|"
        " ha|ed |ler| no|til|ng | på|der|ger|på | fr|isk| ko|nor|ske| va|est|il |nge|ten|tte|"
        "ene| ve|sta| bl|nne|ord|ent|sen| in| be|del|es |var|st |nsk|nd |fra|ens|ett|els|med|"
        "omm|ra | se| li|ren|ist|inn|on |ser|rt |an |ans| la|sjo|jon|ang|kom| ma|ner| el| fø|"
        " pr|tet|ge |mer|men|eri| re|gen|ert|mme|ble|und| sa|gge|ker|dt |all|nte| sk| ka|har|"
        "ers|ret|str| gr|nen| ut|ige| an|tt |ors| un|pro|nin|nes|mun|len|kke|ove|nt |rsk|ig |"
        "rik|end| by|mmu| br| sp| tr|ill|ved| si|one|ikk|une| ba|ber|al |fød|ska| vi| mo| he|"
        "ødt|sto|tal|rin|ate|nse| da|tor|ann|net|lt |ort|ia |in |opp|res|rer|asj|rte|sti|ns |"
        "kan| te|lse| ar| op|ete| fi|se |rde|ran|vin|art|ins|ale|man|dre| ki|tre|enn|ide|jen|"
        " na|lin|ele|han|per|tra| ho|ede| om|lag|ern|kap|ien|el |rst|igg|ris| al|elt|ll |rd |"
        " sø| mi|mar|eng|rke|org|tat|sis|ant|lke|tis| le|itt|ine|kon|kje|ite|ngs|att| pa| co|"
        "erk|ekt|ves|na | ro|nis|kri| to|rk |sse|sam|ken|mel| kr|ven| å |øst|tur|nn |sør|gre|"
        "nst|ion|før|tid|kin|lit|lom|rge|eli|ika|ati|ons|år | po|unn|kal|ina|ike|lsk|met| ne|"
        "ive|par| ga|ore|bru| ru"
    ),
    "pl": (
        " w | po|ie |nie|na |wie|ch |ski| na|ej | pr|rze|ego|go |ia |ny |owi|im |iej|ych|sta|"
        " i |kim|kie|prz|owa|cie|pol|ki |ce |nia| mi|dzi| wi|ów |ka |min|iec| ro| z | za| st|"
        "ani| do| je|pow| ko| si|czn| wy| wo| ma|ols|jąc|wan|ści|ach|mie| gm|gmi|poł|ona| od|"
        "dni| pa|ca |eni|ini|ne |woj|rzy|owy|wa |ji | ni|eci|em |ier|ku |oło|ii |owe|ym |ter|"
        "żon|oje|łoż|się|nyc| cz|ńsk|ożo|ię | re|kow|cho|ci |ódz|cji|cze|odz|est|twi|str|icz|"
        "rod|iel|wód|ist|jew|zie|nic|ośc|any|ast|ina| gr|ewó|ztw|cki|ają|do |zna|dzt|sto|sce|"
        "to |cy |owo|iem|ieg| ka|ran|oni|ana|zy |ane|lsk| te|neg|ieś|ost| pi|pro| lu|edn|ejs|"
        "lsc| la|ycz|st | li| to|pod|tyc|wsk|ion| de|nej|ent|ąca|ta |od | kr|czy|ący|row|par|"
        "nik|ska| ok|yst|ze |da | sz|jes| ob|eś |oli|sie|ich|hod|wy |acj|we |rok|dow|art|now|"
        "ste|tan|rsk|ows| ja|zen| mo|ść |war|oku|jsk|ują| ch|ko |okr|ony|lic|cza|lub|ła |zez|"
        "wia| sp|ra |ez |tow| we|ja |lan|aln| in|naj|ówn|za |któ| ba|ami|ek |lat| sa|mi |gra|"
        " or|tra|nym|cja|tór|mia|ali|odn|stw| an|ncj|jed|er |pie|zny|orz|tor|zec|ien|sko|men|"
        "iał| a |kon| kt|ość|era|eck|nio|ańs| wa|rac|wej|on |ame| tr|wo | sł| fr|szy| ur| ws|"
        "arz|at |trz| sk|ada|zon|wni|kra|ora|zne|ech|oid| al|ry |awi|ub |sty|ero|cow|je |tac|"
        "sze|anc|świ| ta|ata|ur "
    ),
    "cs": (
        " je|je |ní | po|ch | v | a |na | pr| na| se|ho |ce |ter|ou | ne| ro| st|ick|pro|ých|"
        "se |ost|em | př|ké |kte|ky |ské|sta|ého|ně | kt| ob| ve| do| by| ko|byl|ím |ka |la |"
        " vy| z |ký |ova| za|ku |ský|cí |sti|ích| ja|né |nsk|dní| ma| sp| če|lov|sou|edn|rov|"
        "str|ení|ová| le|ká | so|ist|ný | pa|ny |vní|le |cké|od | od|pře|jed|to |ko |ním|ná |"
        " me|ti |ící|pod|odn|ové|pol|sto| mo| te|kéh| ka|zna|ran|vé | ná|ie |nos|ast|řed|ový|"
        "ent|nic| zá|cký|ako| re|ve | sv| vý|tel|neb|ebo|mi |vá |en |ta |ech|ele|pra|van| ta|"
        "spo| li|tic|uje|cho|vět|tro|ým |slo|dy |kov|ém |st |ro |esk|lní|ice|tí |rod|men|ate|"
        " sl|ván|jíc|nov|ovi|bo | ně| kr|tra|jak|roz| ji|kon| s |ole|ati|čes|ci |yl | mě| ze|"
        "erý|kýc|let|ího|va | ho|sko|eri|kou|ání| an|ěst|níc|áln|tní|sky| ch|ra |ze |rav|lo |"
        "jí |nej|oce|měs|ven|ste|tor|ást|ros| de|výc|eck|ani|nýc|vat|ší | in|lad|ver|oli|roc|"
        "rý |vod| mi|sku|nou|rok| dr| sk| tr|pří|hod|olo|el |ská|ric|tov|oku|tře|kla|zem|eré|"
        " al|du |ina|do | to|dno|tav|lav|len|mu |ame|ek |alo|ují| fi|est|er | hr|ční|tak|lic|"
        "led|las| os|nu |ré |ens|tin|ty |čen|es |ace|dob| ce|kol|erá|žen| pl|zen|al |on |pad|"
        "čás|us |oje|rní|ezi| ba|ví |dru|při|níh|hra| o |ern| vo|tu | br|val| sa|nač|no |vel|"
        "ruh|chá|ion|ený|ají|stá"
    ),
    "tr": (
        "an |ir | bi|lar|da | ve|eri|ara|nda|bir|in |en |de |ler|lan| ya|ve |ınd| ol|nde|arı|"
        " ka| de|ın | ta| ba|ya |esi|ind|ır |er |ası| da| sa|ola|ile|dir|rin| ku| al|ak |den|"
        "dan| bu|lı |ini|ili|ele|nin| il|dır| ge|ar |ne |nın| ha|ri |le |sin|anı|si | ma|tar|"
        "ik |edi|li |rın|man|ine|sın|eti|len| be|rak|ılı| te|ki |ını| ar|yıl|na |sı |rı |ana|"
        "nla|ala|iye|tan| an|adı| yı|idi|eni|ulu|ama| me| ye|nan|isi|tür|on |aya|anl| se|eki|"
        "bil| tü| ad|ayı|iri|alı|ıla| ko| di|ni |ist|lla|lma|kar| in|la |al |bul|el |re |nı |"
        "yap| gö|un | so|tir|ran|yan|eli|raf|and|ter|ilm|mış|sta|tır|nya|ada|ığı|ur |ağl|ek |"
        "ma |ste|ra |ari|ras|let|ere|ard| do| pa| si|ce | gü|kle|iği|lın| iç|ğı | en|lin|rle|"
        "lik|lam|ort|uru|mas|et |mak|kur|fın| ni|akt|afı|kan| bö|olu| nı|ver|baş|böl|ına| po|"
        "ene|bel|ık |im |bağ|atı|ye | fi|una|lun|tin| ke|eya|kla|lık|çin|san|içi|ede|ril|apı|"
        "ken|tek|bu | kı| dü|yer| or|gel|ürk|ca |onu|miş|ıdı|kul|yon|üze|lu |gün|end|tem| i̇s"
        "|ği | ki|mer|ğlı|yar|öne|lge|ştı|kta|ull| mi|ren|şti|lle|rla|te |ark|ısı|eme| fa|yla"
        "|aki|emi|zer| li|irl|ti |iz |par|ali|dil|son|ış |ümü|ölg|ndi|ta |min| he|tur|mi |kte"
        "|üne|der| am|ula|at |iya|tle|erd|rma|gen|doğ|yun|por|any| ay|unu| to|alm|ans| şe|di "
        "|ekt|me | mü| ed|mek| fr"
    ),
    "fi": (
        "en |on |in | on|an |ta |ist|ja |sa |ssa| ja|sta| jo|nen|ine|ise| ka|ais|sen|aan|la |"
        "lla| ta|itt|all| se|lai|ka | ku|ala| va|na |li |est|ain| su|lli|een|tta|ett|ell|lis|"
        "iin|ste|vuo|ksi| ma|tä | sa| si| al|si | ko|taa| vu|oli|nna|iss| mu|ass| ol|aa |tti|"
        "val|ia |eli|än |oka|sti| ki|ill|ti |ust|isi|at |ast| pe|tel|lin|oit|maa|eri|sä |mis|"
        "den| la|jok| ke| tu| yh|ois|le |tet|per|ess|int|lle|tai|kan|sin| to|suo|imi|lä |toi|"
        "kai| pa| ra|nta|ien|uon|tii|onn|stä|ssä|lta|ikk|vat| el|kun|kuu|kse|us |ten|uom|ava|"
        "ses|rin|stu|se |ään|ans|min|va |uva|sto|ttä|oma|aja|men| pi|eis|kau|ali|ent|llä| ha|"
        "ina|un | te|ttu|ita|ulu|man| vi|ika|unn| po| he| lu|ama| me| ju|rja|joi| ni|nin| li|"
        "ite|oll|alt|et |ide| en|tee|see|nne|suu|tal|ome|uks|tar|lan|sis|yht| kä|alu|ens|ari|"
        "sii|kka|tu |tte|ija|osa|ma |mal|kir|ran|koi|lma|aik|sia|pal|utt|kaa|ost|ant|esi|ana|"
        "kin|lka|oim|ulk|tie|ri |ime|ait|tää|ut |tin|sit|ani|nim|ter|tan|mer|ila|tam|ai |äyt|"
        "lue|tei|nki|ann| le|its|nti|ian|mi |att|ohj|tio|ail|aal| ve|oin|nka|tun|uot| mi|lii|"
        "kuv|rit|ori|umi| pu|nsa| so|ki |ris|oss|kil|nni|rus|ati|aks| jä|iik|kas|uut|nsi|osi|"
        "net|iva|kki|saa| my|tse|jul|uod|iit|yks|ilm|uus|arj| ti|nte|ete|kal|tav| es|käy|aat|"
        "jan|ark|yhd|kon|nis|van"
    ),
    "hu": (
        " a | sz| az|en |az |an |egy|és |ak | eg| és|sze|ban|gy | me|ek |tt | kö|es |agy| ma|"
        "szt|ele|ben| ta|et |köz|ala| cs|ába| el|let|ai | al|ely| va|szá| ha| be| ne|ik | te|"
        " re|nak|lt |art| le|ren|alá| ki|end|ész|ere|ség|mel|ter|zer| fe|zet|tar|ett|csa|meg|"
        "al |us | am|tal|os |nek|len|osz|rto|ja |ok |is |el |ta | né|nt |sza| ke|toz|lak|ti |"
        "ia |on |ály|ete|ány|sal|ra |ent| mi|leg|nye|ék |int|ame|zó |ott|eze| ny|ros|at |án |"
        "ól |ébe|er | ál|ozó|mag| fo|re |bb |eri|for|gye|ált|lád|nem|áll|áro|ara|kor|jel|lye|"
        " ko|gya|ána| la|ül | an|ztá|vag|ság| fa| ka|tet| vo|olt| is| je| ba|ás |rsz|ény|ae |"
        "tás|ors|ly |tál|ák |rül| ké| pa|asz| vá|üle|hat| ho| ve|esz|ág |mán|vol|lla|orm|min|"
        "tel| os|ba |zen| vi|fel|ég |ul |én |lat|lék|fél| ré|ádj|rés|szl|djá|vez|ndj|ker|ell|"
        "szi|nev|erü| na|ida|zág|djé|ssz|yel|ati|oly|lyá|ny | tö|szo|mer|val|tés|elv|te |em |"
        "ato| he|yar|sz | ez|faj|atá|be | in|ét |élé|eti|gyi|ont|vár|si |omá|lle|eg | er|lta|"
        "rás|ová|lov|nag| de| má|eve|ez | ol|dae|zat|éne|ika|oro|jáb|rt | já|név|ése|ata|sa |"
        "mes|zlo|tes|ől |ver| mo|ran|ada|hoz|át |ill|tár|tot|rme| gy|se |ni |szé|ri | po|ar |"
        " so|mad|ang|tó |ve |bel|elő|zik|tek|yán|eke|vák|lam|jár|yik|más|dik|nál|ásá|nde|nya|"
        "ert|ten|lis|tén|úak|zak"
    ),
    "id": (
        "an |ang|ah |ng | di|ala| da| se|ada|lah| me|dal| pe|yan|kan| ya|di | ad| ke|ata|dan|"
        "ara| te|tan| be| in| ba|ia |ter|ber|nga|eng|ri |at |seb| ka|ari| pa| sa|ama|si |men|"
        "ran|per|aka|gan|era| de|ra |ya |da |dar|al | ma|nya|ing|ta |asa|asi|ai |ni |ela|mer|"
        "am | ko|ak |sa |upa|ebu|eri| ta|man|ngg|pen|lam|aha|ika|ar |sia|is |nta|ini|bua|tu |"
        "uk |ind| su|au |en |ung|esi|ma |eru|pad| pr|ten| la|un |ban|atu|ali|pat|er |uah|den|"
        " at|lan| ti|as |ant|bag|ntu|ndo|ana|aya|ik |one|mat|pro|nes|pak|ian|don|awa|and|in |"
        "tau|tah|ert|rup| ja|ga |han|ota|esa| an|aga| un|mem|ngk|tar|ita|tuk|emb|rik|ent|eta|"
        "ila|ena|sat|kat|sal|ate|na |bah|eba|eca|eh | na|ole|ur |pan|kar|san|kot|ahu|ili|nda|"
        "gai|kec|ka |aan|unt|on |leh|has|nan| ol|nsi|apa|erb|end|nal|ula|tak|dia|sel|mas|us |"
        "abu|hun|ist|ut | ha|erl| si|tas|rta|ora|gka|dis|cam|sar|ser|int|rat|lik|gga|bar|ins|"
        "any|isi|ion|kab|ers|gi | bi|mba|una|ir |kal|gar|ti |uta|ovi|pem| bu|ani| ju| ra|adi|"
        " mu|nam|art|bup|ima|des| pu|vin|gun|rov|ain|lat|ati|rah|rma|wa |ina|epa|tem| re|rin|"
        "iri| wi|emi|ura|let| al|rle|uni|tim|ris|lay|uan|nja|eme|ki |erm| le| po| je|tra| ne|"
        "bel|es |tin|mil|ndi|str|agi| fi|dir|eka| mi|elu|la |ebe|kel|ras|dik|nak|uat|sta|nis|"
        "isa|il |tam| ge|uka|eor"
    ),
    "vi": (
        "ng | th|ểc |ển |ểt | tr|hể | là|là | mể|nh |thể|ểng|ểi | để|mểt|iển| ch| tể| ph| cể|"
        " hể|ân |ưểc|uểc|ểa | nh| ng|ong|ểm | ể |ron|tro| sể| bể| vể|thu|huể|ên | có|có | và|"
        "cểa| lo|trể|ông| kh|ểu | qu| bi|ểnh|ày |tển|ài | đô|và |rển|biể|sể |ch | nư|nưể| gi|"
        "oài| ba|loà| ca|hân| nà|này|đển|ae | đư|uyể| na| dâ|the| nă|dân|ia |đưể|ăm |an |phá|"
        "ung|năm|để |ùng|tru|yển|đô |áp |am |ây |tể |es | lể|ida| ti|quể| cá|run|nam|ểp | vi|"
        "háp|dae|nhể|ưểi|hển|hiể|ác |hểc|ang| ho|ét |huy| nể|chể|mét|hàn|ngư|gưể| vù|vùn| di|"
        "ne |iểu|hểi|ành|ao |phể| hu| ki| tâ|ban|bển|các|ình|hán|iểt| ha| đi|ưển| tí|tây|he |"
        " ma|hu | co|diể|and|tiể|vểt|ích|điể|chi| an| mi|anh|uển|viể|châ|rên|trê|ra |khu|is |"
        " la| sa|on | ểc|tíc|thá|nểm|iên|lan|ểy |sển|đểc|bểc|giể|ay |mểc| nó|hểt|cao|tra|thà|"
        "inh|hoa|vểc|oa |nó | kể|la |áng|in | vu|hi | dể| mé| gể| bì|bìn|us | hà|nd |ía |đôn|"
        "ươn|ơng|phí|en |hía|thâ|ính|vể |uôn|đểi|mểm|iểm|vuô|bể |ai |kể |hểy|gia|le |án |ain|"
        "ine|kil|ent|ilô|ômé|lôm|ter| de|eo |ngà| li| in|heo|ill|tểi| bư|ell|bưể|er |hà |ào |"
        "ểo |ưểm|re |ari|gày| al| hi|chí| pa|cển| to|ểch|êm |âu |hoể| tì|hôn|ngh|ran|phi|tri|"
        "cha| xã|xã |de |ìm | cô|man| no|thi|nha|tìm|ha |te |lla|nt |hín|ing|cho| ar| đê| tê|"
        "tên|đêm|vểi| mo| há|ngu"
    ),
}

ECRITURES_LANGUES = {
    "HANGUL": "ko", "HIRAGANA": "ja", "KATAKANA": "ja", "GREEK": "el", "HEBREW": "he",
    "THAI": "th", "GEORGIAN": "ka", "ARMENIAN": "hy", "BENGALI": "bn", "TAMIL": "ta",
    "TELUGU": "te", "GUJARATI": "gu", "GURMUKHI": "pa", "KANNADA": "kn", "MALAYALAM": "ml",
    "ETHIOPIC": "am", "KHMER": "km", "LAO": "lo", "MYANMAR": "my", "SINHALA": "si",
    "TIBETAN": "bo", "DEVANAGARI": "hi", "CJK": "zh", "CYRILLIC": "ru", "ARABIC": "ar",
}
ECRITURES_DENSES = frozenset({"CJK", "HIRAGANA", "KATAKANA", "HANGUL"})
LETTRES_DEPARTAGE = {
    "CYRILLIC": (("uk", "іїєґ"), ("sr", "ђћџљњј"), ("be", "ў")),
    "ARABIC": (("ur", "ٹڈڑںے"), ("fa", "پچژگ")),
    "DEVANAGARI": (),
}

RE_MOT = re.compile(r"[^\W\d_]+'?")
RE_LETTRES = re.compile(r"[^\W\d_]+")
RE_PARAGRAPHE = re.compile(r"\n[ \t]*\n+")


class ErreurEntree(Exception):
    """Entrée illisible ou invalide : porte le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


# -- profils -----------------------------------------------------------------

def trigrammes(texte: str) -> Counter[str]:
    """Trigrammes de caractères des mots (apostrophe = séparateur), bornés par des espaces."""
    compte: Counter[str] = Counter()
    for mot in RE_LETTRES.findall(texte.lower()):
        borne = f" {mot} "
        compte.update(borne[i:i + 3] for i in range(len(borne) - 2))
    return compte


def construire_profils() -> dict[str, tuple[dict[str, float], float]]:
    """Par langue : log-probabilité de chaque trigramme selon son rang (loi de Zipf),
    et plancher pour un trigramme absent du profil (quatre fois moins que le dernier)."""
    profils = {}
    for langue, morceaux in TRIGRAMMES.items():
        liste = "".join(morceaux).split("|")
        norme = sum(1.0 / (rang + 1) for rang in range(len(liste)))
        logp = {tri: -math.log((rang + 1) * norme) for rang, tri in enumerate(liste)}
        profils[langue] = (logp, -math.log((len(liste) + 1) * norme * 4))
    return profils


PROFILS = construire_profils()
MOTS = {langue: frozenset(liste.split()) for langue, liste in MOTS_OUTILS.items()}


# -- écritures ----------------------------------------------------------------

def ecriture_de(caractere: str) -> str:
    nom = unicodedata.name(caractere, "")
    return nom.split(" ", 1)[0] if nom else "INCONNUE"


def compter_ecritures(texte: str) -> Counter[str]:
    compte: Counter[str] = Counter()
    vus = 0
    for caractere in texte:
        if caractere.isalpha():
            compte[ecriture_de(caractere)] += 1
            vus += 1
            if vus >= LETTRES_ANALYSEES_MAX:
                break
    return compte


def langue_par_ecriture(ecritures: Counter[str], texte: str) -> tuple[str, float] | None:
    """Langue imposée par une écriture non latine dominante, ou None."""
    total = sum(ecritures.values())
    if not total:
        return None
    if ecritures["HIRAGANA"] + ecritures["KATAKANA"] > 0.05 * total:
        return "ja", (ecritures["HIRAGANA"] + ecritures["KATAKANA"] + ecritures["CJK"]) / total
    ecriture, nombre = ecritures.most_common(1)[0]
    if ecriture == ECRITURE_LATINE or ecriture not in ECRITURES_LANGUES:
        return None
    langue = ECRITURES_LANGUES[ecriture]
    for candidate, lettres in LETTRES_DEPARTAGE.get(ecriture, ()):
        if any(c in texte for c in lettres):
            langue = candidate
            break
    return langue, nombre / total


# -- scores latins (stdlib) ---------------------------------------------------

def scores_latins(texte: str) -> dict[str, float]:
    """Log-vraisemblance moyenne par trigramme (Bayes naïf) + part de mots-outils."""
    mots = RE_MOT.findall(texte.lower().replace("\u2019", "'"))
    tri = trigrammes(texte)
    nombre = max(sum(tri.values()), 1)
    scores = {}
    for langue, (logp, plancher) in PROFILS.items():
        vraisemblance = sum(n * logp.get(t, plancher) for t, n in tri.items()) / nombre
        outils = sum(m in MOTS[langue] for m in mots) / max(len(mots), 1) if langue in MOTS else 0.0
        scores[langue] = vraisemblance + POIDS_MOTS * outils
    return scores


def softmax(scores: dict[str, float]) -> dict[str, float]:
    haut = max(scores.values())
    expo = {k: math.exp(TEMPERATURE * (v - haut)) for k, v in scores.items()}
    total = sum(expo.values())
    return {k: v / total for k, v in sorted(expo.items(), key=lambda kv: -kv[1])}


def detecter_stdlib(texte: str) -> tuple[str | None, float, dict[str, float]]:
    probas = softmax(scores_latins(texte))
    langue, confiance = next(iter(probas.items()))
    return langue, confiance, dict(list(probas.items())[:3])


# -- moteurs tiers --------------------------------------------------------------

def detecteur_lingua() -> Any:
    return _LINGUA.LanguageDetectorBuilder.from_all_languages().build()


def detecter_lingua(detecteur: Any, texte: str) -> tuple[str | None, float, dict[str, float]]:
    valeurs = detecteur.compute_language_confidence_values(texte)
    top = {v.language.iso_code_639_1.name.lower(): round(v.value, 4) for v in valeurs[:3]}
    if not top:
        return None, 0.0, {}
    langue, confiance = next(iter(top.items()))
    return langue, confiance, top


def detecter_langdetect(texte: str) -> tuple[str | None, float, dict[str, float]]:
    _LANGDETECT.DetectorFactory.seed = 0
    try:
        resultats = _LANGDETECT.detect_langs(texte)
    except _LANGDETECT.LangDetectException:
        return None, 0.0, {}
    top = {r.lang.split("-")[0]: round(r.prob, 4) for r in resultats[:3]}
    langue, confiance = next(iter(top.items()))
    return langue, confiance, top


# -- blocs et décision ----------------------------------------------------------

def decouper_blocs(texte: str, seuil: int) -> list[dict[str, Any]]:
    """Paragraphes regroupés jusqu'à `seuil` lettres ; un reste court rejoint le précédent."""
    blocs: list[dict[str, Any]] = []
    courant: dict[str, Any] | None = None
    position = 0
    for morceau in RE_PARAGRAPHE.split(texte):
        debut = texte.find(morceau, position)
        position = debut + len(morceau)
        ligne = texte.count("\n", 0, debut) + 1
        if courant is None:
            courant = {"texte": morceau, "ligne_debut": ligne, "lettres": 0, "unites": 0}
        else:
            courant["texte"] += "\n\n" + morceau
        courant["lettres"] += sum(1 for c in morceau if c.isalpha())
        courant["unites"] += unites(morceau)
        courant["ligne_fin"] = ligne + morceau.count("\n")
        if courant["unites"] >= seuil:
            blocs.append(courant)
            courant = None
    if courant is not None and courant["unites"]:
        if blocs and courant["unites"] < seuil:
            for cle in ("lettres", "unites"):
                blocs[-1][cle] += courant[cle]
            blocs[-1]["texte"] += "\n\n" + courant["texte"]
            blocs[-1]["ligne_fin"] = courant["ligne_fin"]
        else:
            blocs.append(courant)
    return [b for b in blocs if b["unites"] >= seuil]


def unites(texte: str) -> int:
    """Lettres pondérées : un idéogramme, un kana ou une syllabe hangul en vaut trois."""
    total = 0
    for caractere in texte:
        if caractere.isalpha():
            dense = caractere >= "\u3040" and ecriture_de(caractere) in ECRITURES_DENSES
            total += POIDS_DENSE if dense else 1
    return total


def detecter_bloc(texte: str, moteur: str, detecteur: Any) -> dict[str, Any]:
    ecritures = compter_ecritures(texte)
    imposee = langue_par_ecriture(ecritures, texte)
    if imposee is not None and moteur == MOTEUR_STDLIB:
        langue, part = imposee
        return {"langue": langue, "confiance": round(part, 4), "candidates": {langue: round(part, 4)},
                "methode": "ecriture"}
    if moteur == MOTEUR_LINGUA:
        langue, confiance, top = detecter_lingua(detecteur, texte)
    elif moteur == MOTEUR_LANGDETECT:
        langue, confiance, top = detecter_langdetect(texte)
    else:
        langue, confiance, top = detecter_stdlib(texte)
    return {"langue": langue, "confiance": round(confiance, 4),
            "candidates": {k: round(v, 4) for k, v in top.items()}, "methode": moteur}


def conclure(blocs: list[dict[str, Any]], confiance_min: float) -> dict[str, Any]:
    """Langue principale (pondérée par les unités), statut et mélange.

    Mélange : une autre langue couvre au moins PART_MELANGE du texte avec au moins un
    bloc bien plus sûr que le minimum (un bloc mal lu dans un long texte ne suffit pas)."""
    tres_sur = (1.0 + confiance_min) / 2
    surs = [b for b in blocs if b["langue"] and b["confiance"] >= confiance_min]
    total = sum(b["unites"] for b in blocs) or 1
    poids: Counter[str] = Counter()
    for b in surs:
        poids[b["langue"]] += b["unites"]
    if not poids:
        return {"langue": None, "confiance": 0.0, "part": 0.0, "statut": "incertaine",
                "melange": False, "parts": {}}
    langue, unites_langue = poids.most_common(1)[0]
    autres = [k for k, v in poids.items() if k != langue and v / total >= PART_MELANGE
              and any(b["langue"] == k and b["confiance"] >= tres_sur for b in surs)]
    confiance = sum(b["confiance"] * b["unites"] for b in surs if b["langue"] == langue) / unites_langue
    statut = "melangee" if autres else ("determinee" if unites_langue / total >= 0.5 else "incertaine")
    return {"langue": langue, "confiance": round(confiance, 4), "part": round(unites_langue / total, 4),
            "statut": statut, "melange": bool(autres),
            "parts": {k: round(v / total, 4) for k, v in poids.most_common()}}


def analyser(texte: str, args: argparse.Namespace, detecteur: Any) -> dict[str, Any]:
    ecritures = compter_ecritures(texte)
    total = sum(ecritures.values())
    blocs = decouper_blocs(texte, args.seuil)
    for numero, bloc in enumerate(blocs, 1):
        bloc["nom"] = f"bloc {numero} (l.{bloc['ligne_debut']}-{bloc['ligne_fin']})"
        bloc.update(detecter_bloc(bloc.pop("texte"), args.moteur_effectif, detecteur))
    dominante = ecritures.most_common(1)[0] if ecritures else ("AUCUNE", 0)
    return {"lettres": total, "unites": unites(texte), "ecriture_dominante": dominante[0],
            "part_ecriture_dominante": round(dominante[1] / total, 4) if total else 0.0,
            "ecritures": dict(ecritures.most_common(6)), "blocs": blocs,
            **conclure(blocs, args.confiance_min)}


# -- entrées -----------------------------------------------------------------

def decoder(octets: bytes, nom: str) -> str:
    if b"\x00" in octets[:65536]:
        raise ErreurEntree(f"{nom} : fichier binaire (octet NUL), pas du texte")
    try:
        return octets.decode("utf-8-sig")
    except UnicodeDecodeError:
        print(f"{nom} : pas de l'{ENCODAGE_ATTENDU} valide, relu en cp1252", file=sys.stderr)
        return octets.decode("cp1252", "replace")


def lire_borne(flux: Any, octets_max: int, nom: str) -> bytes:
    octets = flux.read(octets_max + 1)
    if len(octets) > octets_max:
        raise ErreurEntree(f"{nom} dépasse {octets_max} octets (--max-octets)")
    return octets


def collecter_entree(args: argparse.Namespace, base: Path) -> tuple[str, str]:
    if args.texte is not None:
        return "<texte>", decoder(os.fsencode(args.texte), "<texte>")
    if args.chemin is None:
        raise ErreurEntree("rien à lire : donner un CHEMIN, '-' (entrée standard) ou --texte")
    if args.chemin == "-":
        return "<stdin>", decoder(lire_borne(sys.stdin.buffer, args.max_octets, "<stdin>"), "<stdin>")
    chemin = Path(args.chemin)
    chemin = chemin if chemin.is_absolute() else base / chemin
    if not chemin.exists():
        raise ErreurEntree(f"{chemin} : chemin inexistant")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} : c'est un dossier ; donner un fichier texte")
    with chemin.open("rb") as flux:
        return str(chemin), decoder(lire_borne(flux, args.max_octets, str(chemin)), str(chemin))


def choisir_moteur(demande: str) -> str:
    disponibles = {MOTEUR_LINGUA: _LINGUA, MOTEUR_LANGDETECT: _LANGDETECT}
    if demande in disponibles and disponibles[demande] is None:
        raise ErreurEntree(f"--moteur {demande} demandé mais la bibliothèque est absente")
    if demande != "auto":
        return demande
    for nom, module in disponibles.items():
        if module is not None:
            return nom
    print("lingua et langdetect absents : mode dégradé, profils stdlib (écritures Unicode, "
          "trigrammes de 19 langues latines, mots-outils de fr, en, es, de, it, pt, nl)", file=sys.stderr)
    return MOTEUR_STDLIB


# -- sorties -----------------------------------------------------------------

def extraire_contrat(doc: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith((" ", "\t")):
            courant = tete
            sections[courant] = []
        elif courant is not None and tete:
            sections[courant].append(tete)
    return {cle.lower().replace("è", "e").replace("-", "_"): " ".join(v) for cle, v in sections.items()}


def construire_rapport(nom: str, analyse: dict[str, Any], args: argparse.Namespace, base: Path) -> dict[str, Any]:
    noms = [b["nom"] for b in analyse["blocs"]]
    rapport = {
        "denominateur": len(analyse["blocs"]),
        "examines": noms[:EXAMINES_MAX],
        "source": nom,
        "moteur": args.moteur_effectif,
        "seuil_lettres": args.seuil,
        "confiance_min": args.confiance_min,
        "racine": str(base),
        **analyse,
        "attendue": args.attendue,
        "contrat": extraire_contrat(__doc__ or ""),
    }
    return rapport


def afficher_humain(rapport: dict[str, Any]) -> None:
    sys.stdout.write(f"{rapport['source']} : {rapport['lettres']} lettres, écriture "
                     f"{rapport['ecriture_dominante']} ({rapport['part_ecriture_dominante']:.0%}), "
                     f"moteur {rapport['moteur']}\n")
    for b in rapport["blocs"]:
        sys.stdout.write(f"  {b['nom']:<22} {b['langue'] or '?':<4} confiance {b['confiance']:.2f} "
                         f"({b['unites']} unités) {b['candidates']}\n")
    if rapport["denominateur"]:
        sys.stdout.write(f"langue : {rapport['langue'] or 'indéterminée'} — statut {rapport['statut']}, "
                         f"confiance {rapport['confiance']:.2f}, part {rapport['part']:.0%}, "
                         f"parts {rapport['parts']}\n")


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Détecte la langue d'un texte (écriture Unicode, puis mots-outils et trigrammes "
                    "pour fr, en, es, de, it, pt, nl), sa confiance, et le mélange entre paragraphes. "
                    f"Refuse de conclure sous {SEUIL_LETTRES} lettres (un idéogramme en vaut {POIDS_DENSE}).",
        epilog="Exemple : python detecter_langue.py reponse.txt --attendue fr --json\n"
               "Codes : 0 langue déterminée (et conforme à --attendue) ; 1 mélangée, incertaine ou "
               "différente de --attendue ; 2 usage ; 3 texte trop court (refus).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemin", nargs="?", help="fichier texte à examiner, ou '-' pour l'entrée standard")
    parseur.add_argument("--texte", help="texte donné en ligne (au lieu d'un chemin)")
    parseur.add_argument("--attendue", help="code ISO 639-1 attendu (fr, en...) : code 1 s'il diffère")
    parseur.add_argument("--seuil", type=int, default=SEUIL_LETTRES,
                         help=f"unités minimales par bloc (lettres ; idéogramme, kana, hangul = {POIDS_DENSE})")
    parseur.add_argument("--confiance-min", type=float,
                         help="confiance minimale pour qu'un bloc compte (défaut selon le moteur : "
                              + ", ".join(f"{k} {v}" for k, v in CONFIANCE_MIN.items()) + ")")
    parseur.add_argument("--moteur", choices=("auto", MOTEUR_STDLIB, MOTEUR_LINGUA, MOTEUR_LANGDETECT),
                         default="auto", help="auto : lingua, sinon langdetect, sinon stdlib")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue")
    parseur.add_argument("--racine", type=Path,
                         help=f"base des chemins relatifs (défaut : dossier courant ; outil : {RACINE.name})")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def code_de_sortie(rapport: dict[str, Any]) -> int:
    if rapport["statut"] != "determinee":
        return CODE_DEFAUT
    if rapport["attendue"] and rapport["langue"] != rapport["attendue"].lower():
        return CODE_DEFAUT
    return CODE_RIEN


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    base = (args.racine if args.racine is not None else Path.cwd()).resolve()
    if args.seuil < 1:
        print("erreur : --seuil doit valoir au moins 1", file=sys.stderr)
        return CODE_USAGE
    try:
        args.moteur_effectif = choisir_moteur(args.moteur)
        if args.confiance_min is None:
            args.confiance_min = CONFIANCE_MIN[args.moteur_effectif]
        nom, texte = collecter_entree(args, base)
    except ErreurEntree as erreur:
        print(f"erreur : {erreur}", file=sys.stderr)
        return erreur.code
    except OSError as erreur:
        print(f"erreur de lecture : {erreur}", file=sys.stderr)
        return CODE_USAGE
    detecteur = detecteur_lingua() if args.moteur_effectif == MOTEUR_LINGUA else None
    rapport = construire_rapport(nom, analyser(texte, args, detecteur), args, base)
    if args.json:
        sys.stdout.write(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n")
    else:
        afficher_humain(rapport)
    if not rapport["denominateur"]:
        print(f"dénominateur nul : rien à examiner — {rapport['unites']} unités de texte (lettres ; "
              f"idéogramme, kana, hangul = {POIDS_DENSE}), sous le seuil de {args.seuil} : "
              "refus de conclure sur la langue", file=sys.stderr)
        return CODE_VIDE
    return code_de_sortie(rapport)


if __name__ == "__main__":
    raise SystemExit(main())
