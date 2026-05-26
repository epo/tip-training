# patent_analysis/related_files_searcher.py
"""
RelatedFilesSearcher
====================
Performs OPS published_data_search queries to find patent applications related
to the current DiviTree input file, mirroring the search strategies demonstrated
in OPS Lesson 6, §7.5.3 onwards.

The five search strategies (§7.5.3.2 – §7.5.3.6) are run in sequence.
Results are deduplicated and returned as a DataFrame of publication numbers
(any country) that are NOT already in the current family, plus the input file
itself when at least one additional match is found.

Integration with patent_processor.py
-------------------------------------
Replace the placeholder block inside on_related_files_clicked with:

    searcher = RelatedFilesSearcher(
        ops_client   = self.record.client,
        rec_input    = self.rec_input,
        filtered_df  = self.filtered_app_numbers,
        family_root  = self.familyRoot,
        models       = self.models,
        exceptions   = self.exceptions,
        reference_type = self.reference_type_widget.value,
    )
    results_df = searcher.run()

Data sources for CQL query fields
-----------------------------------
    Field   CQL op     Source
    ------  --------   -------------------------------------------------------
    pa      pa="…"     biblio XML → <applicant-name> first token (lowercase)
    in      in any     biblio XML → <inventor-name> surnames only (no country)
    pr      pr="…"     earliest date across all priority_dates in filtered_df
    ap      ap="…"     biblio XML → <application-reference date=…> for input
                       (INPADOC family XML omits app_date for EP — biblio fetch
                        is the reliable source)
    ti      ti="…"     biblio XML → <invention-title> first 5 words only
                       (full titles exceed OPS CQL length limits → 413)
    not pr  §7.5.3.6   known priority numbers (limited to 20 to avoid 413)
"""

import os
import re
import time
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Any

import pandas as pd


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helpers  (mirrors notebook helpers from §7.5.3)
# ──────────────────────────────────────────────────────────────────────────────

def process_patent_data(content: pd.DataFrame, label: str = "") -> List[str]:
    """
    Extract publication doc-numbers (any country) from a published_data_search DataFrame.
    Returns [] and prints a warning if the expected column is absent (e.g. when
    OPS returns an error response for a CQL query that matches nothing).

    Each entry is returned as "<country><doc-number>" (e.g. "EP2101496", "US20230123456").
    """
    try:
        col = "ops:publication-reference|document-id"
        if col not in content.columns:
            if label:
                print(f"  📋 {label}: 0 result(s)")
            return []

        records = [
            rec for rec in content[col].tolist()
            if isinstance(rec, dict) and rec.get("doc-number")
        ]
        doc_numbers = [
            f"{rec.get('country', '')}{rec['doc-number']}"
            for rec in records
        ]
        if label:
            print(f"  📋 {label}: {len(doc_numbers)} result(s)")
        return doc_numbers

    except Exception as e:
        print(f"  ⚠️  process_patent_data error: {e}")
        return []


def perform_patent_search(
    client,
    cql_query: str,
    doc_start: int = 1,
    doc_stop: int = 100,
    label: str = "",
) -> List[str]:
    """
    Execute one CQL search and return a list of EP doc-numbers.
    Catches 413 (query too large) and other HTTP errors gracefully.
    """
    try:
        print(f"\n  🔍 CQL: {cql_query}")
        content = client.published_data_search(
            cql=cql_query,
            range_begin=doc_start,
            range_end=doc_stop,
            output_type="dataframe",
        )
        return process_patent_data(content, label=label)
    except Exception as e:
        status = ""
        msg = str(e)
        if "413" in msg:
            status = "query too long (413)"
        elif "404" in msg:
            status = "no results (404)"
        else:
            status = msg[:80]
        print(f"  ⚠️  [{label}] skipped — {status}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────────────

class RelatedFilesSearcher:
    """
    Searches OPS for patent applications related to the current DiviTree input.

    Parameters
    ----------
    ops_client     : authenticated OPSClient (= self.record.client)
    rec_input      : cleaned doc number of the input  (e.g. 'EP2101496')
    filtered_df    : family DataFrame from FamilyRecord (self.filtered_app_numbers)
    family_root    : root ET.Element of the INPADOC XML (self.familyRoot)
    models         : epo.tipdata.ops.models module
    exceptions     : epo.tipdata.ops.exceptions module
    reference_type : 'publication' or 'application'
    """

    # Maximum number of inventors to include in a CQL 'in any' clause.
    # OPS rejects queries > ~4 KB  → keep surnames short.
    MAX_INVENTORS = 5

    # Maximum known priorities to list in the §7.5.3.6 exclusion clause.
    MAX_EXCL_PRIORITIES = 10

    # Maximum words from the title to use in the CQL 'ti=' clause.
    MAX_TITLE_WORDS = 5

    def __init__(
        self,
        ops_client,
        rec_input: str,
        filtered_df: pd.DataFrame,
        family_root,
        models,
        exceptions,
        reference_type: str = "publication",
        country_code: str = "",
    ):
        self.client         = ops_client
        self.rec_input      = rec_input
        self.filtered_df    = filtered_df
        self.family_root    = family_root
        self.models         = models
        self.exceptions     = exceptions
        self.reference_type = reference_type
        # Country code of the input file (e.g. "EP", "US", "WO").
        # Falls back to the first two characters of rec_input if not supplied.
        self.country_code   = country_code.strip().upper() or rec_input.strip()[:2].upper()

        # Populated by _extract_biblio_data()
        self._applicant: str       = ""
        self._inventors: List[str] = []   # surnames only, lowercase, no country tag
        self._title_words: str     = ""   # first MAX_TITLE_WORDS words, lowercase

        # Populated by _extract_dates_from_df() + _extract_biblio_data()
        self._priority_date: str        = ""
        self._filing_date: str          = ""   # from biblio XML (reliable for EP)
        self._known_priorities: List[str] = []

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def run(self, doc_stop: int = 100) -> pd.DataFrame:
        """
        Run all five search strategies and return a deduplicated DataFrame.

        Returns
        -------
        pd.DataFrame  columns: ep_number, found_by
        """
        print(f"\n{'='*70}")
        print(f"🔎 Related Files Search for: {self.rec_input}")
        print(f"{'='*70}")

        self._extract_dates_from_df()     # priority date + known priorities
        self._extract_biblio_data()       # applicant, inventors, title, filing date
        self._print_extracted_data()

        if not self._priority_date and not self._filing_date:
            print("\n⚠️  No dates found — cannot build CQL queries.")
            return pd.DataFrame(columns=["ep_number", "found_by"])

        strategy_results: Dict[str, List[str]] = {
            "§7.5.3.2 Dates+Applicant":
                self._search_dates_applicant(doc_stop),
            "§7.5.3.3 Dates+Applicant+Inventors":
                self._search_dates_applicant_inventors(doc_stop),
            "§7.5.3.4 Dates+Applicant+Title":
                self._search_dates_applicant_title(doc_stop),
            "§7.5.3.5 Full (inventors+title)":
                self._search_full(doc_stop),
            "§7.5.3.6 Exclusion check":
                self._search_exclusion_check(doc_stop),
        }

        known = self._known_numbers()

        # All strategies exclude numbers already in the current family — those are
        # family members, not "related files beyond the family".
        # The input file itself is re-added explicitly after deduplication so it
        # always appears at the top of the results as a reference point.
        merged: Dict[str, List[str]] = {}
        for label, numbers in strategy_results.items():
            for num in numbers:
                clean = num.strip()
                if clean in known:
                    continue   # already in the current family — skip
                merged.setdefault(clean, []).append(label)

        if not merged:
            print("\nℹ️  No related applications found beyond the current family.")
            return pd.DataFrame(columns=["pub_number", "found_by"])

        result_df = pd.DataFrame(
            [{"pub_number": n, "found_by": " | ".join(ls)}
             for n, ls in sorted(merged.items())]
        )

        # ── Include the input file itself when other results exist ──────────
        # Strip only the trailing kind code (e.g. "A1", "B2") from rec_input.
        # self.country_code is the authoritative CC (from country_widget.value).
        num_part = self.rec_input.strip()[len(self.country_code):]
        input_clean = self.country_code + re.sub(r"[A-Z]+$", "", num_part).strip()
        if input_clean not in {row["pub_number"] for _, row in result_df.iterrows()}:
            input_row = pd.DataFrame([{
                "pub_number": input_clean,
                "found_by":   "input file",
            }])
            result_df = pd.concat([input_row, result_df], ignore_index=True)

        print(f"\n✅ {len(result_df)} related publication(s) (including input file):")
        print(result_df.to_string(index=False))
        return result_df

    # ──────────────────────────────────────────────────────────────────────
    # Data extraction
    # ──────────────────────────────────────────────────────────────────────

    def _extract_dates_from_df(self) -> None:
        """
        From filtered_df: earliest priority date + known priority numbers.
        Filing date is NOT taken from the DataFrame because INPADOC family XML
        omits <date> for EP application-references; it is extracted instead
        from the biblio fetch in _extract_biblio_data().
        """
        if self.filtered_df is None or self.filtered_df.empty:
            return

        # Earliest priority date across the whole family
        all_dates: List[str] = []
        for _, row in self.filtered_df.iterrows():
            pd_dict = row.get("priority_dates")
            if isinstance(pd_dict, dict):
                all_dates.extend(v for v in pd_dict.values() if v)
        if all_dates:
            self._priority_date = self._normalise_date(min(all_dates))

        # Union of all known priority numbers
        seen: set = set()
        for _, row in self.filtered_df.iterrows():
            pn_list = row.get("priority_numbers")
            if isinstance(pn_list, list):
                for p in pn_list:
                    if p and isinstance(p, str):
                        seen.add(p)
        self._known_priorities = sorted(seen)

    def _extract_biblio_data(self) -> None:
        """
        One OPS published_data(biblio) call for the input publication.
        Extracts: applicant, inventor surnames, title (first N words),
        and filing date (the <application-reference> date in biblio XML,
        which is reliably present even when INPADOC family XML omits it).
        """
        try:
            print(f"\n📡 Fetching biblio data for {self.rec_input} …")
            raw_xml = self.client.published_data(
                reference_type=self.reference_type,
                input=self.models.Epodoc(self.rec_input),
                constituents=["biblio"],
                output_type="raw",
            )
            self._parse_biblio_xml(raw_xml)
        except Exception as e:
            print(f"  ⚠️  Biblio fetch failed: {e}")

    def _parse_biblio_xml(self, raw_xml: str) -> None:
        """
        Parse the OPS exchange-biblio XML using namespace-agnostic iteration.

        Extracts:
        - <applicant-name><name>…       → self._applicant  (first word only)
        - <inventor-name><name>…        → self._inventors  (surnames, no country)
        - <invention-title lang="en">…  → self._title_words (first N words)
        - <application-reference><date> → self._filing_date (YYYYMMDD)

        Inventor name format from OPS:  "Surname Firstname [CC]"
        We keep only the first token (surname) to avoid 413 errors.
        """
        try:
            root = ET.fromstring(raw_xml)

            # ── Applicant ────────────────────────────────────────────────
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "applicant-name":
                    for child in elem:
                        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if ctag == "name" and child.text:
                            # "PANASONIC CORP [JP]" → "panasonic"
                            first_word = child.text.strip().split()[0].rstrip(",").lower()
                            self._applicant = first_word
                            print(f"  ✅ Applicant: {child.text.strip()}"
                                  f"  →  CQL token: '{first_word}'")
                            break
                    if self._applicant:
                        break

            # ── Inventors ────────────────────────────────────────────────
            inventor_names = []
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "inventor-name":
                    for child in elem:
                        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if ctag == "name" and child.text:
                            inventor_names.append(child.text.strip())

            if inventor_names:
                # "Oshima Mitsuaki [JP]"  →  "oshima"   (surname = first token)
                # "Gillard, Paul"         →  "gillard"  (before comma)
                surnames = []
                for name in inventor_names:
                    # Remove country tag like [JP]
                    clean = re.sub(r"\[[A-Z]{2}\]", "", name).strip()
                    # Split on comma (Last, First) or space (First Last)
                    if "," in clean:
                        surname = clean.split(",")[0].strip()
                    else:
                        surname = clean.split()[0].strip()
                    surnames.append(surname.lower())
                self._inventors = list(dict.fromkeys(surnames))  # deduplicate, keep order
                print(f"  ✅ Inventors ({len(self._inventors)}): "
                      f"{', '.join(self._inventors[:self.MAX_INVENTORS])}"
                      f"{'…' if len(self._inventors) > self.MAX_INVENTORS else ''}")

            # ── Title (first N words only) ────────────────────────────────
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "invention-title":
                    lang_attr = (
                        elem.attrib.get("lang")
                        or elem.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "")
                    )
                    if lang_attr in ("en", "", None) and elem.text:
                        full_title = elem.text.strip().lower()
                        words = full_title.split()
                        self._title_words = " ".join(words[:self.MAX_TITLE_WORDS])
                        print(f"  ✅ Title (first {self.MAX_TITLE_WORDS} words): "
                              f"'{self._title_words}'")
                        break

            # ── Filing date from application-reference ────────────────────
            # OPS biblio XML places the filing date in one of:
            #   <application-reference date="YYYYMMDD">          ← attribute
            #   <application-reference><date>YYYYMMDD</date>     ← child element
            #   <application-reference><document-id><date>…      ← grandchild
            # We try all three; if still nothing, fall back to any <date> in the doc.
            def _try_date(raw) -> str:
                return self._normalise_date(raw) if raw else ""

            # Pass 1 — application-reference (attribute or any descendant <date>)
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag != "application-reference":
                    continue
                d = _try_date(elem.attrib.get("date"))
                if d:
                    self._filing_date = d
                    break
                for desc in elem.iter():
                    dtag = desc.tag.split("}")[-1] if "}" in desc.tag else desc.tag
                    if dtag == "date":
                        d = _try_date(desc.text)
                        if d:
                            self._filing_date = d
                            break
                if self._filing_date:
                    break

            # Pass 2 — fallback: any <date> element in the whole document
            if not self._filing_date:
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag == "date":
                        d = _try_date(elem.text)
                        if d and d >= "19700101":
                            self._filing_date = d
                            break

            if self._filing_date:
                print(f"  ✅ Filing date (from biblio): {self._filing_date}")
            else:
                # Debug: print every tag+attrib+text that might hold a date
                # so we can identify the exact XML structure OPS returns
                print(f"  ⚠️  Filing date not found in biblio XML"
                      f" — ap clause omitted from CQL")
                print(f"  🔬 Debug — all date-related nodes in biblio XML:")
                for _e in root.iter():
                    _t = _e.tag.split("}")[-1] if "}" in _e.tag else _e.tag
                    if any(k in _t.lower() for k in ("date", "applic", "filing")):
                        print(f"       tag={_t!r:35} attrib={_e.attrib} text={_e.text!r}")

        except Exception as e:
            print(f"  ⚠️  Biblio XML parse error: {e}")

    def _print_extracted_data(self) -> None:
        inv_display = ", ".join(self._inventors[:self.MAX_INVENTORS])
        if len(self._inventors) > self.MAX_INVENTORS:
            inv_display += "…"
        print(f"\n📊 CQL parameters extracted:")
        print(f"   pn  = '{self.country_code}'")
        print(f"   ap  = '{self._filing_date}'  (filing date from biblio)")
        print(f"   pr  = '{self._priority_date}'  (earliest priority date)")
        print(f"   pa  = '{self._applicant}'")
        print(f"   in  = '{inv_display}'  (surnames only, max {self.MAX_INVENTORS})")
        if self._title_words:
            print(f"   ti  = '{self._title_words}'  (first {self.MAX_TITLE_WORDS} words)")
        print(f"   known priorities: {len(self._known_priorities)}"
              f" (exclusion limited to {self.MAX_EXCL_PRIORITIES})")

    # ──────────────────────────────────────────────────────────────────────
    # CQL query builders
    # ──────────────────────────────────────────────────────────────────────

    def _base_clause(self) -> str:
        """pn + pr + ap — the common core of every query, scoped to self.country_code."""
        parts = [f'pn = "{self.country_code}"']
        if self._priority_date:
            parts.append(f'pr = "{self._priority_date}"')
        if self._filing_date:
            parts.append(f'ap = "{self._filing_date}"')
        return " AND ".join(parts)

    # ──────────────────────────────────────────────────────────────────────
    # Search strategies  §7.5.3.2 – §7.5.3.6
    # ──────────────────────────────────────────────────────────────────────

    def _search_dates_applicant(self, doc_stop: int) -> List[str]:
        """§7.5.3.2  pn + pr + ap + pa"""
        if not self._applicant:
            print("\n  ⏭️  §7.5.3.2 skipped (no applicant)")
            return []
        cql = f'{self._base_clause()} AND pa = "{self._applicant}"'
        return perform_patent_search(self.client, cql, doc_stop=doc_stop,
                                     label="§7.5.3.2")

    def _search_dates_applicant_inventors(self, doc_stop: int) -> List[str]:
        """
        §7.5.3.3  pn + pa + in any "surname1, surname2, …" + pr + ap

        Uses 'in any' with comma-separated surnames (no country tags, no
        full names) to stay well within OPS CQL length limits.
        """
        if not self._applicant or not self._inventors:
            print("\n  ⏭️  §7.5.3.3 skipped (no applicant or inventors)")
            return []
        inv_clause = ", ".join(self._inventors[:self.MAX_INVENTORS])
        cql = (
            f'{self._base_clause()}'
            f' AND pa = "{self._applicant}"'
            f' AND in any "{inv_clause}"'
        )
        return perform_patent_search(self.client, cql, doc_stop=doc_stop,
                                     label="§7.5.3.3")

    def _search_dates_applicant_title(self, doc_stop: int) -> List[str]:
        """
        §7.5.3.4  pn + ti (first N words) + pr + ap + pa

        The full title easily exceeds OPS CQL limits → use first N words only.
        """
        if not self._applicant or not self._title_words:
            print("\n  ⏭️  §7.5.3.4 skipped (no applicant or title)")
            return []
        cql = (
            f'{self._base_clause()}'
            f' AND pa = "{self._applicant}"'
            f' AND ti = "{self._title_words}"'
        )
        return perform_patent_search(self.client, cql, doc_stop=doc_stop,
                                     label="§7.5.3.4")

    def _search_full(self, doc_stop: int) -> List[str]:
        """
        §7.5.3.5  pn + in=("s1" or "s2" …) + ti (first N words) + pr + ap + pa
        """
        if not self._applicant or not self._inventors or not self._title_words:
            print("\n  ⏭️  §7.5.3.5 skipped (missing applicant, inventors, or title)")
            return []
        inv_or = " or ".join(
            f'"{n}"' for n in self._inventors[:self.MAX_INVENTORS]
        )
        cql = (
            f'{self._base_clause()}'
            f' AND pa = "{self._applicant}"'
            f' AND in = ({inv_or})'
            f' AND ti = "{self._title_words}"'
        )
        return perform_patent_search(self.client, cql, doc_stop=doc_stop,
                                     label="§7.5.3.5")

    def _search_exclusion_check(self, doc_stop: int) -> List[str]:
        """
        §7.5.3.6  Same as §7.5.3.2 but excluding already-known priorities.

        Limited to MAX_EXCL_PRIORITIES entries to avoid a 413 error.
        Anything returned here is NOT covered by the existing family priorities.
        """
        if not self._applicant:
            print("\n  ⏭️  §7.5.3.6 skipped (no applicant)")
            return []
        if not self._known_priorities:
            print("\n  ⏭️  §7.5.3.6 skipped (no known priorities to exclude)")
            return []

        # Use only the first 10 priorities to avoid 413, and strip country prefix
        # so each token is shorter (e.g. "1996932019" instead of "EP1996932019")
        excl_list = self._known_priorities[:10]
        excl = ", ".join(excl_list)
        pr_clause = f'(pr = "{self._priority_date}" not pr any "{excl}")'

        parts = [f'pn = "{self.country_code}"', pr_clause]
        if self._filing_date:
            parts.append(f'ap = "{self._filing_date}"')
        parts.append(f'pa = "{self._applicant}"')

        cql = " AND ".join(parts)
        return perform_patent_search(self.client, cql, doc_stop=doc_stop,
                                     label="§7.5.3.6")

    # ──────────────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_date(raw: Any) -> str:
        """Return 8-digit YYYYMMDD string, or '' if unparseable."""
        if not raw:
            return ""
        s = str(raw).strip().replace("-", "")
        return s if re.fullmatch(r"\d{8}", s) else ""

    def _known_numbers(self) -> set:
        """
        Set of full publication/application numbers already in the current family,
        restricted to self.country_code (supplied directly from country_widget.value).
        Kind codes are stripped; numbers are stored WITH their country prefix
        (e.g. "EP4408276") to match the format returned by process_patent_data.
        """
        cc = self.country_code   # e.g. "EP", "US", "WO" — set in __init__

        known: set = set()
        if self.filtered_df is None or self.filtered_df.empty:
            return known
        for _, row in self.filtered_df.iterrows():
            for col in ("pub_number", "app_number"):
                val = row.get(col)
                if isinstance(val, str) and val.startswith(cc):
                    # Strip trailing kind codes (A1, B2, A, B, …) keeping the CC prefix
                    clean = cc + re.sub(r"[A-Z]+$", "", val[len(cc):]).strip()
                    if len(clean) > len(cc):   # guard against bare "EP" with no number
                        known.add(clean)
        return known

    # Backward-compatible alias
    def _known_ep_numbers(self) -> set:
        return self._known_numbers()
