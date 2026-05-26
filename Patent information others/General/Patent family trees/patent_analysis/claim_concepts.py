# patent_analysis/claim_concepts.py
"""
XFR-style concept extraction from patent Claim 1 text.

Faithful Python port of the two-stage pipeline from xfr.pre (Alain Materne, 2012-2018):

  Stage 1 — conceptChunker()
    Splits the claim text at "concept separator" patterns: comprising, wherein,
    said, characterized in that, which, in a, and, …  Each match inserts either
    a %%preamble%% (preamble/characterizing boundary) or a %%sep%% (general
    separator) token so the text becomes a sequence of candidate concept chunks.

  Stage 2 — concepts_strategy()
    For each chunk:
      1. Strip reference numbers (766), figure labels, punctuation.
      2. Remove FullStopWords (whole chunk discarded if it IS a stop word).
      3. Remove TiStopwords (prefix-based filter for generic patent vocabulary).
      4. Apply light suffix stemming.
      5. Build a P-operator query: WORD1 P WORD2 P WORD3  (proximity operator,
         same as the Concept button in XFR — "P" means "within the same sentence
         in either order" in EPODOC/WPI syntax).
      6. De-duplicate across concepts so no token appears twice in the strategy.

Output is a list of concept strings, each being a P-joined phrase ready for
use in a patent database search query (EPODOC /KW field, WPI /BI field, etc.).

Usage
-----
from patent_analysis.claim_concepts import extract_concepts

concepts = extract_concepts(
    "1. A data stream reproduction apparatus for contiguously reproducing "
    "two or more data streams (VOB) after reading the data streams (VOB) "
    "from an optical disk and storing them in a buffer (2600, 2700, 2800), "
    "the data streams (VOB) containing at least video data and audio data "
    "and including a plurality of data units ...",
    language='EN'
)
# → ['data stream P reproduction P apparatus',
#    'optical disk',
#    'buffer P video P data P audio P data',
#    ...]
"""

from __future__ import annotations
import re
from typing import List

# ── Stop-word lists (direct ports of .xfr.st~En*Stopwords) ──────────────────

_EN_FULL_STOPWORDS: frozenset = frozenset("""
ABLE ABOVE ABOUT ACCORDANCE ACROSS ACTUAL ADDITIONAL ADDITIONALLY AFORE AFTER
AFTERWARDS AGAIN AGAINST AIM AIMS AIMED AL ALL ALMOST ALONG ALREADY ALSO
ALTHOUGH ALWAYS AMONG AN AND ANDWHEREIN ANOTHER ANY ANYONE APART APPLY APRIL
ARE AROUND ART ARTICLE AS AT AUGUST AWAY BASED BE BECAME BECAUSE BEEN BEFORE
BEFOREHAND BEHIND BEING BELOW BENEATH BESIDE BESIDES BEST BETTER BETWEEN BEYOND
BORNE BOTH BREAKTHROUGH BRIEFLY BROUGHT BUILD BUILT BUT BY CAN CANNOT CARRY
CERTAIN COMMON COMPLETENESS CONST CONVEY CORRESP COULD DECEMBER DEEPLY DESPITE
DETAIL DID DISTAL DIV DIVERS DO DOES DOING DONE DUE DURING EACH EG EITHER ENDS
ENOUGH ENTIRE ET ETC EVEN EVER EVERY EVERYTHING FAME FAMOUS FAR FAST FEBRUARY
FEW FIG FIGS FIRST FIFTH FIVE FOR FORTH FORTHCOMING FOUR FOURTH FROM FRIDAY FULL
FULLY FURTHER FURTHERMORE GAVE GET GETS GAVE GIVE GIVEN GIVING GO GOES GOING
GONE GOOD GOT HAVE HAVING HAS HAD HENCE HE HER HERE HEREBY HEREIN HEREINABOVE
HEREINAFTER HEREINBEFORE HEREUNDER HEREUPON HEREWITH HERSELF HIMSELF HIS HITHER
HOW HOWEVER IE IF II III IV IMPL IN INASMUCH INDICATING INDICATIVE INDUCE
INDUCED INDUCES INSOFAR INSOMUCH INSTANCE INSTEAD INTENSIVE INTER INTERMEDIARY
INTO IS IT ITS ITSELF JANUARY JULY JUNE JUST KK KEPT KNEW KNOWN LAID LATER LEAST
LESS LET LETS LIKEWISE LIMPLY LOT MADE MAKING MANNER MANY MARCH MAY MAYBE
MEANWHILE MED MET MIGHT MINE MONDAY MORE MOREOVER MOST MUCH MULTI MY ND NEAR
NEARBY NEAREST NEVER NEVERTHELESS NEW NEWLY NEXT NINE NINTH NO NON NONE
NONETHELESS NOONE NOVEMBER NOR NOT NOTEWORTHY NOW NOWADAYS OBTD OCTOBER OF OFF
OFTEN ON ONCE ONE ONES ONLY ONTO OR OTHERWISE OUGHT OUR OURS OUF OUT OUTER
OUTWARD OVER OWING OWN PARTICULAR PCT PER PLENTY PRE PREF PROXIMAL QUASI RATHER
RD RE REASONABLE REFERS REFERING REGARDLESS RELATIONSHIP RUNS SAID SAME SAT
SATURDAY SECOND SEE SEEN SEES SELDOM SEPTEMBER SET SETS SEVEN SEVENTH SHALL
SHARPLY SHE SHOULD SHOWED SHOWING SHOWN SHOWS SINCE SIT SIX SIXTH SLOWLY SO
SOME SOMEONE SOMETIMES SOMETHING SOMEWHAT SOON STILL SUCH SUNDAY TAKE TAKEN TB
TEN TENTH TH THAN THAT THE THEIR THEM THEN THENCE THERE THEREACROSS THEREAGAINST
THEREAFTER THEREBETWEEN THEREBY THEREFOR THEREFORE THURSDAY TOMORROW TOO TOOK
TRULY TUESDAY THEREFROM THEREIN THEREINTO THEREOF THEREON THERETO THERETHROUGH
THEREUPON THEREWITH THESE THEY THIRD THIS THITHER THOROUGHNESS THOSE THOUGH
THREE THROUGH THROUGHOUT THRU THUS TO TOGETHER TWICE TWO UNABLE UNDER UNDERNEATH
UNDERSTOOD UNIT UNLESS UNTIL UP UPBEAT UPON UPRIGHT UPSIDE-DOWN UPWARD USE USED
USEFUL USES USING VIA VERY WAS WE WELL WENESDAY WENT WERE WHAT WHATEVER
WHATSOEVER WHEN WHENCE WHENEVER WHERE WHEREAS WHEREAT WHEREBY WHEREIN WHEREON
WHERETHROUGH WHEREUPON WHEREVER WHETHER WHICH WHICHEVER WHILE WHILST WHITHER WHO
WHOLE WHOM WHOSE WILL WITH WITHIN WITHOUT WORST WOULD Y YESTERDAY YET YOU YOUR
ACT ACTS ADDRESSED CARRIES CARRIED CAME COME COMPRISE
""".split())

# TiStopwords: prefix-based (match word start). Ported from .xfr.st~EnTiStopwords.
_EN_TI_STOPWORD_PREFIXES: tuple = (
    'ABSTRAC', 'ACCE', 'ACCEP', 'ACCOMMOD', 'ACCOMP', 'ACCOMPAN', 'ACCORD',
    'ACHIEV', 'ADAP', 'ADMINIS', 'ADMIT', 'ADOP', 'ADVANC', 'ADVANT', 'AFFEC',
    'AFFLIC', 'AFOREMEN', 'AGENT', 'ALLEV', 'ALLO', 'ALLOW', 'AMEND', 'AMOUN',
    'ANCIL', 'ANTI', 'ANYTH', 'APPAR', 'APPEAR', 'APPL', 'APPLY', 'APPLI',
    'APPARATU', 'APPERTAIN', 'APPLIANC', 'APPRECI', 'APPROPR', 'APPROXIM',
    'ARRANG', 'ARRIV', 'ASCERTAIN', 'ASSEMB', 'ASSI', 'ASSIGN', 'ASSOC',
    'ASSUM', 'ATTACH', 'AVAIL', 'AVAILAB', 'AVOID', 'BECOM', 'BELONG', 'BENEF',
    'CAPAB', 'CAUS', 'CENT', 'CHARACT', 'CIRCU', 'CLAIM', 'CLOS', 'COM',
    'COMBIN', 'COMP', 'COMPA', 'COMPL', 'COMPLE', 'COMPLEMEN', 'COMPO', 'COMPOS',
    'COMPOSI', 'COMPOUND', 'COMPON', 'COMPREH', 'COMPRI', 'CONCENTR', 'CONCERN',
    'CONCLU', 'CONSTRAIN', 'CONSTRUC', 'CONFIG', 'CONFIGU', 'CONFORM', 'CONJUNC',
    'CONSECU', 'CONSID', 'CONSIDER', 'CONSIS', 'CONSOLID', 'CONSTI', 'CONSTITU',
    'CONTAIN', 'CONVEN', 'CONVENI', 'COOP', 'CORRESPOND', 'CREA', 'CRIT', 'CRITER',
    'DECID', 'DEDIC', 'DEDUC', 'DEFIN', 'DEMONS', 'DEMONSTR', 'DENOT', 'DEPAR',
    'DEPEND', 'DEPIC', 'DEPLO', 'DEPLOY', 'DERIV', 'DESCR', 'DESCRIB', 'DESCRIP',
    'DESI', 'DESIGN', 'DESTIN', 'DETERMIN', 'DEVIC', 'DIFF', 'DISADVANT',
    'DISCLO', 'DISCU', 'DISCUS', 'DISPO', 'DISREGARD', 'DISTINC', 'DISTINGU',
    'DRIV', 'DRUG', 'EASY', 'EFFEC', 'EFFIC', 'ELEMEN', 'EMBOD', 'EMPLO',
    'EMPLOY', 'ENABL', 'ENHANC', 'ENSUR', 'ENTAIL', 'ENTIT', 'EQUAL', 'ESPEC',
    'ESSEN', 'ESTABL', 'EVEN', 'EVID', 'EXAMP', 'EXAMPL', 'EXEMPL', 'EXCEL',
    'EXCEP', 'EXECU', 'EXIST', 'EXPLAIN', 'EXPLIC', 'EXPR', 'FABR', 'FACIL',
    'FEAT', 'FED', 'FEED', 'FIGUR', 'FOLLOW', 'FOREGO', 'FORM', 'FORMUL',
    'FUNC', 'FUNCT', 'GENER', 'GETT', 'GIVE', 'GREAT', 'HANDL', 'IDENTIC',
    'ILLUSTR', 'IMMED', 'IMPLEM', 'IMPOR', 'IMPROVEM', 'IMPROV', 'INAPPROPR',
    'INCLUD', 'INCORPOR', 'INDEP', 'INDEPEND', 'INDUS', 'INDUSTR', 'INEVI',
    'INFER', 'INFLU', 'INNER', 'INPING', 'INQUIR', 'INSID', 'INSIGNIF', 'INSTAL',
    'INTEND', 'INTER', 'INTERES', 'INTRODUC', 'INVEN', 'INVOLV', 'KNOW',
    'LAUNCH', 'LEAV', 'LIKE', 'LOCA', 'LOCAT', 'MACHIN', 'MAKE', 'MANIFOLD',
    'MANN', 'MATER', 'MEAN', 'MEMB', 'MAINTAIN', 'MANUFAC', 'MECH', 'MEDIC',
    'MEDICA', 'MEET', 'MENT', 'METHOD', 'METHODOLOG', 'MIGHT', 'MISLEAD',
    'MIXT', 'MODIF', 'MODU', 'MODUL', 'MOUNT', 'MULTIP', 'MUST', 'MUTU',
    'NECE', 'NEED', 'NEGLIG', 'NEITH', 'NORMAL', 'NOVEL', 'OBTAIN', 'OBVI',
    'OCCUR', 'OFFER', 'OPER', 'OPERA', 'OPERAT', 'OPTIM', 'OPTION', 'ORDER',
    'OTHER', 'OWE', 'PACKAG', 'PART', 'PATENT', 'PERCENT', 'PERFORM', 'PERMI',
    'PERTAIN', 'PLURAL', 'POSS', 'POSSIB', 'PREAMB', 'PREC', 'PRECHARACTER',
    'PREDEFIN', 'PREDETERMIN', 'PREDISPO', 'PREFER', 'PRELIMIN', 'PREPAR',
    'PRES', 'PRESCRIB', 'PRESCRIP', 'PRESEN', 'PREV', 'PREVA', 'PREVAL',
    'PREVEN', 'PRIM', 'PRIMAR', 'PRINCIP', 'PRINCIPAL', 'PRIOR', 'PROBLEM',
    'PROC', 'PROCED', 'PROCES', 'PRODUC', 'PROMO', 'PROPER', 'PROPRE', 'PROSEC',
    'PROVI', 'PROVID', 'PROVIS', 'PROVISO', 'PURPOS', 'PURSU', 'REALI', 'REFIN',
    'REGARD', 'RELA', 'RELAT', 'RELEV', 'REMAIN', 'REMARK', 'REMOV', 'REPLEN',
    'REPRESEN', 'REQUIR', 'RESPEC', 'RESUL', 'RETURN', 'REUS', 'RISE', 'RISK',
    'SATISF', 'SCOP', 'SECT', 'SEEM', 'SELEC', 'SETTL', 'SEVER', 'SIGNIF',
    'SIMIL', 'SINGLE', 'SITU', 'SITUAT', 'SKIL', 'SMAL', 'SPEC', 'SPECIF',
    'SPIRIT', 'STEP', 'STRUC', 'STAT', 'SUB', 'SUBJEC', 'SUBSEQU', 'SUBST',
    'SUBSTR', 'SUBSTITU', 'SUCC', 'SUFF', 'SUFFICI', 'SUGGES', 'SUIT', 'SUP',
    'SUPP', 'SUPPL', 'SUPPOR', 'SUSCEP', 'SUSTAIN', 'SYSTEM', 'TECHN',
    'TECHNIQU', 'TECHNOL', 'THING', 'TION', 'TITL', 'TOTAL', 'TOWARD', 'TRAN',
    'TREA', 'TREAT', 'TRIE', 'TYPE', 'UNAVOID', 'UNDERGO', 'UNIFORM', 'UNIT',
    'UNREL', 'UNST', 'URGE', 'USAB', 'USABL', 'USAG', 'UTIL', 'VICIN', 'VIRTU',
    'WANT', 'YIELD',
)

# ── Concept separators (EN) — port of conceptChunker regex list ──────────────
# Each entry: (pattern, is_preamble_separator)
# is_preamble=True → insert %%preamble%% (marks preamble/characterizing boundary)
# is_preamble=False → insert %%sep%% (general chunk boundary)

_EN_SEPARATORS: list = [
    # High-priority preamble markers (comprising, wherein, characterized in that…)
    (re.compile(r'\s+compris\w*\s*:?', re.I), True),
    (re.compile(r'\s+consist\w*\s*:?', re.I), True),
    (re.compile(r'\s+wherein\s*:?', re.I), True),
    (re.compile(r'\s+in\s+which\s*:?', re.I), True),
    (re.compile(r'\s+characteris\w*\s*:?', re.I), True),
    (re.compile(r'\s+characteri[sz]\w*\s*:?', re.I), True),
    (re.compile(r'\s+includ\w*\s*:?', re.I), True),
    (re.compile(r'\s+us\w+\s*(?::|for)\s+', re.I), True),
    (re.compile(r'\s+constitut\w*\s*:?', re.I), True),
    (re.compile(r'\s+disclos\w*\s*:?', re.I), True),
    (re.compile(r'\s+provid\w*\s*:?', re.I), True),
    (re.compile(r'[-\s]+contain\w*\s*:?', re.I), True),
    (re.compile(r'[-\s]+utili[sz]+\w*\s*:?', re.I), True),
    (re.compile(r'\s+having\s*:?', re.I), True),
    (re.compile(r'[,\s]+capable\s+of\s*:?', re.I), True),
    (re.compile(r'\s+thereby\s+(?:way\s+)?:?\s+', re.I), True),
    (re.compile(r'\s+whereby\s+(?:way\s+)?:?\s+', re.I), True),

    # General separators (claim feature boundaries)
    (re.compile(r'[,\s]+said\s+', re.I), False),
    (re.compile(r',+\s*this\s+', re.I), False),
    (re.compile(r',+\s*these\s+', re.I), False),
    (re.compile(r'[,\s]+each\s+', re.I), False),
    (re.compile(r'\s+which\s*:?', re.I), False),
    (re.compile(r'\s+wherein\s+', re.I), False),  # second occurrence
    (re.compile(r'\s+where\s*:?', re.I), False),
    (re.compile(r'\s+when\s+', re.I), False),
    (re.compile(r'\s+while\s*:?', re.I), False),
    (re.compile(r'\s+whose\s*:?', re.I), False),
    (re.compile(r'\s+whether\s*:?', re.I), False),
    (re.compile(r'\s+thereof\s*:?', re.I), False),
    (re.compile(r'\s+whereof\s*:?', re.I), False),
    (re.compile(r'\s+in\s+order\s+', re.I), False),
    (re.compile(r'\s+in\s+accordance\s+', re.I), False),
    (re.compile(r'\s+in\s+particular\s+', re.I), False),
    (re.compile(r'\s+at\s+least\s+', re.I), False),
    (re.compile(r'\s+in\s+response\s+', re.I), False),
    (re.compile(r',+\s*and\s+', re.I), False),
    (re.compile(r',+\s*a\s+', re.I), False),
    (re.compile(r',+\s*an\s+', re.I), False),
    (re.compile(r',+\s*as\s+', re.I), False),
    (re.compile(r'\s+from\s+', re.I), False),
    (re.compile(r'[,\s]+of\s+', re.I), False),
    (re.compile(r'[,\s]+with\s+', re.I), False),
    (re.compile(r',+\s*the\s+', re.I), False),
    (re.compile(r'\s+hav\w*\s*:?', re.I), False),
    (re.compile(r'\s+has\s+:?', re.I), False),
    (re.compile(r'[,\s]+to\s+', re.I), False),
    (re.compile(r'\s+into\s+', re.I), False),
    (re.compile(r'[,\s]+in\s+a[n]?\s+', re.I), False),
    (re.compile(r'[,\s]+in\s+', re.I), False),
    (re.compile(r'\s+by\s+(?:way\s+)?:?\s+', re.I), False),
    (re.compile(r'\s+operable\s+to\s+', re.I), False),
    (re.compile(r'\s+adapted\s+(?:from|to|of)\s*:?', re.I), False),
    (re.compile(r'\s+\w+(?:ed|ing)\s+(?:to|with|in|on|as|from|for|of)\s*:?\s+', re.I), False),
    (re.compile(r'\s+(?:can|could|may|might|shall|will)\s*(?:not\s+)?(?:be\s+)?\w+(?:ed|ing)\s+', re.I), False),
    (re.compile(r'\s+(?:is|are)\s+\w+(?:ed|ing)\s+', re.I), False),
    (re.compile(r'\s+for\s+\w+ing[,\s]+', re.I), False),
    (re.compile(r'[,\s]+optionally\s+', re.I), False),
]

# ── Reference-number stripping patterns ─────────────────────────────────────
_REF_PATTERNS = [
    re.compile(r'[A-Za-z]{0,2}<[^>]+>'), # T<1>, T<req>, a<req>, T<EM> — angle-bracket vars
    re.compile(r'\(\d+\)'),             # (766)
    re.compile(r'\([A-Za-z0-9 ,]+\)'), # (VOB), (, ) residue after angle-bracket strip
    re.compile(r'\[[A-Za-z0-9 ,]+\]'),  # [array notation]
    re.compile(r'\{[A-Za-z0-9 ,]+\}'),  # {brace notation}
]

# ── Light suffix stemmer (mimics xfr stemmingProcess for EN) ─────────────────
_STEM_SUFFIXES = [
    ('ations', ''), ('ation', ''), ('ations', ''), ('nesses', ''),
    ('nesses', ''), ('ments', ''), ('ment', ''), ('ings', ''),
    ('ing', ''), ('tion', ''), ('sion', ''), ('ors', ''), ('ers', ''),
    ('ies', 'y'), ('ves', 'f'), ('ied', 'y'), ('ied', ''),
    ('eed', ''), ('eed', ''),
    ('ed', ''), ('es', ''), ('ss', 's'), ('s', ''),
]

# Extra single-word generics too vague to be useful as standalone concepts
_GENERIC_SINGLES: frozenset = frozenset({
    'LOWER', 'UPPER', 'INNER', 'OUTER', 'INPUT', 'OUTPUT', 'TRACK',
    'INFORM', 'INFORMAT', 'REFERENCE', 'MANNER', 'PLURALITY', 'FURTHER',
    'FIRST', 'SECOND', 'THIRD', 'FOURTH', 'LEAST',
    'LESS', 'MORE', 'NEXT', 'LAST', 'PRIOR', 'FOLLOW', 'GIVEN',
    'CERTAIN', 'VARIOUS', 'GENERAL', 'SPECIFIC', 'RESPECT', 'LEVEL',
    'NUMBER', 'TYPE', 'KIND', 'FORM', 'PART', 'UNIT', 'ITEM', 'AREA',
    'REGION', 'PORTION', 'SECTION', 'ELEMENT', 'MEMBER', 'COMPONENT',
    # Structural patent-claim words that are too generic as standalone concepts:
    'SIGNAL', 'VALUE', 'DATA', 'STATE', 'MODE', 'BASE',
    'MEANS', 'STEP', 'PROCESS', 'OBJECT', 'SYSTEM', 'CURRENT',
})


def _stem(word: str) -> str:
    """Very light suffix-stripping stemmer — adds '+' truncation marker."""
    if len(word) < 5:
        return word
    upper = word.upper()
    for suffix, replacement in _STEM_SUFFIXES:
        if upper.endswith(suffix.upper()) and len(word) - len(suffix) >= 4:
            root = word[: len(word) - len(suffix)]
            return root + replacement + '+'
    return word


def _is_ti_stopword(word: str) -> bool:
    """Returns True if word starts with any TiStopword prefix."""
    upper = word.upper()
    return any(upper.startswith(p) for p in _EN_TI_STOPWORD_PREFIXES)


def _is_full_stopword(word: str) -> bool:
    return word.upper() in _EN_FULL_STOPWORDS


def _strip_refs(text: str) -> str:
    """Remove reference numbers and parenthesised abbreviations."""
    for pat in _REF_PATTERNS:
        text = pat.sub(' ', text)
    return text


# ── Stage 1 — conceptChunker ─────────────────────────────────────────────────

def concept_chunker(claim_text: str, language: str = 'EN') -> str:
    """
    Split claim_text into concept chunks by inserting %%preamble%% and
    %%sep%% tokens at feature-boundary patterns.

    Returns the annotated text string (still one flat string with markers).
    """
    text = claim_text
    # Strip the leading claim number ("1. " or "1 ")
    text = re.sub(r'^\s*\d+\s*[.)]\s*', '', text).strip()
    # Normalise whitespace
    text = ' '.join(text.split())
    # Remove reference numbers / fig IDs
    text = _strip_refs(text)
    text = ' '.join(text.split())

    if language != 'EN':
        # Only EN is implemented; return as single chunk
        return text

    # Apply separator patterns in order, replacing each match with the marker
    preamble_done = False
    for pattern, is_preamble in _EN_SEPARATORS:
        marker = '%%preamble%%' if (is_preamble and not preamble_done) else '%%sep%%'
        def _replace(m, _marker=marker):
            return ' ' + _marker + ' '
        new_text, count = pattern.subn(_replace, text)
        if count:
            text = new_text
            if is_preamble:
                preamble_done = True

    # Normalise multiple consecutive markers
    text = re.sub(r'(\s*%%(?:preamble|sep)%%\s*){2,}', ' %%sep%% ', text)
    text = ' '.join(text.split())
    return text


# ── Stage 2 — concepts_strategy ──────────────────────────────────────────────

def concepts_strategy(chunked_text: str, max_words_per_concept: int = 6) -> List[str]:
    """
    Build a list of P-operator concept queries from chunked text.

    Each concept is the meaningful words of one chunk joined by ' P ':
        'optical disk P reproduction P apparatus'

    Filters applied per word:
      - Skip if numeric
      - Skip if in _EN_FULL_STOPWORDS
      - Skip if starts with a _EN_TI_STOPWORD prefix
      - Apply light stemming + truncation
      - De-duplicate: no token already present in prior concepts

    Returns list of concept strings (empty list if nothing found).
    """
    if not chunked_text.strip():
        return []

    # Split at both marker types
    raw_chunks = re.split(r'%%(?:preamble|sep)%%', chunked_text, flags=re.I)

    concepts: list = []
    seen_tokens: set = set()   # global dedup across all concepts

    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # Strip punctuation artefacts
        chunk = re.sub(r'[,;:.()\[\]{}"]', ' ', chunk)
        chunk = re.sub(r'\s{2,}', ' ', chunk).strip()

        # ── Internal stopword splitting ────────────────────────────────────
        # When a full stopword (e.g. UNIT, FOR, THE) appears between content
        # words it is silently dropped by the word loop, causing words on both
        # sides to merge into one concept ("control P heavy P duty P vehicle").
        # Instead, treat each full-stopword boundary as a sub-chunk separator
        # so "control | unit | heavy duty vehicle" yields two separate concepts.
        # TI-stopwords (generic patent verbs/nouns) are still just dropped.
        sub_chunks: list = [[]]
        for raw_word in chunk.split():
            word = raw_word.replace('-', '_').strip('_').strip(',').strip()
            if not word or word.isnumeric() or re.fullmatch(r'\d[\d,.]*', word):
                continue
            upper = word.upper()
            if _is_full_stopword(upper) or len(word) <= 2:
                # Boundary: flush current sub-chunk and start a new one
                if sub_chunks[-1]:
                    sub_chunks.append([])
            elif not _is_ti_stopword(upper):
                sub_chunks[-1].append(raw_word)
            # TI-stopwords: simply drop (no boundary, no content)

        for sub_words in sub_chunks:
            if not sub_words:
                continue

            concept_words: list = []

            for raw_word in sub_words:
                word = raw_word.replace('-', '_').strip('_').strip(',').strip()
                if not word or word.isnumeric():
                    continue
                if re.fullmatch(r'\d[\d,.]*', word):
                    continue

                # Stem and add truncation
                stemmed = _stem(word)
                token = stemmed.upper().rstrip('+').rstrip('?')

                # Global de-dup: skip if root already represented
                if any(token.startswith(seen) or seen.startswith(token)
                       for seen in seen_tokens if len(seen) >= 4):
                    continue

                concept_words.append(stemmed)
                seen_tokens.add(token)

                if len(concept_words) >= max_words_per_concept:
                    break

            if len(concept_words) >= 1:
                # Discard single-word concepts that are too generic to be useful
                if len(concept_words) == 1:
                    root = concept_words[0].upper().rstrip('+').rstrip('?')
                    if root in _GENERIC_SINGLES or len(root) <= 3:
                        continue
                concept = ' P '.join(concept_words)
                concepts.append(concept)

    return concepts


# ── Public API ───────────────────────────────────────────────────────────────

def extract_concepts(claim1_text: str,
                     language: str = 'EN',
                     max_words_per_concept: int = 6,
                     max_concepts: int = 12) -> List[str]:
    """
    Full two-stage XFR concept extraction pipeline.

    Parameters
    ----------
    claim1_text : str
        Plain text of Claim 1 (already normalised, single line).
    language : str
        'EN', 'DE', 'FR', 'NL' — only EN is fully implemented.
    max_words_per_concept : int
        Maximum words joined per P-operator concept (default 6).
    max_concepts : int
        Maximum concepts returned (XFR caps at 11; default here 12).

    Returns
    -------
    list of str
        Each string is a P-operator concept ready for a patent search query.
        Example: ['optical disk P reproduction', 'buffer P video P data', …]
    """
    if not claim1_text or not claim1_text.strip():
        return []

    chunked = concept_chunker(claim1_text, language=language)
    concepts = concepts_strategy(chunked, max_words_per_concept=max_words_per_concept)
    return concepts[:max_concepts]


def concepts_to_query(concepts: List[str],
                      field: str = '/KW',
                      must_count: int = 2) -> str:
    """
    Assemble a patent search query from a list of P-operator concepts.

    The first `must_count` concepts become MUST facets (AND-joined).
    The remainder become optional facets (OR-joined as a Facet block).

    Parameters
    ----------
    concepts : list of str
    field : str
        Target search field, e.g. '/KW' (EPODOC keyword), '/BI' (WPI basic index).
    must_count : int
        Number of leading concepts to treat as mandatory (AND).

    Returns
    -------
    str
        A ready-to-use EPODOC/WPI query string.

    Example
    -------
    >>> concepts_to_query(['optical P disk', 'video P data P stream', 'buffer'])
    '(optical P disk)/KW AND (video P data P stream)/KW AND (buffer)/KW'
    """
    if not concepts:
        return ''
    parts = [f'({c}){field}' for c in concepts]
    musts = parts[:must_count]
    facets = parts[must_count:]
    query = ' AND '.join(musts)
    if facets:
        query += ' AND (' + ' OR '.join(facets) + ')'
    return query


# ── Display formatting utilities ──────────────────────────────────────────────

#: Canonical human-readable proximity operator.
#: Internal storage and search-query generation always use ' P ';
#: this constant is the single source of truth for display conversion.
DISPLAY_PROXIMITY_OP: str = 'NEAR'

def format_for_display(concepts: List[str]) -> List[str]:
    """
    Convert a list of P-operator concept strings to their human-readable form
    by replacing the internal ' P ' proximity operator with ' NEAR '.

    This is a **display-only** transformation — the internal ' P ' format is
    preserved everywhere else (storage, ancestor comparison, search queries).

    Parameters
    ----------
    concepts : list of str
        Concepts as returned by extract_concepts(), e.g.
        ['optical P disk P stor+', 'video', 'buffer']

    Returns
    -------
    list of str
        Same concepts with ' P ' replaced by ' NEAR ', e.g.
        ['optical NEAR disk NEAR stor+', 'video', 'buffer']

    Examples
    --------
    >>> format_for_display(['optical P disk', 'video P data P stream'])
    ['optical NEAR disk', 'video NEAR data NEAR stream']
    >>> format_for_display([])
    []
    """
    import re as _re
    op = f' {DISPLAY_PROXIMITY_OP} '
    # Replace ' P ' with NEAR and EPODOC '+' truncation with universal '*'
    return [_re.sub(r'\+(?=\s|$)', '*', c.replace(' P ', op)) for c in concepts]


def format_concept_for_display(concept: str) -> str:
    """
    Single-string variant of format_for_display().
    Replaces the internal ' P ' proximity operator with ' NEAR '
    and converts the EPODOC '+' truncation marker to the universal '*'.

    Parameters
    ----------
    concept : str
        A single P-operator concept string, e.g. 'optical P disk P stor+'

    Returns
    -------
    str
        e.g. 'optical NEAR disk NEAR stor*'
    """
    import re as _re
    result = concept.replace(' P ', f' {DISPLAY_PROXIMITY_OP} ')
    # Replace trailing EPODOC '+' truncation with universal '*'
    result = _re.sub(r'\+(?=\s|$)', '*', result)
    return result


def split_concept_to_pairs(concept_display: str) -> List[str]:
    """
    Split a NEAR-chain with 3 or more tokens into overlapping adjacent pairs
    so that each resulting phrase is a valid two-word proximity query.

    Espacenet's ``claims=`` (and ``ab=``, ``nftxt=``) field only supports one
    ``prox/distance`` operator per parenthesised group — a three-token chain
    like ``claims=("a" prox/distance<=3 "b" prox/distance<=3 "c")`` is not
    accepted.  Splitting into ``claims=("a" prox/distance<=3 "b")`` AND
    ``claims=("b" prox/distance<=3 "c")`` is a conservative equivalent that
    Espacenet understands.

    Parameters
    ----------
    concept_display : str
        A display-form concept string (i.e. already converted from internal
        ``P``-operator form), e.g. ``'finger NEAR line* NEAR connect*'``.

    Returns
    -------
    list of str
        For 0–2 tokens: a single-element list containing the original string.
        For 3+ tokens: a list of overlapping adjacent pairs, e.g.
        ``['finger NEAR line*', 'line* NEAR connect*']``.

    Examples
    --------
    >>> split_concept_to_pairs('finger NEAR line* NEAR connect*')
    ['finger NEAR line*', 'line* NEAR connect*']
    >>> split_concept_to_pairs('solar NEAR cell')
    ['solar NEAR cell']
    >>> split_concept_to_pairs('semiconductor')
    ['semiconductor']
    """
    sep = f' {DISPLAY_PROXIMITY_OP} '
    tokens = [t.strip() for t in concept_display.split(sep)]
    if len(tokens) <= 2:
        return [concept_display]
    return [f"{tokens[i]}{sep}{tokens[i + 1]}" for i in range(len(tokens) - 1)]
