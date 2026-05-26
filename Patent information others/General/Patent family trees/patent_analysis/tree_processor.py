# patent_analysis/tree_processor.py

from collections import Counter
import os, re, io, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from typing import Optional, Dict, Tuple, List, Set, Any

from epo.tipdata.ops import OPSClient, models, exceptions
from patent_analysis.ops_client_wrapper import OPSClientWrapper

import json
import base64
import xml.etree.ElementTree as ET
from IPython.display import Markdown, display
from PIL import Image
import requests
from pprint import pprint
import logging
from datetime import datetime

# Set display options for pandas
pd.set_option('display.max_rows', None)  # Display all rows
pd.set_option('display.max_columns', None)  # Display all columns
pd.set_option('display.width', None)  # Avoid line wrapping
pd.set_option('display.max_colwidth', None)  # Show full column content

# logger = logging.getLogger(__name__)  # At top of your file
# logger.setLevel(logging.INFO)         # or DEBUG in development

class TreeProcessor:
    """
    The TreeProcessor class is designed to process a hierarchical tree of patent application data, 
    generate a text-based summary, and manage the interaction with the OPSClientWrapper API to retrieve detailed information about the patent family.
    The TreeProcessor class is responsible for writing results to a file, and handling different patent-related entities such as applications, 
    publications, and legal events. It uses the OPSClientWrapper to interact with the European Patent Office API.
    """

    # ── ANSI colour constants (computed once at class definition time) ────────
    # Hoisted from colorize_code_and_date() so they are never rebuilt per call.
    _C_ORANGE    = "\033[38;5;214m"
    _C_BLUE      = "\033[38;5;39m"
    _C_RESET     = "\033[0m"
    _C_HIGHLIGHT = "\033[1;93m"   # bold yellow for renewal years 18-20

    # Exact-match codes → colour (O(1) dict lookup)
    _EXACT_COLORS: dict = {
        # Group 1: Withdrawals / lapse / ceased
        "9":            "\033[91m",
        "10":           "\033[91m",
        "ADWI":         "\033[91m",
        "0009183":      "\033[91m",
        "RFPR":         "\033[91m",
        "RDEC":         "\033[91m",
        "0009299LAPS":  "\033[91m",
        "0009250":      "\033[91m",
        "GBPC":         "\033[91m",
        "PG25":         "\033[91m",
        # Group 2: Refusals
        "11":           "\033[95m",
        "REFU":         "\033[95m",
        "0009181":      "\033[95m",
        "EPIDOSNREF2":  "\033[95m",
        # Group 3: Revocations
        "4":            "\033[94m",
        "REVO":         "\033[94m",
        "0009271":      "\033[94m",
        "EPIDOSNREV1":  "\033[94m",
        # Group 4: Opposition / observations
        "0009264":      "\033[96m",
        "TIPA":         "\033[96m",
        "EPIDOSNTIPA":  "\033[96m",
        # Group 5: Appeals
        "NOAPE":         "\033[93m", # Appeal dossier modified
        "OBAPE":         "\033[93m", # Communication from Board of Appeal
        "REFNE":         "\033[93m", # Appeal reference recorded
        "IRAPE":         "\033[93m", # Interlocutory decision in appeal
        "IRREV":         "\033[93m", # Interlocutory revision (revocation scenario)
        "IRPRO":         "\033[93m", # Interlocutory revision concerning procedural matter    
        "EPIDOSNREFNO": "\033[93m",
        "EPIDOSNNOA2O": "\033[93m",
        "EPIDOSNNOA3O": "\033[93m",
        "EPIDOSNNOA4O": "\033[93m",
        "EPIDOSNNOA9O": "\033[93m",
        # Group 6: Oppositions
        "7":            "\033[92m",
        "0009260":      "\033[92m",
        "OBSO":         "\033[92m",
        "NOAPO":         "\033[93m", # Appeal dossier modified – opposition context      
        "IDOP":         "\033[92m", # Interlocutory decision in opposition
        "EPIDOSNIDO1":  "\033[92m",
        "0009299OPPO":  "\033[92m",
        "EPIDOSNOBS2":  "\033[92m",
        "OPEX":         "\033[92m",
        "REJO":         "\033[92m",
        # Group 7: Grants
        "8":            "\033[38;5;214m",
        "12":           "\033[38;5;214m",
        "0009210":      "\033[38;5;214m",
        "IGRA":         "\033[38;5;214m",
        "IGRE":         "\033[38;5;214m",
        "AGRA":         "\033[38;5;214m",
        "EPIDOSDIGR1":  "\033[38;5;214m",
        "EPIDOSNIGR1":  "\033[38;5;214m",
        "EPIDOSDIGR3":  "\033[38;5;214m",
        "EPIDOSNIGR3":  "\033[38;5;214m",
        "EPIDOSDIGR5":  "\033[38;5;214m",
        "EPIDOSNIGR5":  "\033[38;5;214m",
        "EPIDOSDIGR7":  "\033[38;5;214m",
        "EPIDOSNIGR7":  "\033[38;5;214m",
        # Group 8: Divisionals
        "0008199DANR":  "\033[95m",
        "0008299DANR":  "\033[95m",
        "AC":           "\033[95m",
        # Group 9: Unitary-patent (UPP)
        "P01": "\033[95m", "P02": "\033[95m", "P03": "\033[95m",
        "P04": "\033[95m", "P05": "\033[95m", "P06": "\033[95m",
        "P07": "\033[95m", "P20": "\033[95m",
        "P90": "\033[95m", "P93": "\033[95m",        
        "U01": "\033[95m", "U02": "\033[95m", "U03": "\033[95m",
        "U04": "\033[95m", "U05": "\033[95m", "U06": "\033[95m",
        "U07": "\033[95m", "U20": "\033[95m",
        "U90": "\033[95m", "U93": "\033[95m",
        "UDEC": "\033[95m", "UDFI": "\033[95m", "UREG": "\033[95m",
        "UPP-STATUS":       "\033[95m",
        "0009700UREQ01":    "\033[95m",
        "0009700UREQ02":    "\033[95m",
    }

    # Substring patterns that appear inside longer codes (compiled once).
    # Ordered so that the first match wins — same semantics as the old loop.
    _SUBSTR_COLOR_PATTERNS: list = [
        # (compiled_regex, colour)
        (re.compile(r"ADWI|0009299LAPS|0009250|GBPC|PG25|RFPR|RDEC|0009183"), "\033[91m"),  # red: withdrawal/lapse
        (re.compile(r"EPIDOSNREF2|0009181"),                                    "\033[95m"),  # magenta: refusal
        (re.compile(r"EPIDOSNREV1|0009271"),                                    "\033[94m"),  # blue: revocation
        (re.compile(r"EPIDOSNTIPA|0009264"),                                    "\033[96m"),  # cyan: opposition/obs
        (re.compile(r"EPIDOSNREFNO|EPIDOSNNOA"),                                "\033[93m"),  # yellow: appeal
        (re.compile(r"EPIDOSNIDO1|EPIDOSNOBS2|0009260|0009299OPPO"),            "\033[92m"),  # green: opposition
        (re.compile(r"EPIDOSDIGR|EPIDOSNIGR|0009210"),                          "\033[38;5;214m"),  # orange: grant
        (re.compile(r"0008199DANR|0008299DANR"),                                "\033[95m"),  # magenta: divisional
    ]

    # Codes excluded from the generic ADW/EXP/REV/LAPS fallback
    _EXP_EXCLUSIONS: frozenset = frozenset({"EXPT", "EPIDOSNEXP2"})
    _RE_ADW_GENERIC = re.compile(r"ADW|EXP|REV|LAPS")

    # ANSI escape pattern for filter_tree_by_event_code (compiled once)
    _RE_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;,]*m')
    # ── end colour constants ──────────────────────────────────────────────────

    def __init__(
        self, 
        tree: Dict[str, Any], 
        root: Dict[str, Any], 
        initFlag: str, 
        listAPs: List[str], 
        listORAPs: List[str], 
        df: pd.DataFrame, 
        familyRoot,
        interleaving_type: str = None,
    ):
        """
        Initialize a TreeProcessor instance with the necessary data structures and configurations.
        It checks for the correct structure of the tree and initializes several attributes, including setting up the file path for saving the tree output.

        Args:
            tree (dict): Tree structure containing patent data.
            root (dict): Root node of the tree.
            initFlag (str): A flag to indicate specific processing methods.
            listAPs (list): List of application identifiers.
            listORAPs (list): List of oldest application identifiers.
            df (pd.DataFrame): DataFrame containing relevant publication data.
            familyRoot: Additional family-related data.
            interleaving_type (str): Human-readable chart type, e.g. 'Applications',
                'Legal Events'. When supplied, a per-type snapshot path is also
                written so DiviTree History can re-render each chart correctly.
        
        Raises:
            TypeError: If 'tree' is a list instead of a dictionary-like object.
            AttributeError: If the 'tree' object doesn't have a 'recInp' attribute.
        """           
        self.tree = tree  # A dictionary representing the tree structure.
        self.root = root  # A dictionary containing root elements of the tree.
        self.initFlag = initFlag  # A string used to control which data is processed.
        self.listAPs = listAPs  # A list of application identifiers (APs).
        self.listORAPs = listORAPs  # A list of oldest application identifiers (ORAPs).
        self.df = df  # A pandas DataFrame containing filtered publication numbers.
        # An instance of OPSClientWrapper, initialized with API credentials.
        # self.client: OPSClientWrapper = OPSClientWrapper(key=os.getenv("OPS_KEY"), secret=os.getenv("OPS_SECRET"))
        self.client: OPSClient = OPSClient(key=os.getenv("OPS_KEY"), secret=os.getenv("OPS_SECRET"))
        self.familyRoot = familyRoot  # Represents the root of the patent family.        
        self.workdir = "/home/jovyan/output"
        os.makedirs(self.workdir, exist_ok=True)
        self.ORAP_tree_file_path = os.path.join(self.workdir, f"{self.tree.recInp}_first_output.txt")
        self.priority_tree_file_path = os.path.join(self.workdir, f"{self.tree.recInp}_second_output.txt")
        # Per-type snapshot paths — defined when interleaving_type is supplied.
        # e.g. EP2101496_Applications_first_output.txt
        if interleaving_type:
            safe = interleaving_type.replace(' ', '_')
            self.ORAP_typed_file_path     = os.path.join(self.workdir, f"{self.tree.recInp}_{safe}_first_output.txt")
            self.priority_typed_file_path = os.path.join(self.workdir, f"{self.tree.recInp}_{safe}_second_output.txt")
        else:
            self.ORAP_typed_file_path     = None
            self.priority_typed_file_path = None

    def process_tree(self, tree_type, reference_mode="application"):
        """
        Processes a tree structure for either ORAP or priority trees, generating a summary text file.

        Args:
            tree_type (str): Determines the tree type, either "ORAP" or "priority".

        Returns:
            dict: The last processed root node.
        """
        try:
            # print("process_tree: self.listORAPs:", self.listORAPs )
            if self.listORAPs is None:
                # print("Error: listORAPs is None")
                return

            if not self.listAPs:
                # print("Error: listAPs is None or empty")
                return

            listApCCs = ['WO' if len(word) > 2 and word[2] == 'W' else word[:2] for word in self.listAPs]
            country_code_counts = Counter(listApCCs)  # Count occurrences of each country code

            # creates a formatted comment for divisional applications, including the number of applications per country code.
            applicationComment = 'including: ' + ' + '.join(
                f'{count} {code}{"s" if count > 1 else ""}' for code, count in country_code_counts.items()
            )

            # Construct a message based on initFlag
            init_description_map = {
                "Show_applications": "Tree displays application data",
                "Show_priorities": "Tree displays priority data",
                "Show_parents": "Tree displays parent data",
                "Show_publications": "Tree displays publication data",
                "Show_citations": "Tree displays citation data",
                "Show_classifications": "Tree displays classification data",
                "Show_parties": "Tree displays parties data",          
                "Show_images": "Tree includes images",
                "Show_legal_events": "Tree includes legal events",
                "Show_procedural_codes": "Tree includes procedural codes",         
                # ✅ new ones
                "Show_biblio": "Tree includes bibliographic register codes",
                "Show_events": "Tree includes events register codes",
                "Show_procedural_steps": "Tree includes procedural steps",
                "Show_upp": "Tree includes unitary patent protection (UPP) codes",
                "Show_claims": "Tree includes first independent claim (Claim 1)",
                "Show_concepts": "Tree includes XFR concepts from first independent claim",
            }

            init_description = init_description_map.get(self.initFlag, f"InitFlag: {self.initFlag}")
            tree_comment = f'Divisional Applications ({self.df.shape[0]}) {applicationComment}\n{init_description} in the reference mode {reference_mode}'
            
            if not self.ORAP_tree_file_path or not self.priority_tree_file_path:
                raise ValueError("Error: Tree file paths are None or invalid.")

            processed_nodes = set()
            written_aps = set()

            # Reset all per-tree highlighting baselines
            for attr in ('_root_classifications', '_root_citations',
                         '_root_parties', '_root_concepts'):
                if hasattr(self, attr):
                    delattr(self, attr)
            
            file_path = self.ORAP_tree_file_path if tree_type == "ORAP" else self.priority_tree_file_path
            
            # The method writes this information to a tree file for later inspection.
            node_key = 'root_ap' if tree_type == "ORAP" else 'root_pr'
            # date_key = 'root_ap_date' if tree_type == "ORAP" else 'root_pr_date'
            
            start_message = "Starting ORAP Family tree Generation with" if tree_type == "ORAP" else "Starting priority Family tree Generation with"

            # Defensive check before using self.root.get
            if not isinstance(self.root, dict):
                raise TypeError(f"Expected self.root to be a dict, but got {type(self.root)}")
    
            with open(file_path, 'w') as tree_file:
                tree_file.write(f'\n{start_message} {self.tree.recInp}\n')
                tree_file.write(f'{tree_comment}\n\n')

                for r in range(len(self.root) + 1, 0, -1):
                    current_root = self.root.get(r, {})
                    # print("current_root:", current_root)
                    if not isinstance(current_root, dict):
                        print(f"Warning: Skipping root[{r}] — Expected dict but got {type(current_root)}: {current_root}")
                        continue  # Skip invalid root entry

                    # Reset per-branch highlighting baselines — each top-level
                    # branch (JP1996041583, JP1996347284 …) starts fresh so that
                    # only items new within that branch are highlighted green.
                    for attr in ('_root_classifications', '_root_citations',
                                 '_root_parties', '_root_concepts'):
                        if hasattr(self, attr):
                            delattr(self, attr)
            
                    my_node = current_root.get(node_key, [])
                    root_an = current_root.get('root_an', '')
                    root_ap = current_root.get('root_ap', '')
                    root_orap = current_root.get('root_orap', '')
                    root_pn = current_root.get('root_pn', '')
                    root_evnt = current_root.get('root_evnt', '')
                    # print(f"Processing node {my_node}, root_an {root_an}, root_ap {root_ap}, root_orap {root_orap}") # , root_evnt {root_evnt}")
                    
                    if tree_type == "priority" and not isinstance(my_node, list):
                        my_node = [my_node] if my_node else []
                        
                    # print("my_node in process_tree method:", my_node)
                
                    for node in (my_node if isinstance(my_node, list) else [my_node]):
                        # print(f"DEBUG: Processing priority tree_type - node {node}, root_ap {root_ap}, root_orap {root_orap}")
                        if isinstance(node, str) and node not in processed_nodes:
                            self.process_root(current_root, tree_file, processed_nodes, tree_type, reference_mode, written_aps)
                            # print(f"Generating tree for: {node} at level 1")
                            
                            if tree_type == "ORAP":                                
                                self.generate_tree(node, 1, tree_file, processed_nodes, current_root, tree_type, reference_mode)
                            elif tree_type == "priority":
                                # Process each node (which could be a priority)
                                for node2 in (my_node if isinstance(my_node, list) else [my_node]):
                                    if isinstance(node2, str) and node2 not in processed_nodes:
                                        self.generate_tree(node2, 1, tree_file, processed_nodes, current_root, tree_type, reference_mode)
                                # Ensure the root application itself is visited
                                for candidate in [root_ap, root_orap]:
                                    if candidate and candidate not in processed_nodes:
                                        self.generate_tree(candidate, 1, tree_file, processed_nodes, current_root, tree_type, reference_mode)

                    # ── Priority fallback: if root_pr was empty, no node entered the loop
                    # above, so process_root and generate_tree were never called.
                    # Fall back to root_ap / root_orap so the tree skeleton is always written.
                    if tree_type == "priority" and not my_node:
                        self.process_root(current_root, tree_file, processed_nodes, tree_type, reference_mode, written_aps)
                        for candidate in [root_ap, root_orap]:
                            if candidate and candidate not in processed_nodes:
                                self.generate_tree(candidate, 1, tree_file, processed_nodes, current_root, tree_type, reference_mode)                                 


            # ── Write per-type snapshot (path computed in __init__) ──
            typed_path = self.ORAP_typed_file_path if tree_type == 'ORAP' else self.priority_typed_file_path
            if typed_path:
                import shutil
                shutil.copy2(file_path, typed_path)
            # print(f'Tree processing of process_tree method completed.\n')
            
            # # After tree generation completes
            # if tree_type == "ORAP" and self.initFlag == 'Show_priorities':
            #     print(f"\n{'='*80}")
            #     print(f"📋 PRIORITY INFORMATION SUMMARY")
            #     print(f"{'='*80}")
    
            #     total_members = len(self.listAPs)
            #     members_with_priorities = sum(1 for t in range(int(self.tree.orapNb)) 
            #                                  if self.tree.pr[t] and self.tree.pr[t] != [])
    
            #     all_priority_countries = set()
            #     for t in range(int(self.tree.orapNb)):
            #         if self.tree.pr[t] and self.tree.pr[t] != []:
            #             for pri in self.tree.pr[t]:
            #                 if isinstance(pri, str) and len(pri) >= 2:
            #                     all_priority_countries.add(pri[:2])
    
            #     print(f"   📊 Total family members: {total_members}")
            #     print(f"   ✅ Members with priorities: {members_with_priorities}")
            #     print(f"   🌍 Priority countries: {', '.join(sorted(all_priority_countries))}")
            #     print(f"{'='*80}\n")
                
        except Exception as e:
            print(f"Error during process_tree ({tree_type}): {e}")

        return current_root
        
    def process_root(self, current_root, tree_file, processed_files, tree_type, reference_mode, written_aps):
        """
        This method processes the current root node of the tree and writes its information into the tree file:
        it identifies key attributes like root_an (application number) and root_ap (application ID).
        Depending on the initFlag, it invokes specialized methods (process_priorities(), process_applications(), etc.) to process different aspects of the node.
        The method checks the last 6 characters of the application to see if it matches the recInp value and formats it accordingly.
        Process a single root node, extracting relevant information and writing it to the tree file.
        Also invokes specific methods depending on the value of the 'initFlag' to further process
        entities like priorities, applications, and legal events.

        Args:
            current_root (dict): Current root node containing application and patent data.
            tree_file (file): The file object to which the processed tree information is written.
        """
        
        root_an = current_root.get('root_an', '').strip()
        root_ap = current_root.get('root_ap', '').strip()
        root_pr = current_root.get('root_pr', '')
        root_pn = current_root.get('root_pn', '').strip()
        rec_inp_right_8 = self.tree.recInp[-8:]  # Get the last 8 characters of recInp
        # print("root_an, root_ap, root_pr, rec_inp_right_8:", root_an, root_ap, root_pr, rec_inp_right_8)
            
        # The method processes root attributes and writes them to the tree file in a hierarchical format.
        # print("self.initFlag:", self.initFlag)
        tree_id = root_pn if reference_mode == 'publication' else root_ap
        if tree_id and tree_id not in processed_files and tree_id not in written_aps:
            if tree_id.endswith(rec_inp_right_8):
                # print("a. {tree_id}:", {tree_id})
                tree_file.write(f', {tree_id}* \n')
            elif 'EP' not in root_an and root_an.endswith(rec_inp_right_8):
                if root_an and root_an != tree_id:
                    # print("b. {root_an}, {tree_id}:", {root_an}, {tree_id})
                    # tree_file.write(f', {tree_id} \n') # Write with a leading comma
                    tree_file.write(f', {root_an}* / {tree_id} \n') # Write with a leading comma                
                else:
                    # print("c. {tree_id}:", {tree_id})
                    tree_file.write(f', {tree_id}* \n') # Write with a leading comma
            else:
                if root_an and root_an != tree_id:
                    # print("d. {root_an}, {tree_id}:", {root_an}, {tree_id})
                    if root_an.endswith(rec_inp_right_8):
                        tree_file.write(f', {root_an}* / {tree_id} \n') # Write with a leading comma
                    else:
                        tree_file.write(f', {root_an} / {tree_id} \n') # Write with a leading comma                        
                    # tree_file.write(f', {tree_id} \n') # Write with a leading comma                    
                else:
                    # print("e. {tree_id}:", {tree_id})                    
                    tree_file.write(f', {tree_id} \n') # Write with a leading comma

            if tree_type == "priority":
                # Add root_ap to the set of written application IDs
                written_aps.add(root_ap)
    
            # Mapping initFlag values to corresponding methods
            process_methods = {
                'Show_priorities': self.process_priorities,
                'Show_applications': self.process_applications,
                'Show_parents': self.process_parents,
                'Show_publications': self.process_publications,
                'Show_citations': self.process_citations,
                'Show_classifications': self.process_classifications,
                'Show_parties': self.process_parties,
                'Show_images': self.process_images,
                'Show_legal_events': self.process_legal_events,
                # All procedural-related flags handled by one method
                'Show_procedural_codes': self.process_procedural_codes,
                'Show_biblio': self.process_procedural_codes,
                'Show_events': self.process_procedural_codes,
                'Show_procedural_steps': self.process_procedural_codes,
                'Show_upp': self.process_procedural_codes,
                'Show_claims': self.process_claims,
                'Show_concepts': self.process_concepts,
            }

            # The initFlag is used to control the type of information processed (e.g., citations, classifications, parties).
            # Wrapped in try/except so any network or data error never aborts the tree backbone.
            if self.initFlag in process_methods:
                try:
                    # For Show_parents, inject the ap→ad lookup so dates can be shown
                    if self.initFlag == 'Show_parents' and hasattr(self.tree, 'ap') and hasattr(self.tree, 'ad'):
                        _ad_lookup = {ap: ad for ap, ad in zip(self.tree.ap, self.tree.ad) if ap}
                        current_root = dict(current_root, root_ad_lookup=_ad_lookup)
                    # print("self.initFlag in process_root:", self.initFlag, current_root)
                    process_methods[self.initFlag](current_root, 1, tree_file)
                except Exception as _supp_err:
                    _pn = current_root.get('root_pn') or current_root.get('root_an', '?')
                    print(f"  ⚠️ Supplementary data skipped for {_pn}: {_supp_err}")

    def generate_tree(self, my_node, level, tree_file, processed_nodes, current_root, tree_type="ORAP", reference_mode="application"):
        """
        Recursively generate a tree structure by navigating through nodes and writing them to the tree file.
        This method tracks processed nodes and ensures no duplicates are included.

        Args:
            my_node (str): Identifier of the current node.
            level (int): Depth level for indentation.
            tree_file (file): File object for writing tree structure.
            processed_nodes (set): Set of already processed application nodes.
            current_root (dict): Root node details (e.g., 'root_an' and 'root_ap').
            tree_type (str): Type of tree ("ORAP" or "priority").
        """
        # Check if the current node has already been processed, avoid reprocessing.
        # print("my_node, processed_nodes:", my_node, processed_nodes)
        if my_node in processed_nodes:
            return  

        # Mark the current node as processed.
        processed_nodes.add(my_node)

        # Increment the level for indentation.
        level += 1

        # Dictionary to track sibling nodes.
        sibling = {'0': 0}
        # print("my_node:", my_node)
        
        # Variable to track the previous my_node
        prev_my_node = None

        # Iterate through tree data in reverse order.
        for t in range(int(self.tree.orapNb) - 1, -1, -1):
            # orap_list = set(self.tree.orap[t].split(',')) if isinstance(self.tree.orap[t], str) else set()
            # IMPROVED: Handle orap as both list and string
            # orap can be: list (from improved family_record.py), string (legacy), or empty
            current_orap = self.tree.orap[t]
            
            if isinstance(current_orap, list):
                # orap is a list (from improved family_record.py)
                orap_list = set(current_orap)
            elif isinstance(current_orap, str) and current_orap:
                # orap is a string - split by comma for multiple values
                orap_list = set(current_orap.split(','))
            else:
                # orap is empty or None
                orap_list = set()
                
            if my_node in orap_list and orap_list:  # orap_list is non-empty set → valid parent link

                # Check if previous my_node also appears in the same orap list (if available)
                # if prev_my_node and prev_my_node not in orap_list:
                #     print(f"Warning: {prev_my_node} is missing in orap[{t}] ({orap_list}), but it should be linked to the same child {self.tree.ap[t]}.")
                    
                # print(f"t: {t} tree.orap[t] {self.tree.orap[t]}, self.tree.ap[t]) {self.tree.ap[t]}, my_node: {my_node}")  
                
                # print(f"t: {t} self.tree.evnt[t]: {self.tree.evnt[t]}")
                # print(f"Type of self.tree.pr[{t}]:", type(self.tree.pr[t]))
                # print(f"Value of self.tree.pr[{t}]:", self.tree.pr[t])

                # if self.tree.pr[t] != []:
                #     print("self.tree.ap[t]:", self.tree.ap[t])
                #     print("self.tree.an[t]:", self.tree.an[t])
                #     print("self.tree.orap[t]:", self.tree.orap[t])
                #     print("self.tree.pn[t]:", self.tree.pn[t])                                    
                #     print("self.tree.pr[t]:", self.tree.pr[t])
                s = sibling['0'] + 1
                sibling[s] = {
                    'an':  self.tree.an[t].split()[0] if isinstance(self.tree.an[t], str) else '',
                    'ap':  self.tree.ap[t].split()[0] if isinstance(self.tree.ap[t], str) else '',
                    'pn':  self.tree.pn[t].split()[0] if isinstance(self.tree.pn[t], str) else '',
                    'pr':  list(self.tree.pr[t]) if hasattr(self.tree, 'pr') and isinstance(self.tree.pr[t], (list, tuple)) else '',
                    'orap': self.tree.orap[t],
                    's':   self.tree.ap[t].split()[0] if isinstance(self.tree.ap[t], str) else '',
                    'evnt': self.tree.evnt[t] if hasattr(self.tree, 'evnt') and isinstance(self.tree.evnt[t], list) else [],
                    'ad':  (self.tree.ad[t] or '') if hasattr(self.tree, 'ad') else '',
                    'pd':  (self.tree.pd[t] or '') if hasattr(self.tree, 'pd') else '',
                    'prd': (self.tree.prd[t] or {}) if hasattr(self.tree, 'prd') else {},
                }
                # print(f"Processing node {my_node}, an {sibling[s]['an']}, ap {sibling[s]['ap']}, orap {sibling[s]['orap']}") # , evnt {sibling[s]['evnt']}")
                # print("sibling[s]:", sibling[s])
                # print("t:", t)
                # print(f"Found child: {sibling[s]['ap']}")
                # print(f"Found publication: {sibling[s]['pn']}")
                # print(f"Found prio: {sibling[s]['pr']}")
                # print(f"Found parent: {sibling[s]['orap']}")
                # print(f"Found evnt: {sibling[s]['evnt']}")
                # print()
                
                # Update prev_my_node for next iteration
                prev_my_node = sibling[s]['s'] # my_node
                # Recursively process children if not already processed
                
                sibling['0'] = s                
        # print()
        
        # If siblings exist, process them.
        if sibling['0'] > 0:
            rec_inp_right_8 = str(self.tree.recInp)[-8:]
            root_an = current_root.get('root_an', '')
            root_ap = current_root.get('root_ap', '')
            # print("root_an, root_ap, current_root:", root_an, root_ap, current_root)

            # Sort siblings by earliest priority date
            def get_priority_date(sibling_entry):
                prios = sibling_entry.get('pr', [])
                try:
                    return min(datetime.strptime(prio[:10], '%Y-%m-%d') for prio in prios if isinstance(prio, str) and len(prio) >= 10)
                except:
                    return datetime.max

            # Extract and sort siblings
            unsorted_siblings = [sibling[s] for s in range(1, sibling['0'] + 1)]
            sorted_siblings = sorted(unsorted_siblings, key=get_priority_date)

            # Rebuild the sibling dictionary with sorted entries
            for idx, entry in enumerate(sorted_siblings, 1):
                sibling[idx] = entry
                
            # Process each sorted sibling
            for s in range(1, sibling['0'] + 1):
                s_ap = sibling[s]['s']
                s_an = sibling[s]['an']
                s_pn = sibling[s]['pn']
                # print("s_ap:", s_ap)
                # print("s_an:", s_an)
                # print("s_pn:", s_pn)                
                tree_id = s_pn if reference_mode == 'publication' else s_ap
                # root_id = current_root.get('root_pn', '') if reference_mode == 'publication' else current_root.get('root_ap', '')
                # print("REFERENCE MODE:", reference_mode)
                # print("TREE ID, ROOT ID:", tree_id, root_id)
                
                if isinstance(tree_id, str) and tree_id not in [root_an, root_ap] and tree_id not in processed_nodes:

                    tree_label = '' # tree_label = "(default tree)" if tree_type == "ORAP" else "(alternative tree)"                    
                    # Write sibling data to the file with appropriate indentation.
                    # Show both app_number and pub_number for clarity (like US96813910 / US8145231B2)
                    if tree_id.find(rec_inp_right_8) >= 0:
                        # This node matches the input reference → mark with *
                        if s_an and s_an != tree_id:
                            tree_file.write(', ' * level + f"{s_an}* / {tree_id}* {tree_label}\n")
                        else:
                            tree_file.write(', ' * level + f"{tree_id}* {tree_label}\n")
                    elif 'EP' not in tree_id and s_an.find(rec_inp_right_8) >= 0:
                        # Application number matches input → mark app with *
                        if s_an and s_an != tree_id:
                            tree_file.write(', ' * level + f"{s_an}* / {tree_id} {tree_label}\n")
                        else:
                            tree_file.write(', ' * level + f"{s_an}* {tree_label}\n")
                    else:
                        # Standard case: show both app_number and pub_number for non-EP apps
                        if s_an and s_an != tree_id and 'EP' not in s_an:
                            tree_file.write(', ' * level + f"{s_an} / {tree_id} {tree_label}\n")
                        else:
                            tree_file.write(', ' * level + f"{tree_id} {tree_label}\n")

                    # Mapping initFlag values to corresponding methods.
                    process_methods = {
                        'Show_publications': self.process_publications,
                        'Show_citations': self.process_citations,
                        'Show_classifications': self.process_classifications,
                        'Show_parties': self.process_parties,
                        'Show_images': self.process_images,
                        # All procedural-related flags handled by one method
                        'Show_procedural_codes': self.process_procedural_codes,
                        'Show_biblio': self.process_procedural_codes,
                        'Show_events': self.process_procedural_codes,
                        'Show_procedural_steps': self.process_procedural_codes,
                        'Show_upp': self.process_procedural_codes,
                        'Show_claims': self.process_claims,
                        'Show_concepts': self.process_concepts,
                    }
                    
                    # Call specific methods based on the initFlag.
                    # Wrapped in try/except so any network or data error in supplementary
                    # data retrieval never aborts the tree backbone loop.
                    try:
                        if self.initFlag == 'Show_priorities':
                            pr_dict = {'root_pr': sibling[s]['pr'], 'root_prd': sibling[s].get('prd', {})}
                            self.process_priorities(pr_dict, level, tree_file)
                        elif self.initFlag == 'Show_applications':
                            ap_dict = {'root_ap': sibling[s]['ap'], 'root_ad': sibling[s].get('ad', '')}
                            self.process_applications(ap_dict, level, tree_file)
                        elif self.initFlag == 'Show_parents':
                            # Build ap→ad lookup so process_parents can show the filing date
                            _ad_lookup = {}
                            if hasattr(self.tree, 'ap') and hasattr(self.tree, 'ad'):
                                _ad_lookup = {ap: ad for ap, ad in zip(self.tree.ap, self.tree.ad) if ap}
                            orap_dict = {'root_orap': sibling[s]['orap'], 'root_ad_lookup': _ad_lookup}
                            self.process_parents(orap_dict, level, tree_file)
                        elif self.initFlag == 'Show_legal_events':
                            if not sibling[s]['evnt']:
                                sibling[s]['evnt'] = [{
                                    'code': None,
                                    'desc': 'No legal events available',
                                    'date': None
                                }]
                            sibling_dict = {
                                'root_pn': sibling[s]['pn'],
                                'root_an': sibling[s]['an'],
                                'root_evnt': sibling[s]['evnt']
                            }
                            self.process_legal_events(sibling_dict, level, tree_file)
                        elif self.initFlag == 'Show_claims':
                            sibling_dict = {
                                'root_pn': sibling[s]['pn'],
                                'root_an': sibling[s]['an'],
                                'root_pd': sibling[s].get('pd', ''),
                            }
                            self.process_claims(sibling_dict, level, tree_file)
                        elif self.initFlag == 'Show_concepts':
                            sibling_dict = {
                                'root_pn': sibling[s]['pn'],
                                'root_an': sibling[s]['an'],
                                'root_pd': sibling[s].get('pd', ''),
                            }
                            self.process_concepts(sibling_dict, level, tree_file)
                        elif self.initFlag in process_methods:
                            sibling_dict = {
                                'root_pn': sibling[s]['pn'],
                                'root_an': sibling[s]['an'],
                                'root_pd': sibling[s].get('pd', ''),
                            }
                            process_methods[self.initFlag](sibling_dict, level, tree_file)
                    except Exception as _supp_err:
                        print(f"  ⚠️ Supplementary data skipped for {sibling[s]['pn'] or sibling[s]['an']}: {_supp_err}")

                    # Recursive call for child nodes.
                    # child_node = tree_id.strip()
                    child_node = s_ap.strip()

                    # print()
                    # print("child_node:", child_node)
                    # print("processed_nodes:", processed_nodes)
                    # It recursively processes children nodes until all nodes are processed.                    
                    if child_node and child_node not in processed_nodes:
                        # print(f"Generating tree for: {child_node} and at level {level} for {self.initFlag}")
                        # print(f"Recursing into: {child_node}, Level: {level}, Processed: {processed_nodes}")
                        # Recursively call generate_tree for the child node.
                        self.generate_tree(child_node, level, tree_file, processed_nodes, current_root, tree_type, reference_mode)

            level -= 1
        return level, sibling
        
    def process_generic(self, current_root, level, tree_file, key, formatter=None):
        """
        Process and write data from the current root node to the tree file.

        Args:
            current_root (dict): Current root node data containing various attributes.
            level (int): The current depth in the tree structure, used for indentation in the output file.
            tree_file (file object): File object where the processed data will be written.
            key (str): The key in current_root that holds the data to process (e.g., 'root_ap', 'root_an').
            formatter (function, optional): A function to format the output data before writing.
               The formatter should handle individual items (e.g., strings or dictionaries) as
               the method applies it to each element in lists or nested structures.
        """
        # if formatter is not None:        
        #     print("1. key, formatter:", key, formatter)
        
        def write_data(data, level, myKey, formatter=None):
            """
            Helper function to recursively write data to the tree file.
            It handles different data types (str, list, dict) and applies formatting if a formatter is provided.

            Args:
                data: The data to write, which can be a string, list, or dictionary.
                level: The current level of recursion used to control the depth (indentation) of the output.
                formatter: A function used to format the data before writing. If not provided, raw data is written.                
            """
            # if formatter is not None:
            #     print("2. myKey, formatter:", myKey, formatter)
            
            # Handle string data
            if isinstance(data, str):
                # print("2. myKey, formatter:", myKey, formatter)
                # print("self.initFlag:", self.initFlag)
                if myKey in ['root_ap', 'root_pn', 'root_orap'] and self.initFlag != 'Show_images':
                    # print("222. myKey, data:", myKey, data)
                    data = "['" + data + "']"
                indent = ". " * (level + 1)
                formatted = formatter(data) if formatter else data
                if formatted is not None:
                    tree_file.write(f'{indent}{formatted}\n')

            elif isinstance(data, list):
                if myKey == 'root_evnt' and formatter:
                    # Apply formatter to the whole list of legal events and write once
                    formatted_text = formatter(data)
                    # print("3. data, myKey, formatted_text", data, myKey, formatted_text)
                    tree_file.write(f"{formatted_text}\n")
                elif myKey == 'root_pr':
                    # print("22. myKey, formatter:", myKey, formatter)
                    indent = ". " * (level + 1)
                    tree_file.write(f'{indent}{formatter(data) if formatter else data}\n')                  
                else:
                    # Process each item recursively
                    for item in data:
                        write_data(item, level + 1, myKey, formatter)

            elif isinstance(data, dict):
                indent = ". " * level                
                for key, value in data.items():
                    if isinstance(value, (str, int)):
                        formatted_val = formatter(value) if formatter else value
                        if formatted_val is None:
                            continue
                        line = f"{key}: {formatted_val}"
                        tree_file.write(f'{indent}{line}\n')
                    else:
                        tree_file.write(f'{indent}{key}:\n')
                        write_data(value, level + 1, key, formatter)
                        
            else:
                # print("2222. myKey, formatter:", myKey, formatter)
                indent = ". " * level
                formatted = formatter(data) if formatter else str(data)
                if formatted is not None:
                    tree_file.write(f'{indent}{formatted}\n') 
            return
        
        # Main logic to extract the relevant data from current_root and process it
        if isinstance(current_root, dict): # instead of dict only
            # Retrieve the data corresponding to the provided key from current_root
            data = current_root.get(key, None)
            if data is not None:
                # print("5. key, data, formatter:", key, data, formatter)                
                write_data(data, level, key, formatter)
            else:
                write_data(current_root, level, key, formatter)
                
        # If current_root is already a string or list, process it directly
        elif isinstance(current_root, (str, list)):
            # print("6. key, current_root:", key, current_root, formatter)            
            write_data(current_root, level, key, formatter)

        # Handle unexpected data types
        else:
            logging.warning(f"Unexpected current_root format: {type(current_root)}")
        
    def process_priorities(self, current_root, level, tree_file):
        """
        Process and write priority data (with dates) from the current root node to the tree file.

        Args:
            current_root (dict): Current root node data, containing information about priorities.
            level (int): The current depth in the tree structure, used for indentation in the output file.
            tree_file (file object): The file object for writing tree structure or priority information.
        """
        if isinstance(current_root, dict):
            pr_list = current_root.get('root_pr', [])
            prd     = current_root.get('root_prd', {}) or {}   # {pr_number: date_string}
            if isinstance(pr_list, str):
                pr_list = [pr_list] if pr_list else []
            indent = '. ' * (level + 1)
            for pr in pr_list:
                date = prd.get(pr, '') or ''
                if date and len(date) == 8 and date.isdigit():
                    date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
                label = f"{pr} [{self._C_ORANGE}{date}{self._C_RESET}]" if date else pr
                tree_file.write(f"{indent}['{label}']\n")
        else:
            self.process_generic(current_root, level, tree_file, key='root_pr')

    def process_applications(self, current_root, level, tree_file):
        """
        Process and write application data (with filing date) from the current root node to the tree file.

        Args:
            current_root (str or dict): Current root node data or application identifier.
            level (int): The current depth in the tree structure, used for indentation in the output file.
            tree_file (file object): The file object for writing tree structure or application information.
        """
        if isinstance(current_root, dict):
            ap   = current_root.get('root_ap', '')
            date = current_root.get('root_ad', '') or ''
            # Normalise YYYYMMDD → YYYY-MM-DD when needed
            if date and len(date) == 8 and date.isdigit():
                date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            indent = '. ' * (level + 1)
            label  = f"{ap} [{self._C_ORANGE}{date}{self._C_RESET}]" if date else ap
            tree_file.write(f"{indent}['{label}']\n")
        else:
            self.process_generic(current_root, level, tree_file, key='root_ap')

    def process_parents(self, current_root, level, tree_file):
        """
        Process and write parent application data (with filing date) from the current root node.

        Args:
            current_root (str, list, or dict): orap value(s), or dict with 'root_orap' and
                optional 'root_ad_lookup' {ap_number: date} for date annotation.
            level (int): The current depth in the tree structure.
            tree_file (file object): File object for writing tree structure.
        """
        if isinstance(current_root, dict):
            orap      = current_root.get('root_orap', '')
            ad_lookup = current_root.get('root_ad_lookup', {})
        else:
            orap      = current_root
            ad_lookup = {}

        # Normalise to a list of parent application numbers
        if isinstance(orap, list):
            orap_list = orap
        elif isinstance(orap, str) and orap.strip():
            orap_list = [orap.strip()]
        else:
            orap_list = []

        indent = '. ' * (level + 1)
        for parent_ap in orap_list:
            date = ad_lookup.get(parent_ap, '') or ''
            if date and len(date) == 8 and date.isdigit():
                date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            label = f"{parent_ap} [{self._C_ORANGE}{date}{self._C_RESET}]" if date else parent_ap
            tree_file.write(f"{indent}['{label}']\n")

    def process_publications(self, current_root, level, tree_file):
        """
        Process and write publication data (with publication date) from the current root node to the tree file.

        Args:
            current_root (str or dict): Current root node data or publication identifier.
            level (int): The current depth in the tree structure, used for indentation in the output file.
            tree_file (file object): The file object for writing publication information.
        """
        if isinstance(current_root, dict):
            pn   = current_root.get('root_pn', '')
            date = current_root.get('root_pd', '') or ''
            if date and len(date) == 8 and date.isdigit():
                date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            indent = '. ' * (level + 1)
            label  = f"{pn} [{self._C_ORANGE}{date}{self._C_RESET}]" if date else pn
            tree_file.write(f"{indent}['{label}']\n")
        else:
            self.process_generic(current_root, level, tree_file, key='root_pn')

    # ── Claims support  (notebook OPS_library_Lesson_2 § 7.1.7 pattern) ─────

    # OPS fulltext namespace for claim-text elements
    _CLAIMS_NS: dict = {
        'ftxt': 'http://www.epo.org/fulltext',
        'ops':  'http://ops.epo.org',
    }

    # Kind-code preference order: latest/most authoritative first.
    # B2 > B1 > C0 > A1 > A2 > A3 > A8 > A9 (A8/A9 are corrected A documents).
    # '%%' is the OPS wildcard (any kind). Used only as a last resort.
    _KIND_PREFERENCE: list = ['B2', 'B1', 'C0', 'A1', 'A2', 'A3', 'A8', 'A9', '%%']

    # Per-session cache: (number, country, kind) → Claim-1 text or ''
    _claims_cache: dict = {}

    def _extract_first_claim(self, number: str, country: str, kind: str) -> str:
        """Fetch claims XML via OPS published_data and return Claim 1 text in English.

        Two enhancements over the basic § 7.1.7 pattern:

        1. **Latest kind code first** — when the caller supplies kind '%%' (wildcard)
           the method tries kind codes in _KIND_PREFERENCE order (B2 > B1 > C0 > A1 …).
           If a specific kind was given it is tried first, then the rest as fallback.

        2. **English language preference** — the OPS fulltext XML wraps each language
           variant in <ftxt:claims lang="en"> / <ftxt:claims lang="de"> / … elements.
           The method selects lang="en" first; falls back to the first available language.

        Results cached in _claims_cache to avoid re-fetching on repeated button presses.
        """
        cache_key = (number, country, kind)
        if cache_key in self._claims_cache:
            return self._claims_cache[cache_key]

        # Build ordered list of kind codes to try
        if kind and kind != '%%':
            rest = [k for k in self._KIND_PREFERENCE if k != kind and k != '%%']
            kinds_to_try = [kind] + rest + ['%%']
        else:
            kinds_to_try = self._KIND_PREFERENCE  # ends with '%%'

        result = ""

        for try_kind in kinds_to_try:
            try:
                raw_xml = self.client.published_data(
                    reference_type="publication",
                    input=models.Docdb(number, country, try_kind, date=None),
                    endpoint='claims',
                    constituents=[],
                    output_type="raw",
                )
            except Exception:
                continue  # this kind code not available — try next

            try:
                root_el = ET.fromstring(raw_xml)
            except ET.ParseError:
                continue

            # ── Language selection ──────────────────────────────────────────
            # OPS fulltext XML structure:
            #   <ftxt:claims lang="en"> … <ftxt:claim-text>…</ftxt:claim-text> </ftxt:claims>
            #   <ftxt:claims lang="de"> … </ftxt:claims>
            # Prefer lang="en"; fall back to first available block.
            claims_blocks = root_el.findall(".//ftxt:claims", self._CLAIMS_NS)
            preferred_block = None
            fallback_block  = None
            for block in claims_blocks:
                lang = block.get('lang', '').lower()
                if lang == 'en':
                    preferred_block = block
                    break
                if fallback_block is None:
                    fallback_block = block

            selected_block = preferred_block or fallback_block
            if selected_block is None:
                # No <ftxt:claims> wrapper — collect all claim-text elements flat
                claim_elements = root_el.findall(".//ftxt:claim-text", self._CLAIMS_NS)
            else:
                # Use direct children of the <claims> block as candidate claim elements.
                # This handles both <ftxt:claim-text num="N"> and any other child tags.
                # We also include a flat search as fallback if direct children are empty.
                direct_children = list(selected_block)
                claim_elements = direct_children if direct_children else \
                    selected_block.findall(".//ftxt:claim-text", self._CLAIMS_NS)

            # ── Find Claim 1 ────────────────────────────────────────────────
            # Strategy: find the <claims> block, then iterate its direct children.
            # Each direct child <claim-text> (with or without num attribute) represents
            # one claim.  We collect ALL text recursively from the matching element
            # (not just its direct text) using a recursive walk that handles any
            # sub-element structure (nested <claim-text>, <p>, <br/>, etc.).

            def _all_text(element) -> str:
                """Recursively collect all text from element and all descendants."""
                parts = []
                if element.text:
                    parts.append(element.text)
                for child in element:
                    parts.append(_all_text(child))
                    if child.tail:
                        parts.append(child.tail)
                return " ".join(p for p in parts if p.strip())

            # Look for claim elements that have a num="1" attribute first (reliable).
            # Fall back to leading-number detection if num attribute is absent.
            found = None

            # Pass 1: num attribute
            for elem in claim_elements:
                if elem.get('num', '').strip() == '1':
                    found = elem
                    break

            # Pass 2: leading number in text
            if found is None:
                for elem in claim_elements:
                    raw = " ".join(elem.itertext())
                    first_token = raw.split()[0] if raw.split() else ""
                    leading = first_token.rstrip(".)").lstrip("(")
                    try:
                        if int(leading) == 1:
                            found = elem
                            break
                    except ValueError:
                        continue

            if found is not None:
                # Collect all text from this element and every descendant
                raw_text = _all_text(found)
                result = " ".join(raw_text.split()).strip()  # normalise whitespace

                # If the result contains subsequent claims (happens when all claims
                # are nested inside a single <claim-text> wrapper), truncate at the
                # first occurrence of the Claim 2 boundary: " 2." or " 2 ."
                # Patterns: " 2. A...", " 2. The...", also handles "2\n" normalised to "2 ."
                if result:
                    import re as _re
                    # Match " 2." or " 2 ." at a word boundary (the start of Claim 2)
                    boundary = _re.search(r'\s+2\s*\.', result)
                    if boundary:
                        result = result[:boundary.start()].strip()

            if result:
                # Reject result if it is predominantly non-ASCII (i.e. not English)
                non_ascii = sum(1 for c in result if ord(c) > 127)
                if non_ascii > len(result) * 0.2:
                    result = ""
                else:
                    break  # found good EN claim — stop trying further kind codes

        self._claims_cache[cache_key] = result
        return result

    def process_claims(self, current_root, level, tree_file):
        """Fetch and write Claim 1 of a publication beneath its tree node.

        The claim line is written with '. ' dot-space indentation (same depth
        as other supplementary data lines such as priorities or legal events)
        so that divitree_plotter.read_tree_data parses it as a 'Claims' node
        attached to its parent application.  The content is wrapped in the
        sentinel string '[Claim 1] …' so detect_indent_and_type can identify
        it and assign type 'Claims' rather than the generic interleaving_type.

        Args:
            current_root (dict): Node dict containing at least 'root_pn'.
            level (int): Current tree depth (used for indentation).
            tree_file (file): Open file object to write claim text into.
        """
        if not isinstance(current_root, dict):
            return

        pn = current_root.get('root_pn', '').strip()
        if not pn:
            return

        # Parse publication number → (country, number, kind)
        m = re.match(r'^([A-Z]{2})([0-9]+)([A-Z][0-9A-Z]?)?$', pn)
        if not m:
            return
        country = m.group(1)
        number  = m.group(2)
        kind    = m.group(3) or '%%'

        # JP, KR, CN national-phase publications rarely carry English full-text
        # in OPS; their claims are returned in the national language only.
        # WO (PCT) publications do carry EN claims reliably.
        CLAIMS_COUNTRIES = {'EP', 'US', 'WO', 'GB', 'FR', 'DE', 'CA', 'AU'}
        if country not in CLAIMS_COUNTRIES:
            return

        claim1 = self._extract_first_claim(number, country, kind)
        if not claim1:
            return

        # Guarantee no embedded newlines — any \n in the claim text would be
        # misread by read_tree_data as a new tree node on the next line.
        claim1 = " ".join(claim1.split())

        indent = '. ' * (level + 1)
        tree_file.write(f"{indent}[Claim 1] {claim1}\n")

    # ── end Claims support ────────────────────────────────────────────────────

    # ── Concepts support  (XFR conceptChunker + conceptsStrategy pipeline) ───

    def process_concepts(self, current_root, level, tree_file):
        """Fetch Claim 1, extract XFR-style P-operator concepts, write to tree.

        Each concept is written as a separate '. ' indented line with the
        sentinel prefix '[Concept] ' so detect_indent_and_type assigns type
        'Concepts' and build_tooltip renders the P-operator phrase.

        Falls back gracefully (writes nothing) if:
          - the publication is not in CLAIMS_COUNTRIES
          - OPS returns no English claim text
          - claim_concepts module is not importable
          - concept extraction produces no results

        Args:
            current_root (dict): Node dict containing at least 'root_pn'.
            level (int): Current tree depth (used for indentation).
            tree_file (file): Open file object to write concept lines into.
        """
        if not isinstance(current_root, dict):
            return

        pn = current_root.get('root_pn', '').strip()
        if not pn:
            return

        m = re.match(r'^([A-Z]{2})([0-9]+)([A-Z][0-9A-Z]?)?$', pn)
        if not m:
            return
        country = m.group(1)
        number  = m.group(2)
        kind    = m.group(3) or '%%'

        CLAIMS_COUNTRIES = {'EP', 'US', 'WO', 'GB', 'FR', 'DE', 'CA', 'AU'}
        if country not in CLAIMS_COUNTRIES:
            return

        claim1 = self._extract_first_claim(number, country, kind)
        if not claim1:
            return

        claim1 = " ".join(claim1.split())

        try:
            from patent_analysis.claim_concepts import (
                extract_concepts, format_concept_for_display)
            concepts = extract_concepts(claim1, language='EN', max_concepts=8)
        except Exception:
            return

        if not concepts:
            return

        # Accumulate seen concepts within the current branch.
        # Internal keys use the raw P-operator form for reliable dedup;
        # the tree file uses NEAR for human-readable display.
        if not hasattr(self, '_root_concepts'):
            self._root_concepts = set()

        indent = '. ' * (level + 1)
        for concept in concepts:
            key = concept.strip().upper()          # dedup key — raw P form
            display = format_concept_for_display(concept)  # NEAR form for output
            if key not in self._root_concepts:
                tree_file.write(f"{indent}[Concept] +{display}\n")
                self._root_concepts.add(key)
            else:
                tree_file.write(f"{indent}[Concept] {display}\n")

    # ── end Concepts support ──────────────────────────────────────────────────

    # ── Image fetch cache: avoids re-downloading the same patent image ──────
    _image_cache: dict = {}

    # ── Image cache: publication_number → embedded <img> tag ─────────────────
    # Populated lazily by format_image_link(); shared across the entire session
    # so each patent drawing is fetched only once even across multiple tree runs.
    _image_cache: dict = {}

    # ── OPS image path cache: publication_number → OPS image path string ─────
    # Avoids a repeated biblio inquiry for the same number.
    _ops_path_cache: dict = {}

    def _ops_image_path(self, cc: str, number: str, kc: str) -> str | None:
        """
        Query the OPS Published-Images Inquiry endpoint for a patent and return
        the link path of the FirstPageClipping document instance, or None if
        unavailable.  Result is cached so the inquiry is made at most once per
        publication number.
        """
        key = f"{cc}{number}{kc}"
        if key in self._ops_path_cache:
            return self._ops_path_cache[key]
        try:
            # OPS inquiry: published-data/images/{CC}/{NR}/{KC}/inquiry
            # Returns XML listing available document instances and their paths.
            inquiry_path = f"{cc}/{number}/{kc}/inquiry"
            xml_bytes = self.client.image(
                path=inquiry_path,
                range=1,
                document_format='application/xml',
            )
            xml_str = xml_bytes if isinstance(xml_bytes, str) else xml_bytes.decode('utf-8', errors='replace')
            root_el = ET.fromstring(xml_str)
            ns = {'ops': 'http://ops.epo.org'}
            # Prefer FirstPageClipping; fall back to Drawing page 1
            for desc in ('FirstPageClipping', 'Drawing'):
                el = root_el.find(f'.//ops:document-instance[@desc="{desc}"]', ns)
                if el is not None:
                    path = el.get('link')
                    if path:
                        self._ops_path_cache[key] = path
                        return path
        except Exception as e:
            # print(f"OPS inquiry failed for {cc}{number}{kc}: {e}")
            return None
        self._ops_path_cache[key] = None
        return None

    def _fetch_ops_image_b64(self, cc: str, number: str, kc: str) -> tuple[str, str] | None:
        """
        Fetch a full-resolution patent image via OPS and return
        (mime_type, base64_string).  TIFF bytes are converted to PNG so they
        render in all browsers.  Returns None on any failure.
        """
        path = self._ops_image_path(cc, number, kc)
        if not path:
            return None
        try:
            tiff_bytes = self.client.image(
                path=path,
                range=1,
                document_format='application/tiff',
            )
            # Convert TIFF → PNG via Pillow (TIFF is not natively supported by
            # all browsers; PNG always is).
            img = Image.open(io.BytesIO(tiff_bytes))
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            return ('image/png', b64)
        except Exception as e:
            # print(f"OPS image fetch failed for {cc}{number}{kc}: {e}")
            return None

    def _fetch_espacenet_thumb_b64(self, cc: str, number: str) -> tuple[str, str] | None:
        """
        Fetch the low-resolution firstPageClipping thumbnail from the public
        Espacenet JPEG endpoint and return (mime_type, base64_string).
        Used as a fast fallback when the OPS full-res fetch fails.
        """
        url = (
            f"https://worldwide.espacenet.com/espacenetImage.jpg"
            f"?CC={cc}&NR={number}&KC=&flavour=firstPageClipping&FT=E"
        )
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            ct = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
            b64 = base64.b64encode(resp.content).decode('ascii')
            return (ct, b64)
        except Exception as e:
            # print(f"Espacenet thumb fetch failed for {cc}{number}: {e}")
            return None

    def format_image_link(self, display_name: str) -> str | None:
        """
        Return a self-contained HTML <img> tag for the first-page drawing of a
        patent publication, with the image embedded as a base64 data URI.

        Strategy (in order of preference):
          1. OPS Published-Images API  → full-resolution TIFF converted to PNG
          2. Espacenet thumbnail JPEG  → fast, lower resolution fallback
          3. Plain <img src="URL">     → last resort if both fetches fail
                                         (will still be blocked by CORS inside
                                          the TIP iframe, but correct for direct
                                          browser access)

        Embedding base64 data bypasses the CORS restriction that prevents
        tip.epo.org from loading images from worldwide.espacenet.com when the
        sunburst HTML is rendered inside the TIP platform.

        Results are cached per publication number so each image is fetched only
        once regardless of how many times the same number appears in the tree.

        Example input : 'EP3632319B1'
        Example output: '<img src="data:image/png;base64,iVBOR..." width="320">'
        """
        try:
            # ── 1. Parse the publication number ──────────────────────────────
            display_name = display_name.strip().upper()
            match = re.match(r'^([A-Z]{2})(\d+)([A-Z0-9]{0,3})$', display_name)
            if not match:
                return None  # Unparseable → skip silently
            cc, number, kc = match.groups()
            kc_safe = kc or 'A'

            cache_key = f"{cc}{number}{kc}"
            if cache_key in self._image_cache:
                return self._image_cache[cache_key]

            # ── 2. Try OPS full-resolution image ─────────────────────────────
            result = self._fetch_ops_image_b64(cc, number, kc_safe)

            # ── 3. Fall back to Espacenet thumbnail ───────────────────────────
            if result is None:
                result = self._fetch_espacenet_thumb_b64(cc, number)

            # ── 4. Build img tag ──────────────────────────────────────────────
            if result is not None:
                mime, b64 = result
                # OPS/PNG → 400px wide for good readability in popup
                # Espacenet JPEG thumbnail → 320px (it is a small image anyway)
                width = 400 if mime == 'image/png' else 320
                img_tag = f'<img src="data:{mime};base64,{b64}" width="{width}">'
            else:
                # Both fetches failed: embed the remote URL as last resort
                fallback_url = (
                    f"https://worldwide.espacenet.com/espacenetImage.jpg"
                    f"?CC={cc}&NR={number}&KC=&flavour=firstPageClipping&FT=E"
                )
                # print(f"All image fetches failed for {display_name}; using remote URL fallback")
                img_tag = f'<img src="{fallback_url}" width="320">'

            self._image_cache[cache_key] = img_tag
            return img_tag

        except Exception as e:
            print(f"format_image_link failed for {display_name}: {e}")
            return None

    def prefetch_images(self, publication_numbers: list[str], max_workers: int = 6) -> None:
        """
        Pre-fetch and cache images for a list of publication numbers in parallel
        using a thread pool.  Call this before process_tree() when Show_images
        mode is active to minimise total wall-clock time.

        Args:
            publication_numbers: List of publication numbers, e.g.
                                 ['EP3632319B1', 'US10123456B2', ...]
            max_workers:         Thread pool size.  6 is a safe default that
                                 stays well within OPS rate limits.
        """
        if not publication_numbers:
            return
        uncached = [pn for pn in publication_numbers
                    if pn.strip().upper() not in self._image_cache]
        if not uncached:
            return
        print(f"Prefetching {len(uncached)} patent images with {max_workers} threads...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.format_image_link, pn): pn
                for pn in uncached
            }
            done = 0
            for future in as_completed(futures):
                done += 1
                pn = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"  prefetch error for {pn}: {e}")
                if done % 10 == 0 or done == len(uncached):
                    print(f"  {done}/{len(uncached)} images fetched")
        print("Prefetch complete.")

    def process_images(self, current_root, level, tree_file):
        """
        Process and write image data from the current root node to the tree file.

        Args:
            current_root (str or dict): Current root node data or application identifier.
            level (int): The level in the tree for formatting output.
            tree_file (file object): File object for writing the processed data.
        """
        # Use the process_generic method to handle image data processing.
        # It passes 'root_pn' as the key, which contains the publication data in the current_root node.
        # Calls the generic processing method with a formatter specific to image data
        self.process_generic(current_root, level, tree_file, key='root_pn', formatter=self.format_image_link)
        
    def format_legal_events(self, level, event_data, country_code=""):
        """
        Format and colorize legal event data into a readable string.
        Uses colorize_legal_event() for consistent semantic highlighting.
        
        Args:
            level: Indentation level
            event_data: Event data (dict or list)
            country_code: Two-letter country code (e.g., 'EP', 'US', 'WO')        
        """
        formatted_events = []
        indent = ". " * (level + 1)

        # --- Normalize input shape ---
        if isinstance(event_data, dict) and "legal_events" in event_data:
            events = event_data["legal_events"]
        elif isinstance(event_data, list):
            events = event_data
        else:
            formatted_events.append(f"{indent}{event_data}")
            return "\n".join(formatted_events)

        # --- Format and colorize each event ---
        for event in events:
            code = (event.get("code") or event.get("legal_event_code") or "").strip()
            desc = (event.get("desc") or event.get("legal_event_desc") or "").strip()
            date = event.get("date") or event.get("event_date") or ""
            countries = event.get("countries") or []
            cc_tag = f" <{','.join(countries)}>" if countries else ""
            colored_line = self.colorize_code_and_date(code, desc, date, country_code)
            formatted_events.append(f"{indent}{colored_line}{cc_tag}")

        # --- Fallback if no events ---
        if not formatted_events:
            formatted_events.append(f"{indent}No legal events available.")

        return "\n".join(formatted_events)
        
    def process_legal_events(self, current_root, level, tree_file):
        """
        Process and write legal event data from the current root node to the tree file.

        Args:
            current_root (str or dict): Current root node data or application identifier.
            level (int): The current depth in the tree structure for indentation.
            tree_file (file object): File object for the tree file where data will be written.
        """
        # Extract country code — validate alphabetic; fall back to root_an for
        # numbers like '11201908897PA' that carry no alphabetic country prefix.
        country_code = ""
        if isinstance(current_root, dict):
            pub_number = (current_root.get('root_pn') or '').strip()
            cc_candidate = pub_number[:2].upper() if len(pub_number) >= 2 else ''
            if cc_candidate.isalpha():
                country_code = cc_candidate
            else:
                app_number = (current_root.get('root_an') or '').strip()
                cc_from_an = app_number[:2].upper() if len(app_number) >= 2 else ''
                if cc_from_an.isalpha():
                    country_code = cc_from_an
                
        # Calls the generic processing method with a formatter specific to legal events
        # ✅ Use lambda to pass country_code to format_legal_events
        self.process_generic(
            current_root, 
            level, 
            tree_file, 
            key='root_evnt', 
            formatter=lambda data: self.format_legal_events(level, data, country_code)
        )

    def colorize_code_and_date(self, code, text, dt, country_code=""):
        """
        Return  code: text [date]  with semantic ANSI colours.
        Format code with country-code prefix in blue brackets.

        Optimisations vs. previous version
        ------------------------------------
        * COLORS dict and ANSI constants are now class-level attributes
          (_EXACT_COLORS, _SUBSTR_COLOR_PATTERNS, _C_ORANGE, …) — they are
          built exactly once when the class is defined, not on every call.
        * Colour lookup uses an O(1) dict check first; only falls through to
          the compiled-regex substring patterns when no exact match is found,
          instead of iterating all ~60 keys linearly.
        """
        code = code or ""   # guard against None

        ORANGE    = self._C_ORANGE
        BLUE      = self._C_BLUE
        RESET     = self._C_RESET
        HIGHLIGHT = self._C_HIGHLIGHT

        colored_code = code
        color = ""

        # --- 1️⃣ O(1) exact match ---
        color = self._EXACT_COLORS.get(code, "")
        
        # --- 1️b Compound-code match: e.g. "EPIDOS IDOP" contains known token "IDOP" ---
        # Handles space-separated compound codes like "EPIDOS IDOP", "EPIDOS REFN", etc.
        if not color and " " in code:
            for token in code.split():
                token_color = self._EXACT_COLORS.get(token, "")
                if token_color:
                    color = token_color
                    break

        # --- 2️⃣ Compiled-regex substring match (only if no exact hit) ---
        if not color:
            for pattern, c in self._SUBSTR_COLOR_PATTERNS:
                if pattern.search(code):
                    color = c
                    break

        # --- 3️⃣ Generic fallback: ADW / EXP / REV / LAPS anywhere in code ---
        if not color and code not in self._EXP_EXCLUSIONS and self._RE_ADW_GENERIC.search(code):
            color = "\033[91m"  # bright red

        # --- 4️⃣ Special case: RFEE / PFEE + renewal year near max (18–20) ---
        if code in ("PFEE", "RFEE") and text:
            match = re.search(r"\b(\d{1,2})\b", text)
            if match:
                year_num = int(match.group(1))
                if 18 <= year_num <= 20:
                    color = HIGHLIGHT
                    colored_code = f"{color}{code}{RESET}"
                    text = text.replace(match.group(1), f"{HIGHLIGHT}{match.group(1)}{RESET}")
                    dt = f"🟠{dt}" if dt else "🟠"
                    return f"{colored_code}: {text} [{ORANGE}{dt}{RESET}]" if text else f"{colored_code} [{ORANGE}{dt}{RESET}]"

        # --- 5️⃣ Apply colour to code (and optionally to its occurrence in text) ---
        if color:
            colored_code = f"{color}{code}{RESET}"
            if text and code in text:
                text = text.replace(code, colored_code, 1)

        # Country-code prefix in blue brackets
        country_prefix = f"[{BLUE}{country_code}{RESET}] " if country_code else ""

        # --- 6️⃣ Final rendering ---
        if dt:
            return f"{country_prefix}{colored_code}: {text} [{ORANGE}{dt}{RESET}]" if text else f"{country_prefix}{colored_code} [{ORANGE}{dt}{RESET}]"
        elif text:
            return f"{country_prefix}{colored_code}: {text}" if text else colored_code
        else:
            return f"{country_prefix}{colored_code}"  # never drop codes like '10' or 'EPIDOSDEXP2'
        
    def extract_procedural_codes_with_dates(self, doc, country_code=""):
        """Collect procedural codes/descriptions along with reg:date timestamps."""
        
        # ✅ Guard against list input
        if isinstance(doc, list):
            codes = []
            for sub in doc:
                if isinstance(sub, dict):
                    codes.extend(self.extract_procedural_codes_with_dates(sub, country_code))
            return codes
        if not isinstance(doc, dict):
            return []  # ignore anything else
            
        codes_with_dates = []  

        def _safe_list(obj):
            """Ensure obj is always returned as a list of dicts or strings."""
            if obj is None:
                return []
            if isinstance(obj, list):
                return obj
            return [obj]

        # ✅ Handle case where OPS sends a list of register-documents
        if isinstance(doc, list):
            merged = []
            for sub in doc:
                if isinstance(sub, dict):
                    merged.extend(self.extract_procedural_codes_with_dates(sub, country_code))
            return merged

        if not isinstance(doc, dict):
            return codes_with_dates
            
        def normalize_dates(raw):
            """Return a list of date strings from various possible formats."""
            dates = []
            if raw is None:
                return dates
            if isinstance(raw, str):
                dates.append(raw)
            elif isinstance(raw, dict):
                if 'reg:date' in raw:
                    dates.append(raw['reg:date'])
                else:
                    # fallback: collect all string values
                    for v in raw.values():
                        if isinstance(v, str):
                            dates.append(v)
            elif isinstance(raw, list):
                for r in raw:
                    dates.extend(normalize_dates(r))
            return dates
            
        def add_code_text_date(code, text, raw_date):
            """Add code:text with date(s) in square brackets."""
            # Normalize text
            if isinstance(text, dict):
                text = text.get('#text', '')
            elif isinstance(text, list):
                texts = []
                for t in text:
                    if isinstance(t, dict):
                        texts.append(t.get('#text', ''))
                    elif isinstance(t, str):
                        texts.append(t)
                text = "; ".join(texts)
            text = (text or "").strip()

            dates = normalize_dates(raw_date)
            if not dates:
                dates = [""]

            for dt in dates:
                # label = f"{code}: {text} [🟠{dt}]" if text else f"{code} [🟠{dt}]"
                # ORANGE = "\033[38;5;214m"
                # RESET = "\033[0m"
                # label = f"{code}: {text} [{ORANGE}{dt}{RESET}]" if text else f"{code} [{ORANGE}{dt}{RESET}]"                      
                label = self.colorize_code_and_date(code, text, dt)
                codes_with_dates.append((dt, label))
                
        # === Procedural steps ===
        for step in _safe_list(doc.get('reg:procedural-data', {}).get('reg:procedural-step')):
            if isinstance(step, dict):
                add_code_text_date(
                    step.get('reg:procedural-step-code'),
                    step.get('reg:procedural-step-text'),
                    step.get('reg:procedural-step-date') or step.get('reg:date')
                )

        # === Events ===
        for ev in _safe_list(doc.get('reg:events-data')):
            if isinstance(ev, dict):
                ev = ev.get('reg:dossier-event', ev)
                if isinstance(ev, dict):
                    add_code_text_date(
                        ev.get('reg:event-code'),
                        ev.get('reg:event-text'),
                        ev.get('reg:event-date') or ev.get('reg:date')
                    )

        # === EP statuses ===
        for status in _safe_list(doc.get('reg:ep-patent-statuses', {}).get('reg:ep-patent-status')):
            if isinstance(status, dict):
                add_code_text_date(
                    status.get('@status-code'),
                    status.get('#text'),
                    status.get('@change-date') or status.get('reg:date')
                )

        # === Bibliographic-data ===
        biblio = doc.get('reg:bibliographic-data', {})
        if isinstance(biblio, dict):
            for claim in _safe_list(biblio.get('reg:priority-claims', {}).get('reg:priority-claim')):
                if isinstance(claim, dict):
                    add_code_text_date(
                        f"PRIORITY-{claim.get('reg:country')}{claim.get('reg:doc-number')}",
                        claim.get('reg:date'),
                        claim.get('reg:date')
                    )

            # ✅ Compact DESIGNATION states into one line
            states = _safe_list(
                biblio.get('reg:designation-of-states', {})
                      .get('reg:designation-pct', {})
                      .get('reg:regional', {})
                      .get('reg:country')
            )
            if states:
                # if all(isinstance(s, str) for s in states):
                #     state_line = ", ".join(states)
                # else:
                #     state_line = ", ".join([s if isinstance(s, str) else str(s) for s in states])
                state_line = ", ".join(s if isinstance(s, str) else str(s) for s in states)
                add_code_text_date("DESIGNATION", state_line, None)
                
                # # ✅ Pass compact designation states forward for tooltip rendering
                # node["designation_states"] = state_line
    
        # === Opposition-data ===
        opp = doc.get('reg:opposition-data', {})
        if isinstance(opp, dict):
            add_code_text_date("OPPOSITION", "opposition-not-filed",
                               opp.get('reg:opposition-not-filed', {}).get('reg:date'))

        # === Unitary-patent (UPP) ===
        upp = doc.get('reg:unitary-patent', {})
        if isinstance(upp, dict):
            # Collect statuses
            # statuses = []
            for status in _safe_list(upp.get('reg:unitary-patent-statuses', {}).get('reg:unitary-patent-status')):
                add_code_text_date(
                    "UPP-STATUS",
                    f"{status.get('@status-code', '')} {status.get('#text', '')}".strip(),
                    status.get('reg:date')
                )                
            #     if isinstance(status, dict):
            #         statuses.append(f"{status.get('@status-code')} {status.get('#text','')}".strip())
            # if statuses:
            #     add_code_text_date("UPP-STATUS", "; ".join(statuses), None)

            for step in _safe_list(upp.get('reg:procedural-data', {}).get('reg:procedural-step')):
                if isinstance(step, dict):
                    add_code_text_date(
                        step.get('reg:procedural-step-code'),
                        step.get('reg:procedural-step-text'),
                        step.get('reg:procedural-step-date') or step.get('reg:date')
                    )

            for ev in _safe_list(upp.get('reg:events-data')):
                if isinstance(ev, dict):
                    ev = ev.get('reg:dossier-event', ev)
                    if isinstance(ev, dict):
                        add_code_text_date(
                            ev.get('reg:event-code'),
                            ev.get('reg:event-text'),
                            ev.get('reg:event-date') or ev.get('reg:date')
                        )

        # ✅ Sort by date (empty dates go last)
        codes_with_dates.sort(key=lambda x: (x[0] == "", x[0]))
        return [label for _, label in codes_with_dates]

    def _fetch_register_data(self, pub_number: str,
                              range_begin: int = 1,
                              range_end: int = 10,
                              output_type: Optional[str] = 'dataframe',
                              constituents: Optional[list] = None) -> Optional[pd.DataFrame]:
        """
        Fetch register search data (procedural codes/events) from EPO OPS using a CQL query,
        flatten reg:register-document entries into a DataFrame for further processing.
        """

        # Use passed constituents or default list
        if constituents is None:
            constituents = ["biblio", "events", "procedural-steps", "upp"]

        # Parse publication number into country, doc_number
        m = re.match(r"^([A-Z]{2})(\d+)([A-Z0-9]{0,2})$", pub_number)
        if not m:
            print(f"Invalid publication number format: {pub_number}")
            return None
        country, doc_number, _ = m.groups()

        try:
            register_df = self.client.register(
                "publication",
                input=models.Epodoc(country + doc_number),
                constituents=constituents,
                output_type="dataframe"
            )

            if register_df is None or register_df.empty:
                print(f"No register data found for {country}{doc_number}")
                return None
            
            proc_col = 'ops:world-patent-data|ops:register-search|reg:register-documents'
            if proc_col not in register_df.columns or pd.isnull(register_df[proc_col].iloc[0]):
                print(f"❌ Column '{proc_col}' not found for {pub_number}")
                return None

            flattened_rows = []
            for i, cell in register_df[proc_col].items():
                docs = []
                
                if isinstance(cell, dict):
                    docs = cell.get('reg:register-document', [])
                if isinstance(docs, dict):
                    docs = [docs]
                elif not isinstance(docs, list):
                    docs = []
                
                for doc in docs:
                    flattened_rows.append({
                        'original_index': i,
                        'root_query': register_df.get(
                            'ops:world-patent-data|ops:register-search|ops:query',
                            pd.Series([None] * len(register_df))
                        ).iloc[i],
                        'reg:register-document': doc
                    })

            if not flattened_rows:
                return None

            flat_df = pd.DataFrame(flattened_rows)
            pd.set_option('display.max_colwidth', 20000)
            return flat_df

        except Exception as e:
            # 404 = no Register entry for this publication (expected for JP/KR/CN)
            # Suppress silently; only warn on unexpected errors.
            msg = str(e)
            if '404' not in msg and 'Not Found' not in msg:
                print(f"  ⚠️  Register fetch failed for {pub_number}: {type(e).__name__}: {e}")
            return None

    def _store_procedural_step(self, step=None, parent_id=None, level=0, tree_file=None):
        """Store a procedural step safely (replaces missing method)."""
        if step is None or parent_id is None or tree_file is None:
            return
        # Flatten dict or list into a readable string
        if isinstance(step, dict):
            step_text = "; ".join(f"{k}: {v}" for k, v in step.items())
        elif isinstance(step, list):
            step_text = "; ".join(str(s) for s in step)
        else:
            step_text = str(step)
        tree_file.write(f"{'. ' * (level + 1)}PROC-STEP: {step_text}\n")

    def retrieve_procedural_codes(self, current_root, level, tree_file):
        """
        Robust parser for register data:
        - Recurses dicts/lists safely (no .get on lists)
        - Extracts labels via extract_procedural_codes_with_dates
        - Writes scalar 'code lists' (e.g., UPP states) via _store_procedural_step
        - Deduplicates everything
        """
        # --- Guards ---
        if not isinstance(current_root, dict):
            print(f"Invalid current_root: {current_root}")
            return
        pub_number = (current_root.get('root_pn') or '').strip()
        if len(pub_number) < 5:
            print(f"Invalid pub_number: {current_root}")
            return
            
        # Extract country code — validate alphabetic; fall back to root_an for
        # pub_numbers that start with digits (e.g. SG '11201908897PA').
        cc_candidate = pub_number[:2].upper() if len(pub_number) >= 2 else ''
        if cc_candidate.isalpha():
            country_code = cc_candidate
        else:
            app_number = (current_root.get('root_an') or '') if isinstance(current_root, dict) else ''
            cc_from_an = app_number[:2].upper() if len(app_number) >= 2 else ''
            country_code = cc_from_an if cc_from_an.isalpha() else ''
    
        # What to fetch based on initFlag
        flag_map = {
            'Show_biblio': ["biblio"],
            'Show_events': ["events"],
            'Show_procedural_steps': ["procedural-steps"],
            'Show_upp': ["upp"]
        }
        constituents = (
            flag_map.get(self.initFlag, ["procedural-codes"])
            if self.initFlag != 'Show_procedural_codes' else None
        )

        # --- Fetch ---
        register_df = self._fetch_register_data(pub_number, output_type='dataframe', constituents=constituents)
        if register_df is None or register_df.empty:
            # print(f"No procedural codes found for {pub_number}")
            return
        reg_col = 'reg:register-document'
        if reg_col not in register_df.columns:
            print(f"'reg:register-document' missing for {pub_number}")
            return

        # --- Dedup sets ---
        seen_labels = set()           # for lines from extract_procedural_codes_with_dates
        seen_steps = set()            # for scalar PROC-STEPs: tuples of (parent_id, step_text)

        # Keys whose values often are scalar code lists we want to print (tweak as you encounter more)
        scalar_code_keys = {
            "upp:state",
            "upp:states",
            "upp:participating-member-state",
            "reg:designation-state-code",
            "reg:state-code",
            "designation-state-code",
            "state",
            "country",
            "country-code",
        }

        def write_scalar_step(step_value, parent_id):
            """Write a scalar 'code' once per parent_id."""
            # Coerce to clean string
            s = (str(step_value) if step_value is not None else "").strip()
            if not s:
                return
            key = (parent_id, s)
            if key in seen_steps:
                return
            # re-use your existing helper
            self._store_procedural_step(step=s, parent_id=parent_id, level=level, tree_file=tree_file)
            seen_steps.add(key)

        def _process_entry(entry, parent_id, ctx_key=None, country_code=""):
            """Recursive, context-aware descent."""
            # Lists: recurse into items with same context
            if isinstance(entry, list):
                for item in entry:
                    _process_entry(item, parent_id, ctx_key, country_code=country_code)
                return

            # Scalars: only write when context key indicates a code list
            if not isinstance(entry, dict):
                if ctx_key in scalar_code_keys:
                    write_scalar_step(entry, parent_id)
                return

            # Try to extract labeled procedural/biblio/UPP lines
            try:
                labels = self.extract_procedural_codes_with_dates(entry, country_code) or []
                for lbl in labels:
                    if lbl not in seen_labels:
                        tree_file.write(f"{'. ' * (level + 1)}{lbl}\n")
                        seen_labels.add(lbl)
            except Exception:
                # Some sub-objects (e.g. where 'reg:procedural-step' is a list)
                # can trip code that expects dicts; skip quietly and keep recursing.
                pass

            # If the dict directly carries scalar code lists, write them here too
            for k in scalar_code_keys:
                if k in entry:
                    v = entry[k]
                    if isinstance(v, list):
                        for s in v:
                            write_scalar_step(s, parent_id)
                    else:
                        write_scalar_step(v, parent_id)

            # Recurse into all nested dict/list values, passing the key as context
            for k, v in entry.items():
                if isinstance(v, (dict, list)):
                    _process_entry(v, parent_id, ctx_key=k, country_code=country_code)

        # --- Walk each register-document payload ---
        for raw in register_df[reg_col].tolist():
            # Try to decode JSON strings
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    # leave as-is; _process_entry will ignore non-structured scalars
                    pass

            try:
                _process_entry(raw, parent_id=pub_number, ctx_key=None, country_code=country_code)
            except Exception as e:
                # Keep processing other entries even if one branch is malformed
                print(f"⚠️ Skipping malformed register entry for {pub_number}: {e}")
            
    def process_procedural_codes(self, current_root, level, tree_file):
        """
        Process and write procedural codes from the current root node to the tree file.

        Args:
            current_root (dict): The document number or dictionary containing document number information.
            level (int, optional): The level in the tree for formatting output. Defaults to None.
            tree_file (file object, optional): File object for writing the processed data. Defaults to None.
        """
        # Call the retrieve_biblio_data method with specific parameters for processing citations
        
        self.retrieve_procedural_codes(
            current_root=current_root,  # Document number or dictionary with document number information
            level=level,                # Level for formatting output
            tree_file=tree_file        # File object to write the formatted data
        )

    def retrieve_biblio_data(self, current_root, level, tree_file, data_type, extraction_func=None, output_format_func=None):
        """
        A unified method to process different types of data (e.g., citations, classifications, parties, images) 
        from the current root node and write it to a tree file.

        Args:
            - current_root (dict): The document number or dictionary containing document number information.
            - level (int): The level in the tree for formatting output.
            - tree_file (File): The file object to write the processed data.
            - data_type (str): The type of data to process ('citations', 'classifications', 'parties', 'images').
            - extraction_func (callable): A function to extract specific data from the retrieved bibliographic information.
            - output_format_func (callable): A function to format the extracted data before writing to the file.
        """
        
        # Step 1: Check if the current_root is valid and contains the expected key 'root_pn'
        if not isinstance(current_root, dict) or 'root_pn' not in current_root:
            print(f"Invalid 'current_root': {current_root}")
            return

        # Extract the publication number from the current_root
        pub_number = current_root.get('root_pn', '').strip()
        # print("pub_number:", pub_number)
        
        if not pub_number or len(pub_number) < 5:
            print("Invalid pub_number format. Skipping.")
            return

        # Step 2: Extract parts using regex
        match = re.match(r'^([A-Z]{2})(\d+)([A-Z][0-9]?)?$', pub_number)
        if not match:
            print(f"Unable to parse pub_number: {pub_number}")
            return

        country, doc_number, kind = match.groups()
        kind = kind if kind else 'A'  # Default if missing

        # print(f"Parsed: country={country}, number={doc_number}, kind={kind}")

        # Step 3: Determine endpoint and output type based on the data_type
        endpoint = "biblio" if data_type != 'images' else "images"
        output_type = "Dataframe" if data_type != 'images' else "Raw"
        
        # Fetch with retry loop — errors must never propagate and truncate the tree.
        biblio_data = None
        max_retries    = 5
        backoff_factor = 0.1
        for attempt in range(1, max_retries + 1):
            try:
                biblio_data = self.client.published_data(
                    reference_type="publication",
                    input=models.Docdb(doc_number, country, kind),
                    endpoint=endpoint,
                    constituents=[],
                    output_type=output_type,
                )
                break  # success
            except requests.exceptions.RequestException as e:
                response = getattr(e, 'response', None)
                status   = getattr(response, 'status_code', None)
                if status == 404:
                    return  # document not found — skip gracefully, no retry
                print(f"  ⚠️ Request error {attempt}/{max_retries} for {pub_number}: {e}")
            except Exception as e:
                print(f"  ❌ Unexpected error {attempt}/{max_retries} for {pub_number}: {e}")
                break  # non-network error — no point retrying
            time.sleep(backoff_factor * attempt)

        # display(biblio_data)
        
        # If biblio_data is not retrieved, skip the rest of the process
        if biblio_data is None:
            return
        
        # Extract and process the relevant data        
        extracted_data = extraction_func(biblio_data) if extraction_func else None
        # print("extracted_data:", extracted_data)
        
        # Format the output and write to file if data exists
        if extracted_data:
            formatted_output = output_format_func(extracted_data) if output_format_func else str(extracted_data)
            # print("formatted_output:", formatted_output)
            if tree_file:
                tree_file.write(f'{". " * (level + 1)}{formatted_output}\n')

    def extract_citations(self, biblio_df):
        """
        Extracts citation information from a bibliographic DataFrame.

        Args:
            biblio_df (pandas.DataFrame): DataFrame containing bibliographic data.

        Returns:
            list: A list of citations extracted from the DataFrame.
        """
        citations_list = []
        
        # Check if the DataFrame contains the expected column
        if 'bibliographic-data|references-cited' in biblio_df.columns:
            # Iterate over each entry in the 'bibliographic-data|references-cited' column
            for citations_data in biblio_df['bibliographic-data|references-cited']:
                # Check if the entry is a dictionary and contains 'citation'
                if isinstance(citations_data, dict) and 'citation' in citations_data:
                    citations = citations_data['citation']
                    # If 'citation' is a list, extend citations_list with its elements
                    if isinstance(citations, list):
                        citations_list.extend(citations)                        
                    # If 'citation' is a dictionary, append it to citations_list
                    elif isinstance(citations, dict):
                        citations_list.append(citations)
        return citations_list

    def format_citations(self, citations_list):
        """
        Format a list of citations into a readable string.
        Citations not seen in previously processed nodes are prefixed with '+'
        so the display layer can highlight them.

        Args:
            citations_list (list): List of citations to format, each citation is expected to be a dictionary.

        Returns:
            str: A formatted string representation of the citations.
        """
        # Convert citation list into DataFrame
        citations_df = pd.json_normalize(citations_list)

        # Filter valid patent or XP references
        citation_filter = pd.Series([False] * len(citations_df))
        if 'patcit.@dnum-type' in citations_df.columns:
            citation_filter |= citations_df['patcit.@dnum-type'] == 'publication number'
        if 'nplcit.@num' in citations_df.columns:
            citation_filter |= citations_df['nplcit.@num'].notna()

        citations_df = citations_df[citation_filter].copy()

        # Functions to extract values
        def extract_doc_number(doc_ids):
            if isinstance(doc_ids, list):
                for doc in doc_ids:
                    if doc.get('@document-id-type') in ['epodoc', 'docdb']:
                        return doc.get('doc-number')
            elif isinstance(doc_ids, dict):
                if doc_ids.get('@document-id-type') in ['epodoc', 'docdb']:
                    return doc_ids.get('doc-number')
            return None

        def extract_xp_number(npl_text):
            if isinstance(npl_text, str) and 'XP' in npl_text:
                # Take the first whitespace-separated token after "XP",
                # then strip trailing punctuation (comma/period/semicolon
                # /colon/bracket) that NPL records often glue onto the
                # XP identifier — e.g. "XP000539528," or "XP000539528;".
                token = 'XP' + npl_text.split('XP')[-1].split()[0]
                return token.rstrip(',.;:)]}\u00a0 ').strip()
            return None
    
        # Extract values
        citations_df['extracted_doc_number'] = citations_df.get('patcit.document-id', None).apply(extract_doc_number) if 'patcit.document-id' in citations_df else None
        citations_df['extracted_xp_number'] = citations_df.get('nplcit.text', None).apply(extract_xp_number) if 'nplcit.text' in citations_df else None

        # Combine and normalize
        citations_df['formatted_citation'] = citations_df['extracted_doc_number'].combine_first(citations_df['extracted_xp_number'])
        citations_df['formatted_citation'] = citations_df['formatted_citation'].astype(str).str.upper().str.strip()

        # Deduplicate while keeping order and remove 'NONE'
        citations_df = citations_df.loc[citations_df['formatted_citation'].dropna().duplicated(keep='first') == False]
        citation_list = [
            c for c in citations_df['formatted_citation'].dropna().tolist()
            if c.upper() not in {"NONE", "NULL", "N/A", "UNKNOWN", ""}
        ]

        # Accumulate seen citations within the current branch.
        # The branch-level reset in process_tree ensures each top-level branch
        # starts fresh, so '+' marks items new within this branch only.
        if not hasattr(self, '_root_citations'):
            self._root_citations = set()

        annotated = []
        for cit in citation_list:
            if cit not in self._root_citations:
                annotated.append(f'+{cit}')
                self._root_citations.add(cit)
            else:
                annotated.append(cit)

        return str(annotated)

    def process_citations(self, current_root, level=None, tree_file=None):
        """
        Process and write citation data from the current root node to the tree file.

        Args:
            current_root (dict): The document number or dictionary containing document number information.
            level (int, optional): The level in the tree for formatting output. Defaults to None.
            tree_file (file object, optional): File object for writing the processed data. Defaults to None.
        """
        # Call the retrieve_biblio_data method with specific parameters for processing citations
        self.retrieve_biblio_data(
            current_root=current_root,  # Document number or dictionary with document number information
            level=level,                # Level for formatting output
            tree_file=tree_file,        # File object to write the formatted data
            data_type='citations',      # Type of data to process
            extraction_func=self.extract_citations,  # Function to extract citation data from the retrieved bibliographic information
            output_format_func=self.format_citations  # Function to format the extracted citation data before writing to the file
        )

    def extract_classifications(self, biblio_df):
        """
        Extracts and processes patent classification data from a DataFrame containing bibliographic information.

        Args:
            biblio_df (pd.DataFrame): DataFrame containing bibliographic data, including patent classifications.

        Returns:
            list: A list of unique patent classification strings.
        """
        classification_data = []
        seen = set()
        
        # Check if 'bibliographic-data|patent-classifications' column exists in the DataFrame
        if 'bibliographic-data|patent-classifications' in biblio_df.columns:
            # Iterate through each row of the DataFrame
            for index, row in biblio_df.iterrows():
                classification_info = row['bibliographic-data|patent-classifications']

                # Ensure the classification information is a dictionary and contains 'patent-classification'
                if isinstance(classification_info, dict) and 'patent-classification' in classification_info:
                    classifications = classification_info['patent-classification']

                    # If classifications are in list format, process each classification
                    if isinstance(classifications, list):
                        for classification in classifications:
                            section    = classification.get('section',    '') or ''
                            _class     = classification.get('class',      '') or ''
                            subclass   = classification.get('subclass',   '') or ''
                            main_group = classification.get('main-group', '') or ''
                            subgroup   = classification.get('subgroup',   '') or ''

                            # Skip entries where any field is missing or 'Unknown'
                            parts = [section, _class, subclass, main_group, subgroup]
                            if any(p.lower() == 'unknown' or p == '' for p in parts):
                                continue

                            cpc_item = f"{section}{_class}{subclass}{main_group}/{subgroup}"

                            if cpc_item not in seen:
                                seen.add(cpc_item)
                                classification_data.append(cpc_item)

        return classification_data
    
    def format_classifications(self, classification_data):
        """
        Formats classification list, marking entries new relative to the root
        publication with a '+' prefix so they can be highlighted at display time.
        Plain text is written to the tree file; the display layer converts to HTML.

        Args:
            classification_data (list): A list of patent classification strings.

        Returns:
            str: Formatted string with new classifications prefixed by '+'.
        """
        # Accumulate seen classifications within the current branch.
        if not hasattr(self, '_root_classifications'):
            self._root_classifications = set()

        annotated = []
        for cpc in classification_data:
            if cpc not in self._root_classifications:
                annotated.append(f'+{cpc}')
                self._root_classifications.add(cpc)
            else:
                annotated.append(cpc)

        return str(annotated)

    def process_classifications(self, current_root, level=None, tree_file=None):
        """
        Process and write classification data from the current root node to the tree file.

        Args:
            current_root (dict): The document number or dictionary containing document number information.
            level (int, optional): The level in the tree for formatting output. Defaults to None.
            tree_file (file object, optional): The file object to write the processed data. Defaults to None.
        """
        # Debugging output
        # print(f"Processing classifications for {current_root}")
        
        # Retrieve and process bibliographic data
        self.retrieve_biblio_data(
            current_root,                  # The root data (document number or dictionary)
            level,                         # The indentation level for output formatting
            tree_file,                     # The file object to write the output
            'classifications',            # Type of data to retrieve (classifications)
            self.extract_classifications, # Function to extract classification data
            self.format_classifications   # Function to format the extracted classification data
        )

    def extract_party_names(self, biblio_df):
        """
        Extract names of parties (applicants, inventors, representatives) from the bibliographic DataFrame.

        Args:
            biblio_df (pd.DataFrame): DataFrame containing bibliographic data, including party information.

        Returns:
            list: A list of party names extracted from the DataFrame.
        """
        # Match a trailing two- or three-letter country code in square brackets,
        # e.g. " [JP]" or " [USA]". Used to strip these out of party names so
        # that they no longer appear in the textual tree or tooltips, and so
        # that "KASHIWAGI YOSHIICHIRO [JP]" and "KASHIWAGI YOSHIICHIRO" no
        # longer deduplicate as two distinct people.
        _country_tag_re = re.compile(r'\s*\[[A-Z]{2,3}\]\s*$')

        def _strip_country_tag(name):
            return _country_tag_re.sub('', name).strip()

        def extract_names(party_list, role):
            """
            Extract names of parties based on their role.

            Args:
                party_list (list): List of party dictionaries.
                role (str): Role of the party (e.g., 'applicant', 'inventor', 'representative').

            Returns:
                list: A list of party names.
            """
            return [
                _strip_country_tag(
                    party.get(f'{role}-name', {}).get('name', '').replace('\u2002', ' ').strip(', ')
                )
                for party in party_list if isinstance(party, dict) and f'{role}-name' in party
            ]

        def normalize_name(name):
            # Remove commas, excessive spaces, and make all-uppercase for deduplication.
            # Country tags are already stripped at extraction time.
            return ' '.join(name.replace(',', ' ').upper().split())

        def is_valid(name):
            # Keep names that have at least two words and no lone comma.
            # The previous rule that accepted any name containing '[' was a
            # proxy for "looks like an inventor record"; with country tags now
            # removed at extraction time, this proxy is no longer needed.
            return (',' not in name and len(name.split()) >= 2)
        
        if biblio_df is None:
            return []

        # Extract relevant rows from the DataFrame
        indexes_to_iterate = list(biblio_df.head(5).index) + list(biblio_df.tail(5).index)
        indexes_to_iterate = list(set(indexes_to_iterate))  # Remove duplicates if any

        seen = set()
        unique_results = []
        
        for index in indexes_to_iterate:            
            try:
                row = biblio_df.loc[index]
            
                # Application number (fallback to UNKNOWN)
                app_number = (
                    row.get('root_ap') or
                    row.get('application-number') or
                    row.get('publication-reference|document-id|doc-number') or
                    "UNKNOWN"
                )
                parties_info = row.get('bibliographic-data|parties', {})

                if not isinstance(parties_info, dict):
                    # Log and skip — do not raise, which would abort the tree backbone
                    print(f"  ⚠️ Unexpected parties_info type at index {index}: {type(parties_info)}")
                    continue

                applicants = parties_info.get('applicants', {}).get('applicant', [])
                inventors = parties_info.get('inventors', {}).get('inventor', [])
                representatives = parties_info.get('representatives', {}).get('representative', [])

                all_names = extract_names(applicants, 'applicant') + \
                            extract_names(inventors, 'inventor') + \
                            extract_names(representatives, 'representative')

                for name in all_names:
                    name = name.strip()
                    norm = normalize_name(name)                    
                    if is_valid(name) and norm not in seen:
                        seen.add(norm)
                        unique_results.append(name)                

            except Exception as e:
                raw_row = biblio_df.loc[index].to_dict()
                print(f"\n⚠️ Error processing row at index {index} (application {app_number}): {e}")
                print("↪️  Raw row content:")
                print(json.dumps(raw_row, indent=2, ensure_ascii=False))

        return unique_results
        
    def process_parties(self, current_root, level, tree_file):
        """
        Process and write party data (applicants, inventors, representatives) from the current root node to the tree file.

        Args:
            current_root (str or dict): Current root node data or application identifier.
            level (int): The level in the tree for formatting output.
            tree_file (file object): File object for writing the processed data.
        """
        def format_party_output(data):
            """
            Format the extracted party names into a comma-separated string.
            New names (not seen in previously processed nodes) are prefixed with '+'
            so the display layer can highlight them.

            Args:
                data (list): List of party names.

            Returns:
                str: A comma-separated string of party names, new ones prefixed with '+'.
            """
            if not hasattr(self, '_root_parties'):
                self._root_parties = set()

            annotated = []
            for name in data:
                key = ' '.join(name.replace(',', ' ').upper().split())
                if key not in self._root_parties:
                    annotated.append(f'+{name}')
                    self._root_parties.add(key)
                else:
                    annotated.append(name)
            return ', '.join(annotated)

        self.retrieve_biblio_data(
            current_root, 
            level, 
            tree_file, 
            'biblio', 
            extraction_func=self.extract_party_names, 
            output_format_func=format_party_output
        )
    