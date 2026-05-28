from collections import Counter, defaultdict
import os, re
from datetime import datetime
from typing import Optional, Dict, Tuple, List, Set
import xml.etree.ElementTree as ET
import pandas as pd
from IPython.display import display

from epo.tipdata.ops import OPSClient, models, exceptions

from patent_analysis.helpers import convert_japanese_priority_number, sort_orap
from patent_analysis.ops_client_wrapper import OPSClientWrapper

from pprint import pprint
import logging
#  logging.basicConfig(level=logging.DEBUG)

import time
import requests
from requests.exceptions import ReadTimeout, RequestException

# Set display options for pandas
pd.set_option('display.max_rows', None)  # Display all rows
pd.set_option('display.max_columns', None)  # Display all columns
pd.set_option('display.width', None)  # Avoid line wrapping
pd.set_option('display.max_colwidth', None)  # Show full column content

class FamilyRecord:
    """
    Represents family record in the European Patent Office (EPO) database.
    Provides methods to fetch, parse, and process patent family data.
    """
    
    # Put more fundamental/lower-level methods earlier in the class definition:
    def __init__(
        self, 
        reference_type: str, 
        doc_number: str, 
        country: Optional[str] = None, 
        kind: Optional[str] = None, 
        constituents: Optional[str] = None, 
        countrySelection=None, 
        output_type: Optional[str] = None,
    ):
        """
        Initialize the FamilyRecord object.
        
        Args:
        - reference_type (str): The type of reference (e.g., 'publication', 'application').
        - doc_number (str): The document number.
        - country (str): The country code (e.g., 'EP' for Europe).
        - kind (Optional[str]): The kind code (e.g., 'A' for application, 'B' for publication).
        - constituents (Optional[str]): Constituents for the OPSClient.
        - countrySelection (Optional[List[str]]): List of countries for filtering.
        - output_type (Optional[str]): Output type for the OPSClient.        
        """        
        # self.client: OPSClient = OPSClientWrapper(key=os.getenv("OPS_KEY"), secret=os.getenv("OPS_SECRET"))
        self.client: OPSClient = OPSClient(key=os.getenv("OPS_KEY"), secret=os.getenv("OPS_SECRET"))
        self.reference_type: str = reference_type
        self.doc_number: str = doc_number        
        self.source_doc_number: Optional[str] = self.compute_source_doc_number(
            doc_number, country, kind
        )
        # print("self.source_doc_number:", self.source_doc_number)
        self.country: Optional[str] = country
        self.kind: Optional[str] = kind
        self.constituents: Optional[List[str]] = ["legal", "biblio"] if constituents is None else (
            [constituents] if isinstance(constituents, str) else constituents
        )
        # Normalize countrySelection
        if countrySelection is None:
            self.countrySelection = []
        elif isinstance(countrySelection, list):
            self.countrySelection = countrySelection
        else:
             self.countrySelection = [countrySelection] if countrySelection else []

        self.output_type: str = output_type
        self.ccw_to_wo_mapping = {}
        self.familyRoot = None
        self.xml_tree = None
        self.data = {}  # Initialize the data attribute
        self.pd = pd  # Assign pandas to self.pd

        # ⚡ Initialize rerun_attempted BEFORE any method that uses it
        self.df = None
        self.dropdown_cc = []
        self.df, self.dropdown_cc = self._initialize_dataframe()
        # print("self.df in __init__ definition after dataframe initialisation:")
        # display(self.df)
        if self.df is not None:
            self.df['source_doc_number'] = self.source_doc_number
        else:
            print("Error: DataFrame is empty or failed to initialize.")
        
    def compute_source_doc_number(self, doc_number: str, country: str, kind: Optional[str]) -> str:
        """
        Compute the source document number by concatenating country code, document number, and kind code.
        
        Args:
        - doc_number (str): The document number.
        - country (str): The country code.
        - kind (Optional[str]): The kind code.

        Returns:
        - str: The computed source document number.
        """        
        # source_doc_number = f"{country}{doc_number}"  # Start with concatenating country code and doc_number
        source_doc_number = f"{country or ''}{doc_number}"  # Start with concatenating country code and doc_number
        if kind:
            # source_doc_number = f"{country}{doc_number}{kind}"
            source_doc_number = f"{source_doc_number}{kind}"
        return source_doc_number
        
    def _fetch_xml_tree(
        self, 
        reference_type: str, 
        doc_number: str, 
        country: str, 
        kind: Optional[str] = None,
        constituents: Optional[str] = None, 
        output_type: Optional[str] = None,
        batch_size: int = 100, 
        country_filter: Optional[List[str]] = None
    ) -> Optional[List[pd.DataFrame]]:
        """
        Fetch XML family tree from EPO OPS with retries; process family members in batches.

        Args:
            reference_type (str): The type of reference.
            doc_number (str): Document number.
            country (str): Country code.
            kind (Optional[str]): Kind code.
            constituents (Optional[str]): Constituents param.
            output_type (Optional[str]): Output type.
            batch_size (int): Number of family members to process per batch.
            country_filter (Optional[List[str]]): If provided, fetch only these country codes in large families.

        Returns:
            List[pd.DataFrame]: List of DataFrames for each batch or None on failure.
        """
        max_retries = 5
        backoff_factor = .1  # seconds delay between retries

        # Create the correct input model
        input_model = models.Docdb(doc_number, country, kind) if kind else models.Epodoc(f"{country}{doc_number}")

        # Retry loop for family fetch
        for attempt in range(1, max_retries + 1):
            try:
                # print("1. constituents:", constituents)
                self.xml_tree = self.client.family(
                    reference_type=reference_type,
                    input=input_model,
                    constituents=constituents,
                    output_type=output_type
                )                    
                return self.xml_tree

            except exceptions.HTTPError as e:
                print(f"HTTPError {attempt}/{max_retries} for {country}{doc_number}{kind or ''}: {e}")
            except ReadTimeout:
                print(f"⏳ Timeout {attempt}/{max_retries} for {country}{doc_number}{kind or ''}")
            except RequestException as e:
                print(f"⚠️ Request error {attempt}/{max_retries} for {country}{doc_number}{kind or ''}: {e}")
            except Exception as e:
                print(f"❌ Unexpected error {attempt}/{max_retries} for {country}{doc_number}{kind or ''}: {e}")

            time.sleep(backoff_factor * attempt)  # exponential backoff

        print(f"🚫 Skipping {country}{doc_number}{kind or ''} after {max_retries} failed attempts.")
        return None
    
    # This is a core data-fetching method. It should come before methods that depend on the XML data.
    def get_family_root(self) -> Optional[ET.Element]:
        """
        Get the root element of the family XML tree.

        Returns:
        - Optional[ET.Element]: The root element if XML tree is parsed successfully, None otherwise.
        """        
        if self.xml_tree is None:
            print("Error: self.xml_tree is not initialized.")
            return None
        return ET.fromstring(self.xml_tree)

    def _parse_xml(self) -> Optional[ET.Element]:
        """
        Parses XML from self.xml_tree and returns the root element.

        Returns:
        - Optional[ET.Element]: The root element if XML tree is parsed successfully, None otherwise.
        """        
        if self.xml_tree is None:
            print("Error: self.xml_tree is None.")
            return None
        try:
            # print("self.xml_tree:", self.xml_tree)
            return ET.fromstring(self.xml_tree)  # Parse the XML string
        except ET.ParseError as e:
            print(f"XML parsing failed: {e}")
            return None
            
    def _get_namespace_map(self, root=None) -> Dict[str, str]:
        """
        Returns the namespace map for XML parsing.§

        Returns:
        - Dict[str, str]: The namespace map.
        """
        root = root or self.familyRoot
        return {k: v for k, v in root.nsmap.items()} if hasattr(root, "nsmap") else {
            "ns0": "http://ops.epo.org",
            "ns1": "http://www.epo.org/exchange",
        }
        
    def get_ns_prefix(self, tag: str, nsmap: Dict[str, str]) -> str:
        """
        Resolves the namespace prefix and builds a fully qualified tag.

        Args:
            tag (str): The tag with a namespace prefix (e.g., "ns0:family-member").
            nsmap (dict): A dictionary mapping namespace prefixes to their URIs.

        Returns:
            str: The fully qualified tag with the namespace URI.
    
        Raises:
            ValueError: If the tag does not contain a prefix or the prefix is not found in nsmap.
        """
        parts = tag.split(':')
        if len(parts) != 2:
            raise ValueError(f"Invalid tag format: {tag}. Expected format 'prefix:tag'.")
    
        prefix, local_name = parts
        namespace_uri = nsmap.get(prefix)
        if not namespace_uri:
            raise ValueError(f"Namespace prefix '{prefix}' not found in nsmap.")
    
        return f"{{{namespace_uri}}}{local_name}"

    # Utility for XML parsing. Parses the XML tree and returns a list of family members.        
    def _extract_family_members(self, root=None):
        """
        Extract family members from XML tree.
        Always return a list of Elements.
        """
        root = root or self.familyRoot
        if root is None:
            return []
        # Assuming each member is under 'document-id'
        nsmap = self._get_namespace_map()
        members = root.findall('.//ns0:family-member', nsmap)
        return members or []

    def _parse_application_data(self, family_member, nsmap) -> Tuple[str, Dict]:
        """
        Extracts application details such as country, kind, number, and date.

        Args:
        - family_member (ET.Element): The family member element.
        - nsmap (Dict[str, str]): The namespace map.

        Returns:
        - Tuple[Optional[str], Dict[str, Any], Optional[str]]: Application number, application data, and kind text.
        """
        app_number_full = None
        app_data = {}
        app_kind_text = ""
        app_country_text = ""
        
        for application_reference in family_member.findall(f".//{self.get_ns_prefix('ns1:application-reference', nsmap)}"):  # 'exchange:application-reference'
            app_doc_number = application_reference.find(f".//{self.get_ns_prefix('ns1:doc-number', nsmap)}")                 # 'exchange:doc-number'
            app_country = application_reference.find(f".//{self.get_ns_prefix('ns1:country', nsmap)}")                       # 'exchange:country'
            app_kind = application_reference.find(f".//{self.get_ns_prefix('ns1:kind', nsmap)}")                             # 'exchange:kind'
            app_date = application_reference.find(f".//{self.get_ns_prefix('ns1:date', nsmap)}")                             # 'exchange:date'

            app_number = app_doc_number.text if app_doc_number is not None else None
            app_country_text = app_country.text if app_country is not None else 'Unknown'
            app_kind_text = app_kind.text if app_kind is not None else 'Unknown'
            app_date_text = app_date.text if app_date is not None else None
            app_number_full = f"{app_country_text}{app_number}"

            if app_number_full.startswith('EP'):
                year_prefix = '19' if int(app_number_full[2:4]) > 50 else '20'
                app_number_full = f"EP{year_prefix}{app_number_full[2:11]}"

            accession_number = app_number_full
            
            # Handle WO (PCT) mappings
            if app_kind_text == 'W' or app_country_text == 'WO':
                accession_number = f"{app_country_text}{app_kind_text}{app_number}"
                if int(app_number[:2]) > 50:
                    year_prefix = '19' 
                    app_number_full = f"WO19{app_number[:2]}{app_country_text}{app_number[2:]}"
                else:
                    year_prefix = app_number[:4]
                    serial_number = app_number[4:].lstrip('0')
                    app_number_full = f"WO{year_prefix}{app_country_text}{serial_number}"
                self.ccw_to_wo_mapping[accession_number] = (app_number_full, app_date_text)

            # Include additional fields
            app_data = {
                'accession_number': accession_number,
                'app_number': app_number_full,
                'app_country': app_country_text,
                'app_kind': app_kind_text,
                'app_date': app_date_text,
                'priority_numbers': [],   # Placeholder for priority numbers
                'orap': [],               # Placeholder for other application references # not 'orap': {}, 
                'priority_dates': {},     # Placeholder for priority dates
                'pub_number': '',
                'pub_country': '',
                'pub_kind': '',
                'pub_date': '',
                'legal_events': []
            }

        return app_number_full, app_data, app_kind_text, app_country_text

    def _parse_publication_data(self, index, family_member, nsmap, data, app_number_full, app_data, country_codes):
        """
        Parse and process publication data from the family member element.

        Args:
        - family_member (ET.Element): The family member element.
        - nsmap (Dict[str, str]): The namespace map.
        - data (Dict[str, Any]): The data dictionary to update with parsed information.
        - app_number_full (str): The full application number.
        - app_data (Dict[str, Any]): The application data dictionary to update.
        - country_codes (Set[str]): A set to track country codes.
        """        
        pub_data = {
            'pub_number': None,
            'pub_country': None,
            'pub_kind': None,
            'pub_date': None
        }
    
        for publication_reference in family_member.findall(f".//{self.get_ns_prefix('ns1:publication-reference', nsmap)}"): # 'exchange:publication-reference'
            pub_attrs = {
                "pub_number": publication_reference.find(f".//{self.get_ns_prefix('ns1:doc-number', nsmap)}"), # 'exchange:doc-number', nsmap)}"
                "pub_country": publication_reference.find(f".//{self.get_ns_prefix('ns1:country', nsmap)}"),   # 'exchange:country'
                "pub_kind": publication_reference.find(f".//{self.get_ns_prefix('ns1:kind', nsmap)}"),         # 'exchange:kind'
                "pub_date": publication_reference.find(f".//{self.get_ns_prefix('ns1:date', nsmap)}")     # 'exchange:date'
            }
        
            # Extract text values, ensuring defaults where necessary
            pub_data = {key: (elem.text if elem is not None else None) for key, elem in pub_attrs.items()}
            pub_data['pub_country'] = pub_data['pub_country'] or 'Unknown'
                
            if pub_data['pub_number'][:2] == 'WO' and pub_data['pub_country'] != 'WO':
                pub_data['pub_country'] = 'WO'
            
            if pub_data['pub_number'] and str(pub_data['pub_number']).isdigit():
                pub_data['pub_number'] = f"{pub_data['pub_country']}{pub_data['pub_number']}"
            elif pub_data['pub_number'] and pub_data['pub_number'][0].isdigit():
                # pub_number starts with a digit but contains letters (e.g. '11201908897PA' for SG).
                # Prepend the country code so downstream code can parse it correctly.
                pub_data['pub_number'] = f"{pub_data['pub_country']}{pub_data['pub_number']}"
                
            # print("pub_data['pub_country'], pub_data['pub_number'], pub_data['pub_kind']:", pub_data['pub_country'], pub_data['pub_number'], pub_data['pub_kind'])
            
            if app_number_full in data:
                app_data.update({k: v for k, v in pub_data.items() if v})  # Update only non-empty values
                country_codes.add(pub_data["pub_country"])
            
        return pub_data
        
    def _parse_priority_claims(
        self, index, family_member, nsmap, data, 
        app_number_full, app_data, app_kind_text, app_country_text, pub_data
    ):
        """
        Extracts and processes priority claims for patent applications.

        Args:
        - family_member (ET.Element): The family member element.
        - nsmap (Dict[str, str]): The namespace map.
        - data: Data dictionary
        - app_number_full: Full application number
        - app_data: Application data dictionary
        - app_kind_text: Application kind
        - app_country_text: Application country
        - pub_data: Publication data dictionary

        Returns:
        - Tuple[Optional[str], Dict[str, Any], Optional[str]]: Application number, application data, and kind text.
        """
        # Initialize data structures once
        app_data.setdefault('priority_numbers', [])
        app_data.setdefault('orap', [])            
        app_data.setdefault('priority_dates', {})
        
        # Early exit if orap already set or app not in data
        if app_data['orap'] or app_number_full not in data:
            return
            
        # ========================================================================
        # PHASE 1: COLLECT ALL PRIORITIES (no decisions yet)
        # ========================================================================        
        for priority_claim in family_member.findall(f".//{self.get_ns_prefix('ns1:priority-claim', nsmap)}"):
            elements = { 
                key: priority_claim.find(f".//{self.get_ns_prefix(f'ns1:{key}', nsmap)}")
                for key in ['doc-number', 'country', 'kind', 'date']
            }

            if elements['doc-number'] is None:
                continue
           
            priority_country = elements['country'].text if elements['country'] is not None else 'Unknown'
            priority_kind = elements['kind'].text if elements['kind'] is not None else ''
            priority_date = elements['date'].text if elements['date'] is not None else ''

            priority_doc_number_full = f"{priority_country}{elements['doc-number'].text}"
            # print("priority_doc_number_full:", priority_doc_number_full)

            # Handle EP year prefix
            if priority_doc_number_full.startswith('EP') and len(priority_doc_number_full) > 4:
                year_prefix = '19' if int(priority_doc_number_full[2:4]) > 50 else '20'
                priority_doc_number_full = f"EP{year_prefix}{priority_doc_number_full[2:11]}"

            # Handle W kind
            if priority_kind == 'W':
                priority_doc_number_full = f"{priority_country}{priority_kind}{elements['doc-number'].text}"
        
            priority_doc_number_full = convert_japanese_priority_number(priority_doc_number_full) 
            
            # Skip self-references
            if priority_doc_number_full == app_number_full:
                continue
            
            # Update priority dates — keep the EARLIEST date seen for each priority
            if (priority_doc_number_full not in app_data['priority_dates']) or (
                priority_date and priority_date < app_data['priority_dates'][priority_doc_number_full]
            ):
                app_data['priority_dates'][priority_doc_number_full] = priority_date
                # print("app_number_full, app_data['priority_dates']:", app_number_full, app_data['priority_dates'])
                
            # Unified logic for all application types
            is_wo_app = (
                app_kind_text == 'W' or
                'WO' in (pub_data.get('pub_number') or '') or
                (len(priority_doc_number_full) >= 3 and priority_doc_number_full[2] == 'W')
            )
            
            if is_wo_app or priority_country in ['EP'] or app_country_text not in ['EP', 'WO']:
                # Add to priority_numbers
                if priority_doc_number_full not in app_data['priority_numbers']:
                    app_data['priority_numbers'].append(priority_doc_number_full)                               

        # ORAP assignment intentionally removed from here.
        # It is now done after ALL family members are parsed, in
        # _parse_xml_to_dataframe Step 2, so that in-family parents
        # are preferred over external priorities.
        
    def _parse_legal_events(self, index, family_member, nsmap, data, app_number_full, app_data):
        """
        Parse and process legal events from the family member element.

        Adds:
          - indicator  (e.g. "KR GRNT", "KR FPAY")
          - category   (e.g. "F: IP right grant", "U: Payment")
          - countries  (list of country codes, e.g. ["KR"])

        Also:
          - Sorts events by event date (ascending), undated events last.

        Optimisations vs. previous version
        ------------------------------------
        * Single-pass subtree walk per candidate element — previously the helper
          functions (extract_indicator, extract_category, extract_countries, date
          loop, desc loop, text_bits loop) each called elem.iter() independently,
          walking the same subtree up to 6 times.  Now one loop collects every
          field simultaneously.
        * Sort only once — the previous version sorted parsed_events and then
          re-sorted the entire accumulated app_data["legal_events"] list on every
          call.  Now we sort once after extending.
        * Compile the two date regexes once at function scope rather than per-call.
        """

        # ── helpers ──────────────────────────────────────────────────────────

        def clean_str(val):
            if val is None:
                return None
            s = str(val).strip()
            if s == "" or s == "[]":
                return None
            return s

        _RE_DATE8     = re.compile(r"^(\d{8})$")
        _RE_DATE_ISO  = re.compile(r"(\d{4}-\d{2}-\d{2})")
        _RE_DATE8_ANY = re.compile(r"(\d{8})")

        def normalize_date_raw(d):
            d = clean_str(d)
            if not d:
                return None
            m = _RE_DATE8.fullmatch(d)
            if m:
                try:
                    return datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
                except Exception:
                    return d
            m2 = _RE_DATE_ISO.search(d)
            if m2:
                return m2.group(1)
            m3 = _RE_DATE8_ANY.search(d)
            if m3:
                try:
                    return datetime.strptime(m3.group(1), "%Y%m%d").strftime("%Y-%m-%d")
                except Exception:
                    pass
            return d

        def local_name(elem):
            return elem.tag.split("}")[-1].lower() if isinstance(elem.tag, str) else ""

        _CC2      = re.compile(r"^[A-Z]{2}$")
        _CC2_WORD = re.compile(r"^[A-Z]{2}\b")

        def _parse_country_string(entry):
            out = []
            for p in re.split(r"[,\s;/|]+", entry.strip()):
                p = p.strip()
                if not p:
                    continue
                if _CC2.fullmatch(p):
                    out.append(p)
                elif _CC2_WORD.match(p):
                    out.append(p[:2])
            return out

        def dt_sort_key(d):
            ts = pd.to_datetime(d, errors="coerce")
            if pd.isna(ts):
                return (1, pd.Timestamp.max)
            return (0, ts)

        # ── candidate element collection ─────────────────────────────────────

        EVENT_NAMES = {"legal-event", "legal-event-data", "event-data",
                       "event", "legal-data", "legal-status-data"}
        FALLBACK_TAGS = ("legal", "legal-data", "legalinfo", "legalstatus")

        candidates = []
        for elem in family_member.iter():
            name = local_name(elem)
            if name in EVENT_NAMES:
                candidates.append(elem)

        if not candidates:
            for tag in FALLBACK_TAGS:
                candidates.extend(e for e in family_member.iter() if tag in local_name(e))

        candidates = list({id(c): c for c in candidates}.values())

        # ── field names for single-pass walk ─────────────────────────────────

        _INDICATOR_TAGS = {"event-indicator", "event_indicator", "indicator"}
        _CATEGORY_TAGS  = {"category", "cat", "event-category", "event_category", "group", "type"}
        _DATE_TAGS      = {"l007ep", "l018ep", "l019ep", "event-date", "date", "publication-date"}
        _DESC_TAGS      = {"pre", "title", "text", "description", "event-text", "event-description"}
        _COUNTRY_TAGS   = {
            "country", "countries", "origin-country", "origin_country", "authority",
            "event-country", "event_countries", "legal-country", "legal_countries",
            "register-country", "register_countries",
        }
        _COUNTRY_ATTRS  = ("country", "countries", "origin", "origincountry", "authority", "auth")
        _INDICATOR_ATTRS = ("event-indicator", "indicator")
        _CATEGORY_ATTRS  = ("category", "cat", "group")

        # ── main parsing loop ────────────────────────────────────────────────

        parsed_events = []
        seen = set()

        for le in candidates:
            # Fast attribute grabs (no subtree walk needed)
            event_code = clean_str(le.attrib.get("code"))
            event_desc = clean_str(le.attrib.get("desc"))

            indicator = (
                clean_str(le.attrib.get("event-indicator"))
                or clean_str(le.attrib.get("indicator"))
            )
            category = (
                clean_str(le.attrib.get("category"))
                or clean_str(le.attrib.get("cat"))
                or clean_str(le.attrib.get("group"))
            )

            raw_date = clean_str(le.attrib.get("date")) or clean_str(le.attrib.get("dateMigr"))
            if raw_date == "00010101":
                raw_date = None

            country_raw: list[str] = []
            for attr in _COUNTRY_ATTRS:
                v = clean_str(le.attrib.get(attr))
                if v:
                    country_raw.append(v)

            # ── SINGLE-PASS subtree walk ──────────────────────────────────
            text_bits: list[str] = []
            for c in le.iter():
                ln = local_name(c)
                txt = clean_str(c.text)

                if not indicator and ln in _INDICATOR_TAGS and txt:
                    indicator = txt
                if not category and ln in _CATEGORY_TAGS and txt:
                    category = txt
                if not raw_date and ln in _DATE_TAGS and txt:
                    raw_date = txt
                if not event_desc and ln in _DESC_TAGS and txt:
                    event_desc = txt
                if ln in _COUNTRY_TAGS and txt:
                    country_raw.append(txt)
                if txt:
                    text_bits.append(txt)
            # ─────────────────────────────────────────────────────────────

            event_date = normalize_date_raw(raw_date)

            # Fallback description from any child text
            if not event_desc and text_bits:
                event_desc = " ".join(text_bits[:2])

            # Clean up date echoes in description
            if event_desc and event_date:
                event_desc = re.sub(r"\[" + re.escape(event_date) + r"\]", "", event_desc).strip()
            if event_desc:
                event_desc = event_desc.replace("[]", "").strip() or None

            if not (event_code or event_desc or event_date or indicator or category):
                continue

            # Build country list (add indicator as possible country source)
            if indicator:
                country_raw.append(indicator)
            countries: list[str] = []
            seen_cc: set[str] = set()
            for entry in country_raw:
                for cc in _parse_country_string(entry):
                    if cc not in seen_cc:
                        seen_cc.add(cc)
                        countries.append(cc)

            display_desc = event_desc or (f"🔑 code-only event:{event_code}" if event_code else None)
            if not display_desc:
                continue

            key = (event_code, display_desc, event_date, indicator, category, tuple(countries))
            if key in seen:
                continue
            seen.add(key)

            parsed_events.append({
                "code":      event_code,
                "desc":      display_desc,
                "date":      event_date,
                "indicator": indicator,
                "category":  category,
                "countries": countries,
            })

        if parsed_events:
            _sort_key = lambda ev: (dt_sort_key(ev.get("date")), ev.get("code") or "", ev.get("desc") or "")
            parsed_events.sort(key=_sort_key)

            app_data.setdefault("legal_events", [])
            app_data["legal_events"].extend(parsed_events)
            # Sort once after extending — not after every family member
            app_data["legal_events"].sort(key=_sort_key)

        return parsed_events
        
    def _process_family_member(self, index, family_member: ET.Element, nsmap: Dict[str, str], data: Dict, country_codes: Set[str]):
        """
        Parses and processes data for a single family member.

        Args:
        - family_member (ET.Element): The family member element.
        - nsmap (Dict[str, str]): The namespace map.
        - data (Dict[str, Any]): The data dictionary to update with parsed information.
        - country_codes (Set[str]): A set to track country codes.
        """
        app_number_full, new_data, app_kind_text, app_country_text = self._extract_application_data(family_member, nsmap)
    
        if not app_number_full:
            return  # Skip processing if no valid application data

        app_data = data[app_number_full]  # existing or new
        for key in new_data:
            if isinstance(app_data.get(key), list):
                app_data[key].extend(new_data[key])
            elif isinstance(app_data.get(key), dict):
                app_data[key].update(new_data[key])
            else:
                app_data[key] = new_data[key]

        country_codes.add(app_data['app_country'])
        
        if isinstance(data[app_number_full]['priority_numbers'], list):
            data[app_number_full]['priority_numbers'].extend(app_data.get('priority_numbers', []))
        else:
            data[app_number_full]['priority_numbers'].update(app_data.get('priority_numbers', set()))

        if isinstance(data[app_number_full]['orap'], list):
            data[app_number_full]['orap'].extend(app_data.get('orap', []))
        else:
            data[app_number_full]['orap'].update(app_data.get('orap', set()))

        pub_data = self._parse_publication_data(index, family_member, nsmap, data, app_number_full, app_data, country_codes)
        
        self._parse_priority_claims(index, family_member, nsmap, data, app_number_full, app_data, app_kind_text, app_country_text, pub_data)
        self._parse_legal_events(index, family_member, nsmap, data, app_number_full, app_data)               

    def _add_missing_countries(self, data: Dict[str, Dict], country_codes: Set[str]):
        """
        Ensures all selected countries are represented in the data.

        Args:
        - data (Dict[str, Dict[str, Any]]): The data dictionary to update with missing countries.
        - country_codes (Set[str]): A set of existing country codes.
        """
        missing_countries = set(self.countrySelection) - country_codes
        for country in missing_countries:
            data[f"{country or 'Unknown'}0000000"] = {
                'accession_number': '',
                'app_number': f"{country or 'Unknown'}0000000",
                'app_country': country,
                'app_kind': None,
                'app_date': None,
                'priority_numbers': [],
                'orap': [], # 'orap': {}, 
                'orap_history': [],
                'priority_dates': {},
                'legal_events': [],
                'pub_number': None,
                'pub_country': None,
                'pub_kind': None
            }
            
    # Sorting function
    def custom_sort_key(self, item):
        # print("myItem:", item)
        if len(item) > 2 and item[2] == 'W':  # Check if 'W' is in the third position
            return (0, item)  # Highest priority
        elif 'EP' in item:
            return (1, item)  # Second priority
        else:
            return (2, item)  # Lowest priority
          
    def _create_dataframe(self, data: Dict[str, Dict]) -> pd.DataFrame:
        """Flattens extracted data into a pandas DataFrame, including earliest event_date."""

        flattened_data = []

        def dt_sort_key(d):
            ts = pd.to_datetime(d, errors="coerce")
            if pd.isna(ts):
                return (1, pd.Timestamp.max)
            return (0, ts)

        for value in data.values():
            raw_events = [e for e in value.get("legal_events", []) if isinstance(e, dict)]
            raw_events.sort(key=lambda ev: (dt_sort_key(ev.get("date")), ev.get("code") or "", ev.get("desc") or ""))

            event_dates = [
                pd.to_datetime(ev.get("date"), errors="coerce")
                for ev in raw_events
                if ev.get("date")
            ]
            earliest_event = min([d for d in event_dates if pd.notna(d)], default=pd.NaT)

            flattened_data.append(
                {
                    **value,
                    "priority_numbers": sorted(list(dict.fromkeys(value.get("priority_numbers", []))), key=self.custom_sort_key),
                    "orap": sorted(list(dict.fromkeys(value.get("orap", []))), key=self.custom_sort_key),

                    "legal_events": [
                        {
                            "code": ev.get("code", ""),
                            "desc": ev.get("desc", ""),
                            "date": ev.get("date", ""),
                            "indicator": ev.get("indicator", ""),
                            "category": ev.get("category", ""),
                            "countries": ev.get("countries", []) or [],
                        }
                        for ev in raw_events
                    ],
                    "event_date": earliest_event,
                    "pub_number": value.get("pub_number", None),
                    "pub_country": value.get("pub_country", None),
                    "pub_kind": value.get("pub_kind", None),
                }
            )

        df = pd.DataFrame(flattened_data)
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")

        if "app_number" in df.columns:
            df = df[df["app_number"] != "Unknown0000000"]

        if df.empty:
            print("Warning: DataFrame is empty after filtering out 'Unknown0000000'.")
            
        # print("df in _create_dataframe method after dataframe creation:")
        # display(df)
        return df

    def _parse_xml_to_dataframe(
        self, constituents: Optional[list] = None
    ) -> Tuple[Optional[pd.DataFrame], List[str]]:
        """
        Parses the OPS family XML to extract data for each family member.
        Delegates reruns to _fetch_legal_events_per_input for cleaner logic.
        """
        # --- Defensive normalization of constituents ---
        if not constituents:
            constituents = ['legal']
        elif isinstance(constituents, str):
            constituents = [constituents]
        elif not isinstance(constituents, list):
            constituents = list(constituents) if constituents else ['legal']
        # Preserve for rerun use
        self.constituents = constituents
        # print(f"� Ensured constituents = {constituents}")

        if self.familyRoot is None:
            self.familyRoot = self._parse_xml()
        if self.familyRoot is None:
            return None, []

        nsmap = self._get_namespace_map()
        data = defaultdict(lambda: {
            "accession_number": "",
            "app_number": "",
            "app_country": "",
            "app_kind": None,
            "app_date": None,
            "pub_number": "",
            "pub_country": "",
            "pub_kind": "",
            "priority_numbers": [],
            "orap": [],
            "orap_history": [],
            "priority_dates": {},
            "legal_events": []
        })
        country_codes = set()

        family_members = self._get_family_members()
        print(f"✅ Extracted {len(family_members)} family members in the parsed XML.")

        # Step 1: process bibliographic + legal event info (priority collection only, no ORAP decision)
        for index, fm in enumerate(family_members):
            self._process_family_member(index, fm, nsmap, data, country_codes)

        # Step 2: assign ORAP now that ALL family members are known.
        # Strategy: always use the EARLIEST priority date as ORAP, regardless of
        # whether it is a family member or external. External oraps are handled
        # correctly by tree_creation which creates a synthetic root entry for them.
        #
        # Also build a reverse map accession_number -> app_number so that WO
        # accession keys like 'DKW9700190' (stored as app_number 'WO1997DK00190')
        # are resolved correctly when they appear in another member's priority_dates.
        accession_to_app = {}
        for app_num, app_data in data.items():
            an = app_data.get('accession_number', '')
            if an and an != app_num:
                accession_to_app[an] = app_num

        for app_num, app_data in data.items():
            if not app_data['orap'] and app_data['priority_dates']:
                # Normalise priority keys: replace accession_number refs with app_numbers
                normalised = {}
                for k, v in app_data['priority_dates'].items():
                    resolved = accession_to_app.get(k, k)
                    normalised[resolved] = v
                # Always pick the globally earliest priority
                best = min(normalised.items(), key=lambda x: (x[1], x[0]))[0]
                app_data['orap'] = [best]
            elif not app_data['orap']:
                app_data['orap'] = [app_num]  # no priorities at all → self-root

        # # DEBUG: Check US apps before subset method
        # print("\n🔍 DEBUG: Checking US apps BEFORE subset method")
        # for app_num, app_data in data.items():
        #     if app_num.startswith('US'):
        #         print(f"  {app_num}:")
        #         print(f"    orap: {app_data.get('orap', 'NOT SET')}")
        #         print(f"    priority_numbers: {app_data.get('priority_numbers', [])[:3]}...")

        # Step 3: US/CN/JP/KR divisional hierarchy (filing-date-based)
        self._assign_divisional_hierarchy(data)
        df = self._create_dataframe(data)
        return df, sorted(country_codes)

    def _assign_orap_by_subset(self, data: Dict[str, Dict]) -> None:
        print("\n" + "="*80)
        print("🔍 SUBSET METHOD CALLED - Starting diagnostics")
        print("="*80)
    
        # Count US apps
        us_count = sum(1 for app in data if app.startswith('US'))
        us_without_orap = sum(1 for app, d in data.items() 
                              if app.startswith('US') and not d.get('orap'))
    
        print(f"Total US apps: {us_count}")
        print(f"US apps without ORAP: {us_without_orap}")
    
        # Show first few US apps
        for app, d in list(data.items())[:5]:
            if app.startswith('US'):
                print(f"{app}: orap={d.get('orap')}, priorities={d.get('priority_numbers', [])[:2]}")

    def _assign_divisional_hierarchy(self, data: Dict[str, Dict]) -> None:
        """
        Build parent-child hierarchy using SUBSET logic for countries that use this pattern.
        
        Note: EP divisionals are NOT handled here - they use a different system
        (parent application reference, not priorities) handled by sort_orap.
        
        Divisional applications in some jurisdictions (US, CN, JP, KR, etc.) include
        their parent application number in their priority list, creating a subset
        relationship. This works for those specific patent offices.
        
        Example:
            US8145231:  ['JP2007...', 'JP2008...', 'US67348210']
            US8315642:  ['JP2007...', 'JP2008...', 'US67348210', 'US96813910']
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                        US8315642's priorities ⊃ US8145231's priorities
                        → US8145231 is the parent of US8315642
        
        Strategy: Pre-group apps by country, then for each app with 2+ priorities,
        find the same-country app whose priority set is the largest proper subset.
        That's the immediate parent. Pre-grouping reduces comparisons from O(n²)
        across all apps to O(k²) per country where k << n.
        
        Returns the set of app_numbers that got subset-based ORAP assignment.
        """
        from collections import defaultdict

        subset_assigned_apps = set()

        # Step 1: Pre-group eligible apps by country.
        # Eligibility: not EP/WO, not a PCT (app_kind != 'W'), and 2+ priorities.
        by_country = defaultdict(list)
        for app_num, app_data in data.items():
            country = app_num[:2]
            if country in ('EP', 'WO'):
                continue
            if app_data.get('app_kind') == 'W':
                continue
            priorities = frozenset(app_data.get('priority_numbers', []))
            if len(priorities) >= 2:
                by_country[country].append((app_num, priorities, app_data))

        # Step 2: Within each country group, find subset parents.
        for country, apps in by_country.items():
            for app_num, priorities, app_data in apps:
                candidates = [
                    (other_num, len(other_prio))
                    for other_num, other_prio, _ in apps
                    if other_num != app_num and other_prio and other_prio < priorities
                ]
                if candidates:
                    # Largest proper subset = immediate parent
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    app_data['orap'] = [candidates[0][0]]
                    subset_assigned_apps.add(app_num)

                    # print(f"✓ {country} divisional: {app_num} → parent: {candidates[0][0]} "
                    #       f"(priorities: {len(priorities)} vs {candidates[0][1]})")

        self.subset_assigned_apps = subset_assigned_apps
        return subset_assigned_apps
    
    def _assign_divisional_hierarchy2(self, data: Dict[str, Dict]) -> None:
        """
        Build parent-child hierarchy using SUBSET logic for countries that use this pattern.
        
        Note: EP divisionals are NOT handled here - they use a different system
        (parent application reference, not priorities) handled by sort_orap.
        
        Divisional applications in some jurisdictions (US, CN, JP, KR, etc.) include
        their parent application number in their priority list, creating a subset
        relationship. This works for those specific patent offices.
        
        Example:
            US8145231:  ['JP2007...', 'JP2008...', 'US67348210']
            US8315642:  ['JP2007...', 'JP2008...', 'US67348210', 'US96813910']
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                        US8315642's priorities ⊃ US8145231's priorities
                        → US8145231 is the parent of US8315642
        
        Strategy: For each non-EP app with 2+ priorities, find the app with the
        LARGEST priority set that is still a proper subset. That's the immediate parent.
        
        Returns the set of app_numbers that got subset-based ORAP assignment.
        """
        
        # Track which apps actually found a subset parent
        subset_assigned_apps = set()
        
        for app_num, app_data in data.items():
            country = app_num[:2]
            app_kind = app_data.get('app_kind', '')
            
            # CRITICAL: Skip EP and WO apps
            # - EP: Uses different divisional system (handled by sort_orap)
            # - WO: Already points to earliest priority (should never be changed)
            if country == 'EP' or app_kind == 'W' or country == 'WO':
                continue
            
            priorities = set(app_data.get('priority_numbers', []))
            
            # Only process apps with multiple priorities (potential divisionals)
            if not priorities or len(priorities) < 2:
                continue
            
            # Find all potential parents: apps whose priorities are a PROPER subset
            # IMPORTANT: Only consider same-country apps as potential parents
            # (e.g., US parent of US child, CN parent of CN child)
            candidates = []
            
            for other_app_num, other_app_data in data.items():
                if other_app_num == app_num:
                    continue
                
                # Only same-country apps can be subset parents
                # (US divisional's parent is US, not CN or JP)
                other_country = other_app_num[:2]
                if other_country != country:
                    continue
                
                other_priorities = set(other_app_data.get('priority_numbers', []))
                
                # Check if other_priorities is a proper subset of priorities
                if other_priorities and other_priorities < priorities:
                    # Proper subset found → potential parent
                    candidates.append((other_app_num, len(other_priorities)))
            
            if candidates:
                # Sort by priority set size (descending) → largest subset is immediate parent
                candidates.sort(key=lambda x: x[1], reverse=True)
                parent_app_num = candidates[0][0]
                
                # CRITICAL: ORAP must use APPLICATION NUMBERS
                # The tree processor uses app_number (self.tree.ap) as my_node
                old_orap = app_data.get('orap', [])
                app_data['orap'] = [parent_app_num]
                
                # Track that this app got subset-based assignment
                subset_assigned_apps.add(app_num)
                
                # print(f"✓ {country} divisional: {app_num} → parent: {parent_app_num} "
                #       f"(priorities: {len(priorities)} vs {candidates[0][1]})")
        
        # Store the set for later use
        self.subset_assigned_apps = subset_assigned_apps
        return subset_assigned_apps
            
    def _initialize_dataframe(self, country: Optional[str] = None, xml_tree: Optional[str] = None) -> pd.DataFrame:
        """
        Initialize the DataFrame from the fetched XML.
        Handles both small and large families, with batching if needed.
        """
        # import traceback
        # print("📍 _initialize_dataframe called from:")
        # traceback.print_stack(limit=4)
        
        country = country if country else self.country
        try:
            # Fetch XML tree if not provided
            if xml_tree is None:
                # Preliminary fetch to determine family size                
                temp_xml_tree = self._fetch_xml_tree(
                    self.reference_type, self.doc_number, self.country, 
                    self.kind, self.constituents, self.output_type
                )
                if temp_xml_tree is None:
                    print("🚫 No XML tree fetched; returning empty DataFrame.")
                    return pd.DataFrame(), []
                else:
                    self.xml_tree = temp_xml_tree
            else:
                self.xml_tree = xml_tree
                
            # Collect family members
            family_members = self._get_family_members()
            family_size = len(family_members)
            # print(f"✅ Extracted {family_size} family members in the parsed XML.")
            
            # Apply EP filter if too large
            if family_size > 100:
                nsmap = self._get_namespace_map()
                ep_members = [
                    fm for fm in family_members 
                    if fm.findtext(".//ns1:country", namespaces=nsmap) == "EP"
                ]
                family_size=len(ep_members)
                if family_size <= 100:
                    print(f"⚠️ Reduced to {len(ep_members)} EP members; no batching needed.")
                    self._get_family_members = lambda: ep_members
                    self.df, self.dropdown_cc = self._parse_xml_to_dataframe(self.constituents)
                else:
                    print(f"⚠️ Still {family_size} EP members >100; processing in batches...")
                    dfs, all_cc = [], set()
                    orig_get_members = self._get_family_members
                    for offset in range(0, family_size, 100):
                        batch_members = ep_members[offset:offset + 100]

                        self._get_family_members = lambda bm=batch_members: bm
                        try:
                            batch_df, batch_cc = self._parse_xml_to_dataframe(self.constituents)
                            if batch_df is not None and not batch_df.empty:
                                dfs.append(batch_df)
                                # Use the returned batch_cc (country codes) instead of self.dropdown_cc
                                all_cc.update(batch_cc)
                                print(f"   Processed batch {offset // 100 + 1} with {len(batch_members)} members.")
                        finally:
                            pass
                            
                    self._get_family_members = orig_get_members

                    if dfs:
                        self.df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["app_number", "pub_number"], keep="first")
                        self.dropdown_cc = sorted(all_cc)
                    else:
                        print("⚠️ No DataFrames produced from batch processing.")
                        self.df, self.dropdown_cc = pd.DataFrame(), []
            else:
                # Small families processed directly
                self.df, self.dropdown_cc = self._parse_xml_to_dataframe(self.constituents)                
                
            # Validate DataFrame
            if self.df is None or self.df.empty:
                print("⚠️ DataFrame is empty after parsing XML.")
                return pd.DataFrame(), []

            # Fix: fetch legal events individually for all family members that
            # were left with an empty legal_events list after the single OPS
            # family() call (which only embeds events for the queried document).
            # Only needed when the input is a publication reference — application
            # references do not have a matching publication endpoint to query.
            if 'legal' in (self.constituents or []) and self.reference_type == 'publication':
                self._fetch_legal_events_for_all_members()
            
            # print(self.df[['app_number', 'orap', 'legal_events']].head(20))

            # # Debugging for the environment
            # try:
            #     print("_initialize_dataframe:")
            #     display(self.df)  # to use in priority for self.df display
            # except ImportError:
            #     print(self.df.head())  # Fallback for non-Jupyter environments

            return self.df, self.dropdown_cc

        except Exception as e:
            print(f"Error initializing DataFrame: {e}")
            import traceback; traceback.print_exc()
            return pd.DataFrame(), []

    def _fetch_legal_events_for_all_members(self) -> None:
        """
        For each row in self.df whose legal_events list is empty, fetch legal
        events individually from OPS using the row's publication number.

        Background: the initial OPS family() call returns legal-event XML only
        for the queried document itself; all other family members arrive with an
        empty <legal-event> section.  This method repairs that gap with one
        targeted OPS request per member that still needs events.

        The method updates self.df in place (legal_events column).
        It is intentionally scoped to rows that are missing events so that the
        already-populated input publication row is never overwritten.

        Optimisation: requests are issued in parallel via ThreadPoolExecutor
        (max 5 workers to stay within OPS rate limits).  Results are collected
        and written back to the DataFrame only after all workers finish, so
        pandas is never touched from multiple threads simultaneously.
        """
        # # --- TEMPORARY: verify cache path is shared across worker instances ---
        # _ops_key    = os.getenv("OPS_KEY")
        # _ops_secret = os.getenv("OPS_SECRET")
        # test_client = OPSClient(key=_ops_key, secret=_ops_secret)
        # cache_attrs = [a for a in dir(test_client) if 'cache' in a.lower() or 'db' in a.lower() or 'path' in a.lower()]
        # print(f"🔍 OPSClient cache-related attrs: {cache_attrs}")
        # for attr in cache_attrs:
        #     print(f"   {attr} = {getattr(test_client, attr, '?')}")
        # # --- END DIAGNOSTIC ---
        
        import concurrent.futures

        if self.df is None or self.df.empty:
            return

        nsmap = self._get_namespace_map()

        rows_missing = self.df[
            self.df['legal_events'].apply(lambda v: not v if isinstance(v, list) else True)
        ]

        if rows_missing.empty:
            return  # Nothing to do — all members already have events.

        # print(f"ℹ️  Fetching legal events for {len(rows_missing)} family member(s) ...")

        # Capture credentials once on the main thread so workers can create
        # their own OPSClient instances.  self.client uses a SQLite-backed cache
        # whose connection is bound to the thread that created it — sharing it
        # across ThreadPoolExecutor workers raises:
        #   "SQLite objects created in a thread can only be used in that thread"
        # The fix: each worker instantiates its own client (and therefore its
        # own SQLite connection) rather than borrowing self.client.
        
        # Pre-flight: one fast TCP probe before launching any workers.
        # If OPS is unreachable (firewall / transient outage) skip all fetches
        # silently — the tree and chart outputs are unaffected.
        import socket as _socket
        _ops_ok = False
        try:
            _socket.create_connection(("ops.epo.org", 443), timeout=3.0).close()
            _ops_ok = True
        except OSError:
            pass
        if not _ops_ok:
            print(f"  ℹ️  OPS unreachable — skipping legal-event fetch "
                  f"for {len(rows_missing)} member(s) (tree/chart unaffected).")
            return
            
        _ops_key    = os.getenv("OPS_KEY")
        _ops_secret = os.getenv("OPS_SECRET")
        _fetch_errors = []   # collect quietly; print one summary at the end

        def _fetch_one(idx, row):
            """Fetch and parse legal events for a single row; return (idx, events_or_None)."""

            pub_number  = str(row.get('pub_number', '') or '').strip()
            pub_kind    = str(row.get('pub_kind',   '') or '').strip()
            app_country = str(row.get('app_country','') or '').strip()

            if not pub_number or not app_country or pub_number == row.get('app_number', ''):
                return idx, None

            kind = pub_kind if pub_kind else None
            doc_number = pub_number
            if pub_number.upper().startswith(app_country.upper()):
                doc_number = pub_number[len(app_country):]
            if kind and doc_number.upper().endswith(kind.upper()):
                doc_number = doc_number[: -len(kind)]

            try:
                import requests_cache  # ← add this line
                # Use a shared SQLite cache so repeated Submit clicks reuse results.
                # expire_after=-1 means cache never expires (legal events don't change).
                # The cache file is shared by all workers via the same path.
                cached_session = requests_cache.CachedSession(
                    cache_name=os.path.expanduser('~/.ops_legal_events_cache'),
                    backend='sqlite',
                    expire_after=-1,      # never expire — legal events are immutable
                )                
                # Thread-local client: avoids SQLite cross-thread conflict
                # thread_client = OPSClient(key=_ops_key, secret=_ops_secret)
                thread_client = OPSClient(key=_ops_key, secret=_ops_secret) # , timeout=5.0
                thread_client.request.session = cached_session  # inject cached session

                input_model = models.Docdb(doc_number, app_country, kind) if kind \
                              else models.Epodoc(f"{app_country}{doc_number}")

                max_retries    = 5
                backoff_factor = 0.1
                xml_bytes      = None
                for attempt in range(1, max_retries + 1):
                    try:
                        xml_bytes = thread_client.family(
                            reference_type='publication',
                            input=input_model,
                            constituents=['legal'],
                            output_type='raw'
                        )
                        break  # success — exit retry loop
                    except ReadTimeout:
                        print(f"  ⏳ Timeout {attempt}/{max_retries} for {pub_number}{kind or ''}")
                    except RequestException as e:
                        print(f"  ⚠️ Request error {attempt}/{max_retries} for {pub_number}{kind or ''}: {e}")
                    except Exception as e:
                        print(f"  ❌ Unexpected error {attempt}/{max_retries} for {pub_number}{kind or ''}: {e}")
                        break  # non-network error — no point retrying
                    time.sleep(backoff_factor * attempt)

                if xml_bytes is None:
                    _fetch_errors.append(pub_number)
                    return idx, None

                try:
                    member_root = ET.fromstring(xml_bytes)
                except ET.ParseError as exc:
                    print(f"  ⚠️ XML parse error for {pub_number}{kind or ''}: {exc}")
                    return idx, None

                temp_data: Dict = {'legal_events': []}
                self._parse_legal_events(
                    index=0,
                    family_member=member_root,
                    nsmap=nsmap,
                    data=defaultdict(lambda: {'legal_events': []}),
                    app_number_full=f"{app_country}{doc_number}",
                    app_data=temp_data
                )
                return idx, temp_data['legal_events'] or None

            except Exception as exc:
                _fetch_errors.append(pub_number)
                print(f"  ⚠️ Could not fetch legal events for "
                      f"{pub_number}{kind or ''}: {exc}")
                return idx, None

        # Run up to 5 parallel HTTP requests — conservative enough for OPS throttling
        results: list[tuple] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_fetch_one, idx, row): idx
                for idx, row in rows_missing.iterrows()
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    print(f"  ⚠️ Worker error: {exc}")

        # Write back to DataFrame on the main thread only
        for idx, events in results:
            if events:
                self.df.at[idx, 'legal_events'] = events
                
        # One-line summary instead of one warning per member
        if _fetch_errors:
            print(f"  ℹ️  Legal events unavailable for {len(_fetch_errors)} member(s) "
                  f"(OPS unreachable — tree/chart unaffected).")
            
        # print("✅ Legal events fetch complete.")

    def _get_family_members(self) -> List[ET.Element]:
        """Extracts and returns a list of family members from the parsed XML."""
        return list(self._extract_family_members())
    
    def _extract_application_data(self, family_member: ET.Element, nsmap: Dict[str, str]) -> Tuple[Optional[str], Dict, Optional[str]]:
        """Extracts application data and returns a tuple (app_number_full, app_data, app_kind_text)."""
        app_number_full, app_data, app_kind_text, app_country_text = self._parse_application_data(family_member, nsmap)
    
        if not app_number_full:
            print("Skipping family member: No app_number_full")
            return None, {}, None, None
    
        app_data['app_country'] = app_data.get('app_country', 'Unknown')  # Ensure app_country is set  
        return app_number_full, app_data, app_kind_text, app_country_text

    def get_pub_to_app_map(self, country: str = 'EP') -> dict:
        """Return a dict mapping EP publication serial (no kind code) to
        the EP application number.  E.g. {'EP3826075': 'EP2019188854'}.
        Used by tree_processor to embed data-appid in tooltip HTML.
        """
        if self.df is None or self.df.empty:
            return {}
        import re as _re
        _kind_re = _re.compile(r'^([A-Z]{2})(\d+)([A-Z]\d?)?$')
        result = {}
        for _, row in self.df.iterrows():
            app_num = str(row.get('app_number') or '').strip()
            pub_num = str(row.get('pub_number') or '').strip()
            if not app_num or not pub_num:
                continue
            if not app_num.upper().startswith(country.upper()):
                continue
            m = _kind_re.match(pub_num)
            if m:
                pub_serial = m.group(1) + m.group(2)   # e.g. EP3826075
                result[pub_serial] = app_num
        return result

    def get_filtered_application_numbers(self, additional_countries: Optional[str] = None) -> Optional[pd.DataFrame]:
        if self.df is None:
            return None

        if 'app_country' not in self.df.columns:
            print("Column 'app_country' is missing from the DataFrame.")
            return pd.DataFrame()  # or handle appropriately
            
        if 'pub_country' not in self.df.columns:
            self.df['pub_country'] = None  # or pd.NA or ''

        if isinstance(additional_countries, str):
            additional_countries = [additional_countries]
        additional_countries = additional_countries or []  # Ensure it's a list
        
        # Define region-based country groups
        country_groups = {
            "europe": [
                'AL', 'AT', 'BE', 'BG', 'CH', 'CY', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI', 'FR', 'GB', 'GR', 'HR', 'HU', 'IE', 
                'IS', 'IT', 'LI', 'LT', 'LU', 'LV', 'MC', 'MT', 'NL', 'NO', 'PL', 'PT', 'RO', 'RS', 'SE', 'SI', 'SK', 'SM', 
                'TR', 'MK', 'BY', 'MD', 'RU', 'UA'
            ],
            "asia": [
                'JP', 'CN', 'KR', 'TW', 'HK', 'MO', 'SG', 'TH', 'MY', 'PH', 'VN', 'ID', 'BN', 'KH', 'LA', 'MM', 'IN', 'PK', 
                'BD', 'LK', 'NP', 'BT', 'MV', 'KZ', 'UZ', 'KG', 'TJ', 'TM', 'AE', 'SA', 'IL', 'TR', 'IR', 'QA', 'KW', 'BH', 
                'OM', 'JO', 'LB', 'SY', 'YE', 'IQ', 'PS', 'AU', 'NZ', 'PG', 'FJ'
            ],
            "americas": [
                'US', 'CA', 'MX', 'GT', 'BZ', 'SV', 'HN', 'NI', 'CR', 'PA', 'CU', 'DO', 'HT', 'JM', 'TT', 'BB', 'BS', 'LC', 
                'GD', 'KN', 'AG', 'VC', 'DM', 'BR', 'AR', 'CO', 'CL', 'PE', 'VE', 'EC', 'BO', 'PY', 'UY', 'GY', 'SR', 'GF'
            ],
            "africa": [
                'DZ', 'EG', 'LY', 'MA', 'SD', 'TN', 'BJ', 'BF', 'CV', 'CI', 'GH', 'GM', 'GN', 'GW', 'LR', 'ML', 'MR', 'NE', 
                'NG', 'SN', 'SL', 'TG', 'AO', 'CM', 'CF', 'TD', 'CG', 'CD', 'GQ', 'GA', 'ST', 'BI', 'DJ', 'ER', 'ET', 'KE', 
                'MG', 'MW', 'MU', 'MZ', 'RW', 'SC', 'SO', 'TZ', 'UG', 'ZM', 'ZW', 'BW', 'LS', 'NA', 'SZ', 'ZA'
            ]
        }

        # Define filtering function
        def filter_by_region(region, exclude_second_w=False):
            cond  = self.df['app_country'].isin(country_groups[region])
            if exclude_second_w:
                cond &= ~self.df['app_country'].str[1].eq('W')  # Exclude where 'W' is second letter
            cond &= (self.df['app_kind'] == 'W') | self.df['app_number'].str.contains(r'W$|W.*$', regex=True)
            # print("cond:", cond)
            return cond 
        
        # Define main conditions
        condition_user = condition_cn = condition_ep = condition_jp = condition_us = condition_wo = False
        
        # === Basic filtering conditions ===
        input_condition = pd.Series(False, index=self.df.index)
        country_condition = pd.Series(False, index=self.df.index)
        if self.input_appln_number:
            input_condition = self.df['app_number'] == self.input_appln_number
            self.input_country_code = self.input_country_code or self.input_appln_number[:2].upper()
            country_condition = self.df['app_country'] == self.input_country_code
        
        # condition_cn = (self.df['app_country'] == 'CN') & (self.df['app_kind'] == 'A')
        # condition_ep = (self.df['app_country'] == 'EP') # & (self.df['app_kind'] == 'A')
        condition_ep = (self.df['app_country'] == 'EP') & (self.df['app_country'] != 'WO')
        # condition_jp = (self.df['app_country'] == 'JP') & (self.df['app_kind'] == 'A')
        # condition_us = (self.df['app_country'] == 'US') & (self.df['app_kind'] == 'A')
        condition_wo = ((self.df['app_country'] == 'WO') | (self.df['pub_country'] == 'WO')) # & (self.df['app_kind'] == 'A')
        # print("condition_wo:", condition_wo)
        # Handle user-defined country selection
        selected_countries = set(self.countrySelection or []) | set(additional_countries)
        condition_user = self.df['app_country'].isin(selected_countries) if selected_countries else pd.Series(False, index=self.df.index)
        
        condition_europe = filter_by_region("europe")
        condition_asia = filter_by_region("asia", exclude_second_w=True)
        condition_americas = filter_by_region("americas")
        condition_african = filter_by_region("africa", exclude_second_w=True)
        
        # Apply filters and return the filtered DataFrame
        filtered_df = self.df[
            input_condition | 
            country_condition | 
            condition_ep | condition_jp | condition_us | condition_cn | 
            condition_wo | 
            condition_user | 
            condition_europe | 
            condition_asia | 
            condition_americas | 
            condition_african # Always include the input application
            # input_condition | condition_cn | condition_ep | condition_jp | condition_us | condition_wo | condition_xx # | condition_europe | condition_asia | condition_americas | condition_african
        ].copy()
                
        # Ensure 'orap' and 'orap_history' columns exist
        if 'orap' not in filtered_df.columns:
            filtered_df['orap'] = None
        if 'orap_history' not in filtered_df.columns:
            filtered_df['orap_history'] = None
            
        # filtered_df.loc[:, 'orap'] = None
        # filtered_df.loc[:, 'orap_history'] = None

        if not filtered_df.empty:
            # Run sort_orap as before (handles EP divisional parent-picking via
            # priority_numbers[-1] and WO via ccw_to_wo_mapping), then override
            # only where Step 2 found an earlier priority date.
            # This preserves EP/WO hierarchy while fixing cases where sort_orap
            # picks a later family member over an earlier external priority.

            # Snapshot Step-2 orap values (set on self.df before filtering)
            step2_orap = (
                self.df.set_index('app_number')['orap']
                if 'app_number' in self.df.columns and 'orap' in self.df.columns
                else pd.Series(dtype=object)
            )

            # Build a date-lookup from priority_dates across all rows
            def _get_orap_date(app_num, orap_val):
                """Return the date string for orap_val as seen in app_num's priority_dates."""
                if not orap_val or not isinstance(orap_val, str):
                    return None
                rows = self.df[self.df['app_number'] == app_num]
                if rows.empty:
                    return None
                pd_dates = rows.iloc[0].get('priority_dates', {})
                if isinstance(pd_dates, dict):
                    return pd_dates.get(orap_val)
                return None

            subset_apps = getattr(self, 'subset_assigned_apps', set())
            if subset_apps:
                subset_mask = filtered_df['app_number'].isin(subset_apps)
                non_subset = filtered_df[~subset_mask].copy()
                non_subset = non_subset.apply(
                    lambda row: sort_orap(row, self.ccw_to_wo_mapping, self.data), axis=1
                )
                filtered_df = pd.concat(
                    [non_subset, filtered_df[subset_mask]], ignore_index=False
                ).sort_index()
            else:
                filtered_df = filtered_df.apply(
                    lambda row: sort_orap(row, self.ccw_to_wo_mapping, self.data), axis=1
                )

            # Override sort_orap result with Step-2 value when Step-2 found an earlier date.
            # NEVER override EP or WO members: sort_orap correctly builds their
            # divisional hierarchy via priority_numbers[-1] / ccw_to_wo_mapping.
            def _maybe_override(row):
                app_num = row['app_number']
                # EP and WO hierarchy is handled entirely by sort_orap — skip override
                app_cc = row.get('app_country', app_num[:2] if len(app_num) >= 2 else '')
                if app_cc in ('EP', 'WO'):
                    return row
                s2 = step2_orap.get(app_num)
                if not s2:
                    return row
                s2_val = s2[0] if isinstance(s2, list) else s2
                if not s2_val:
                    return row
                current = row.get('orap')
                current_val = current[0] if isinstance(current, list) else current
                if not current_val or current_val == s2_val:
                    return row
                # Compare dates: prefer whichever is earlier
                pd_dates = row.get('priority_dates', {})
                if not isinstance(pd_dates, dict):
                    return row
                # Also check ccw_to_wo_mapping for WO accession keys
                def _resolve_date(key):
                    if key in self.ccw_to_wo_mapping:
                        return self.ccw_to_wo_mapping[key][1]
                    return pd_dates.get(key)
                s2_date = _resolve_date(s2_val)
                cur_date = _resolve_date(current_val)
                if s2_date and cur_date and s2_date < cur_date:
                    row['orap'] = s2_val
                return row

            filtered_df = filtered_df.apply(_maybe_override, axis=1)
                
        # Add missing parent placeholders
        filtered_df = self._add_missing_parent_placeholders(filtered_df)
        
        # print("display(self.df)")
        # display(self.df)
        # print()
        # print("display(filtered_df)")
        # display(filtered_df)
        return filtered_df  

    def _add_missing_parent_placeholders(self, filtered_df: pd.DataFrame) -> pd.DataFrame:
        """
        Detect and add placeholder entries for missing parent applications.
    
        This is critical for Asian (JP, CN, KR) applications which often claim
        original priorities that aren't in the family but need to be roots.
    
        Args:
            filtered_df: The filtered DataFrame
        
        Returns:
            DataFrame with missing parent placeholders added
        """
        # Extract all orap values (parents) from the filtered data
        all_orap_values = set()
        for orap in filtered_df['orap'].dropna():
            if isinstance(orap, list):
                all_orap_values.update(orap)
            elif isinstance(orap, str) and orap:
                all_orap_values.add(orap)
    
        # Extract all application numbers in the family
        all_app_numbers = set(filtered_df['app_number'].dropna())
    
        # Find missing parents (orap values not in app_numbers)
        missing_parents = all_orap_values - all_app_numbers
    
        # print(f"\n🔍 Placeholder detection:")
        # print(f"  Total ORAP values: {len(all_orap_values)}")
        # print(f"  Sample ORAP values: {list(all_orap_values)[:5]}")
        # print(f"  Total app_numbers in family: {len(all_app_numbers)}")
        # print(f"  Missing parents: {len(missing_parents)}")
        # if missing_parents:
        #     print(f"  Sample missing: {list(missing_parents)[:5]}")
    
        # Filter to only include specific patterns (JP, CN, KR priorities)
        # This prevents adding every priority claim as a parent
        missing_parents_to_add = set()
        for parent in missing_parents:
            # Only add if it's a Japanese, Chinese, or Korean priority
            # (These are the ones that cause missing hierarchy)
            if parent.startswith(('JP', 'CN', 'KR')) and len(parent) > 4:
                # Skip if it's clearly just a year/date (too short)
                missing_parents_to_add.add(parent)
                # print(f"  ✓ Will add placeholder: {parent}")
    
        if not missing_parents_to_add:
            # print("  ℹ️ No missing JP/CN/KR parents detected")
            return filtered_df

        # Sorted once, used for both print AND row creation
        sorted_missing_parents = sorted(missing_parents_to_add, reverse=True)

        # print(f"Found {len(missing_parents_to_add)} missing parent applications:")
        for parent in sorted_missing_parents:
            pass
            # count = sum(
            #     1 for orap in filtered_df['orap'] 
            #     if (isinstance(orap, list) and parent in orap) or 
            #        (isinstance(orap, str) and parent == orap)
            # )
            # print(f"  {parent} ({count} children)")
    
        # Create placeholder entries for missing parents
        placeholder_rows = []
        for parent_num in sorted_missing_parents:
            # Determine country and basic info from parent number
            country = parent_num[:2]
        
            # Create minimal placeholder entry
            placeholder = {
                'accession_number': parent_num,
                'app_number': parent_num,
                'app_country': country,
                'app_kind': 'A',  # Assume application
                'appln_filing_date': '',  # Unknown
                'pub_number': parent_num,
                'pub_kind': '',
                'pub_country': country,
                'pub_date': '',
                'priority_numbers': [],
                'priority_dates': {},
                'orap': [parent_num],  # Points to itself (root)
                'orap_history': None,
                'legal_events': [],
                'source_doc_number': self.source_doc_number if hasattr(self, 'source_doc_number') else ''
            }
            placeholder_rows.append(placeholder)
    
        # Create DataFrame from placeholders
        if placeholder_rows:
            placeholders_df = pd.DataFrame(placeholder_rows)
        
            # Ensure column types match
            for col in filtered_df.columns:
                if col not in placeholders_df.columns:
                    # Use appropriate default based on column type in filtered_df
                    if filtered_df[col].dtype == 'object':
                        placeholders_df[col] = ''
                    elif filtered_df[col].dtype == 'float64':
                        placeholders_df[col] = 0.0
                    elif filtered_df[col].dtype == 'int64':
                        placeholders_df[col] = 0
                    else:
                        placeholders_df[col] = None                    
        
            # Reorder columns to match filtered_df
            placeholders_df = placeholders_df[filtered_df.columns]
        
            # Concatenate with filtered data
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=FutureWarning)
                result_df = pd.concat([filtered_df, placeholders_df], ignore_index=True)
    
            # print(f"Added {len(placeholder_rows)} placeholder parent entries")
            return result_df
    
        return filtered_df
    
    def process_fami_record(self, additional_countries: Optional[str] = None) -> None:
        """
        Process and display the family record.
        Retrieves filtered application numbers and prints them.

        Returns:
        - Optional[Tuple[pd.DataFrame, str]]: Tuple containing the filtered DataFrame and the source type, or None if no data.
        """

        source = 'Epodoc' if self.kind is None else 'Docdb'

        # Use the method to get filtered publication numbers
        result = self.get_filtered_application_numbers(additional_countries)
        if result is not None and not result.empty:
            # print(f"Filtered Publication numbers (Source: {source}):")
            # display(result)
            return result, source
        else:
            print("DataFrame is empty. No data to process.")
            return None
