import pandas as pd
from typing import Dict, Any
import re
from datetime import datetime
from pandas import to_datetime

# Helper function to safely extract first orap value (handles both list and string)
def get_first_orap(orap_value):
    """
    Extract the first orap value, handling both list and string formats.
    
    Args:
    - orap_value: Can be a list, string, or None
    
    Returns:
    - str: The first orap value, or empty string if None/empty
    """
    if isinstance(orap_value, list):
        # orap is a list (from improved family_record.py)
        return orap_value[0] if orap_value else ''
    elif isinstance(orap_value, str):
        # orap is a string (legacy format)
        return orap_value.split()[0] if orap_value and orap_value.strip() else ''
    else:
        # orap is None or other type
        return ''
        
class TreeCreation:
    """
    The TreeCreation class is designed to manage and organize data related to publications, patents, or applications, 
    likely within a legal or patent-related context. 
    It initializes various lists that will store different types of information, and optionally, 
    it can be initialized with a DataFrame (pd.DataFrame) that will populate the class attributes upon initialization.
    """
    def __init__(self, db="EPODOC", df: pd.DataFrame = None):
        """
        Initialize the Tree object.
        
        Args:
        - db (str): The database type. Defaults to "EPODOC".
        - df (pd.DataFrame, optional): DataFrame containing initial data. Defaults to None.
        """
        # Initialize attributes
        self.db = db  # Database type, likely used to identify which database schema to interact with
        self.df = df  # Optional DataFrame passed during initialization

        # Default values for various properties
        self.orapNb =  "<no data>"  # Placeholder for 'orapNb' attribute (possibly a reference or identifier)
        
        # Initialize various lists that will hold data, these lists will be populated as needed
        self.an = []  # 'an' represents accessions number
        self.ap = []  # 'ap' represents an application number
        self.pn = []  # 'pn' represents a publication number
        self.pr = []  # 'pr' represents a priority number
        self.orap = []  # 'orap' represents an original application number
        self.orpr = []  # 'orpr' represents an original priority number
        self.evnt = []  # 'evnt' represents legal events information
        self.recInp = []  # 'recInp' represents records or inputs being processed

        # If a DataFrame is passed, populate the class from it
        if df is not None:
            self.populate_from_dataframe(df)  # Populate the attributes using the DataFrame
        
    def clean_doc_number(self, doc_number):
        """
        Clean a document number by removing trailing alphabetic and numeric kind codes.
        
        Args:
        - doc_number (str): The document number to be cleaned.
        
        Returns:
        - str: Cleaned document number.
        """        
        # print("doc_number:", doc_number)         
        # First: remove DOCDB-style kind codes like 'A1', 'B2', 'C' with a regular expression to remove trailing alphabetic and numeric kind codes
        doc_number = re.sub(r'[A-Za-z]{1,2}[0-9]*$', '', doc_number).strip()
        # print("doc_number:", doc_number)
        # Second: remove single trailing letter if preceded by a digit (e.g., GB9015198A)
        doc_number = re.sub(r'(?<=\d)[A-Za-z]$', '', doc_number).strip()
        # print("cleaned_doc_number:", doc_number)
        return doc_number

    def populate_from_dataframe(self, df: pd.DataFrame):
        """
        Populate the Tree object with data from a DataFrame.
        
        Args:
        - df (pd.DataFrame): DataFrame containing initial data.
        """        
        self.orapNb = str(df.shape[0])
        self.an = df['accession_number'].tolist()
        self.ap = df['app_number'].tolist()
        self.pn = (df['pub_number'].astype(str) + df['pub_kind'].astype(str)).tolist()
        self.pr = df['priority_numbers'].tolist()
        self.orap = df['orap'].tolist()
        # print("self.orap:", self.orap)
        self.orpr = [''] * df.shape[0]  # Initializing orpr with empty strings
        self.evnt = df['legal_events'].tolist()  # Populate legal events
        self.ad   = df['app_date'].tolist()           # application filing dates
        self.pd   = df['pub_date'].tolist()           # publication dates
        self.prd  = df['priority_dates'].tolist()     # priority dates {pr_number: date}
        # print("self.evnt:", self.evnt)
        # display(df)
        # print("df.source_doc_number.iloc[0]", df.source_doc_number.iloc[0])
        # print("self.source_doc_number", self.source_doc_number)        
        self.recInp = self.clean_doc_number(df.source_doc_number.iloc[0])  # Access source_doc_number
        # print("1. self.recInp", self.recInp)
        # print("type(self.recInp):", type(self.recInp))
        # print("Final DataFrame columns:", df.columns)
    
    def create_nested_dict(self, df: pd.DataFrame, selected_countries=None) -> Dict[str, Any]:
        """
        Create a nested dictionary from a DataFrame.
    
        Args:
        - df (pd.DataFrame): The DataFrame containing data.
    
        Returns:
        - Dict[str, Any]: Nested dictionary with hierarchical data.
        """
        # print("Tree creation:")
        # Reset index to ensure continuous integer indexing
        df = df.reset_index(drop=True)

        # Print DataFrame head and columns for debugging
        # print("DataFrame df:")
        # display(df)
        # print("\nDataFrame Columns:")
        # print(df.columns)

        # Initialize tree with the DataFrame
        self.populate_from_dataframe(df)

        self.orapNb = df.shape[0]
        # print("self.orapNb:", self.orapNb)
        # print()

        listAPs = df['app_number'].tolist() # check listAPs from df dataframe before deleting values later
        # print("listAPs:", listAPs)
        # print()

        listPRs = df['priority_numbers'].tolist() # check listPRs from df dataframe before deleting values later
        # print("listPRs:", listPRs)
        # print()
        
        # Generate listORAPs
        # print("df['orap']:", df['orap'])        
        listORAPs = df['orap'].apply(get_first_orap).dropna().tolist()

        # # listORAPs contains all oldest applications with potential redundancies
        # print("listORAPs:", listORAPs) 

        if self.orapNb is not None and int(self.orapNb) > 0:
            # Initialize root dictionary
            root = {'0': 0} # Ensure root['0'] is initialized
            if self.db in ["EPODOC", "DOCDB"]:
                # First pass: handle the new condition and empty 'orap' values
                for i in range(len(self.df)):
                    current_row = df.iloc[i]
                    # print()
                    # print(f"Row {i}: {current_row}")  # Print the row to check its content

                    # Safely handle 'accession_number'
                    accession_number = current_row['accession_number'].split(' ')[0] if pd.notna(current_row['accession_number']) else ''
                    # print("accession_number:", accession_number)
                    # Safely handle 'app_number'
                    app_number = current_row['app_number'].split(' ')[0] if pd.notna(current_row['app_number']) else ''
                    # print("app_number:", app_number)
                    # Safely handle 'pub_number'
                    pub_number = str(current_row['pub_number']) + str(current_row['pub_kind']) # current_row['pub_number']
                    # print("pub_number:", pub_number)
                    priority_numbers = current_row['priority_numbers']
                    # print("priority_numbers:", priority_numbers)
                    # Safely handle 'orap'
                    orap = get_first_orap(current_row['orap']) if pd.notna(current_row['orap']) else ''
                    # print("orap:", orap)
                    if (orap == '') and (accession_number != app_number):
                        for j in range(len(df)):
                            if df.iloc[j]['accession_number'] == accession_number:
                                df.iloc[i, df.columns.get_loc('orap')] = df.iloc[j]['orap']
                                break  # Stop searching after finding the match
                                
                orapNumber = 0             
                # Second pass: create root dictionary and handle remaining empty 'orap' values
                for i in range(len(df), 0, -1):
                    prior_row = df.iloc[i-1]
                    # print()
                    # print("prior_row:", prior_row)
                    # print("i-1 < len(df):", i-1, len(df), i-1 < len(df))
                    # print("prior_row['app_number']:", prior_row['app_number'])
                    if i-1 < len(df) and prior_row['app_number'] is not None:                    
                        # Skip rows where 'accession_number' or 'app_number' is None or invalid
                        if pd.isna(prior_row['accession_number']) or pd.isna(prior_row['app_number']) or prior_row['app_number'] == "Unknown0000000":
                            # print(f"Skipping row {i} due to None or invalid values")
                            continue

                        # print("listAPs:", listAPs)
                        # print("1. get_first_orap(prior_row['orap'])", get_first_orap(prior_row['orap']))

                        # Proper handling of orap as list or string
                        orap_value = prior_row['orap']
                        orap_first = get_first_orap(orap_value)

                        # Check if orap is effectively empty (works for both list and string)
                        is_empty_orap = (
                            orap_value is None or 
                            orap_value == '' or 
                            orap_value == [] or 
                            (isinstance(orap_value, list) and len(orap_value) == 0) or
                            orap_first == ''
                        )

                        # Check if orap points to external parent (not in family)
                        is_external_parent = (not is_empty_orap and orap_first not in listAPs)

                        # For external parents: create ONE synthetic root entry whose
                        # root_ap IS the external orap value (e.g. 'DK49496').
                        # generate_tree() uses root_ap as my_node to find children,
                        # so all members with orap=['DK49496'] will be found correctly.
                        # Deduplicate: skip if a root with the same root_ap already exists.
                        if is_external_parent:
                            already_has = any(
                                isinstance(v, dict) and v.get('root_ap') == orap_first
                                for v in root.values() if v != 0
                            )
                            if not already_has:
                                r = root['0'] + 1
                                root[r] = {
                                    'root_an': orap_first,
                                    'root_ap': orap_first,
                                    'root_pn': orap_first,
                                    'root_pr': [],
                                    'root_orap': '',
                                    'root_evnt': [],
                                    'root_ad':   '',
                                    'root_pd':   '',
                                    'root_prd':  {}
                                }
                                root['0'] = r

                        # Make it a root if no parent (orap is empty)
                        if is_empty_orap:
                            r = root['0'] + 1
                            
                            root[r] = {
                                'root_an': prior_row['accession_number'].split(' ')[0] if pd.notna(prior_row['accession_number']) else '',                        
                                'root_ap': prior_row['app_number'].split(' ')[0] if pd.notna(prior_row['app_number']) else '',
                                'root_pn': str(prior_row['pub_number']) + str(prior_row['pub_kind']),
                                'root_pr': prior_row['priority_numbers'],
                                'root_orap': get_first_orap(prior_row['orap']) if pd.notna(prior_row['orap']) else '',
                                'root_evnt': prior_row['legal_events'],  # Include legal events
                                'root_ad':   prior_row.get('app_date', '') or '',
                                'root_pd':   prior_row.get('pub_date', '') or '',
                                'root_prd':  prior_row.get('priority_dates', {}) or {}
                                }

                            # print("i and r:", i-1, r)
                            # print("2. prior_row['orap']", prior_row['orap'])
                        
                            # Ensure i-1 is within the bounds
                            # Check if orap is empty (works for both list and string)
                            current_orap_is_empty = (
                                prior_row['orap'] is None or
                                prior_row['orap'] == '' or
                                prior_row['orap'] == [] or
                                (isinstance(prior_row['orap'], list) and len(prior_row['orap']) == 0)
                            )

                            if i > 0 and current_orap_is_empty:
                                df_orap_is_empty = (
                                    df.at[i-1, 'orap'] is None or
                                    df.at[i-1, 'orap'] == '' or
                                    df.at[i-1, 'orap'] == [] or
                                    (isinstance(df.at[i-1, 'orap'], list) and len(df.at[i-1, 'orap']) == 0)
                                )
                                if df_orap_is_empty:                                
                                # # Use .at for positional access
                                # if df.at[i-1, 'orap'] == '':
                                    # print()
                                    # print("df.at[i-1, 'orap']:", df.at[i-1, 'orap'])
                                    priority_numbers = df.at[i-1, 'priority_numbers']
                                    # Check if it's a list and convert it to a string if necessary
                                    if isinstance(priority_numbers, list):
                                        priority_numbers = ','.join(priority_numbers)
                                        
                                    # print("df.at[i-1,'accession_number']", df.at[i-1,'accession_number'])
                                    # print("df.at[i-1,'priority_dates']", df.at[i-1,'priority_dates'])
                                    # print("priority_numbers:", priority_numbers)
                                    # print()

                                    # Extract priority numbers from priority_dates if priority_numbers is empty
                                    if not priority_numbers and isinstance(df.at[i-1, 'priority_dates'], dict):
                                        priority_numbers = ' '.join(df.at[i-1, 'priority_dates'].keys())
                                        df.at[i-1, 'priority_numbers'] = priority_numbers  # Directly update dataframe
                                            
                                    # Retain only the latest priority number based on the latest priority date
                                    if isinstance(df.at[i-1, 'priority_dates'], dict) and df.at[i-1, 'priority_dates']:
                                        priority_numbers = max(df.at[i-1, 'priority_dates'].items(), key=lambda x: x[1])[0]
    
                                    # Now you can safely compare and assign values
                                    # if not df.at[i-1, 'orap'] and priority_numbers != '':
                                    #     df.at[i-1, 'orap'] = priority_numbers
                                    # else:
                                    #     orapNumber += 1
                                    #     df.at[i-1, 'orap'] = f"noOrap{orapNumber}"
        
                                    # print("df.at[i-1, 'orap']:", df.at[i-1, 'orap'])
                                    # prior_row['orap'] = df.at[i-1, 'orap']  # Update the prior_orap if needed
                                    # print("3. prior_orap:", prior_row['orap'])
                                    # print()
                                    self.orap = df['orap'].tolist()  # Ensure the tree's orap is updated from the DataFrame
                                
                                    # Update the dictionary with the latest orap value from DataFrame
                                    root[r]['root_orap'] = get_first_orap(df.at[i - 1, 'orap']) if df.at[i - 1, 'orap'] else ''
                            
                            root['0'] = r

                def to_datetime_safe(value):
                    try:
                        return pd.to_datetime(value, errors='coerce')
                    except Exception:
                        return pd.NaT

                def get_earliest_date(row):
                    pd_dates = row.get('priority_dates', {})
                    filing = to_datetime_safe(row.get('appln_filing_date'))
                    if isinstance(pd_dates, dict):
                        pd_converted = [to_datetime_safe(d) for d in pd_dates.values()]
                        pd_valid = [d for d in pd_converted if pd.notna(d)]
                        if pd_valid:
                            return min(pd_valid + ([filing] if pd.notna(filing) else []))
                    return filing if pd.notna(filing) else pd.NaT
        
                def apply_third_pass_and_fallback(df: pd.DataFrame, root: dict) -> dict:
                    for i in range(len(df)):
                        current_row = df.iloc[i]
                        orap_value = get_first_orap(current_row['orap']) if pd.notna(current_row['orap']) else ''
                        app_number = current_row['app_number'].split(' ')[0] if pd.notna(current_row['app_number']) else ''

                        # Debugging output to check values of orap_value and app_number
                        # print(f"Row {i}: orap_value = {orap_value}, app_number = {app_number}")
                        # print("orap_value and orap_value != app_number:", orap_value and orap_value != app_number)
                        
                        if orap_value and orap_value != app_number != '':
                            
                            matched_rows = df[df['app_number'].str.startswith(orap_value)]
                            if not matched_rows.empty:
                                matched_rows = matched_rows.copy()
                                matched_rows['earliest'] = matched_rows.apply(get_earliest_date, axis=1)
                                matched_row = matched_rows.sort_values('earliest').iloc[0]

                                current_earliest = get_earliest_date(current_row)
                                matched_earliest = matched_row['earliest']

                                assign_new_root = False
                                if pd.notna(matched_earliest) and pd.notna(current_earliest):
                                    assign_new_root = matched_earliest < current_earliest

                                if assign_new_root:
                                    new_root = {
                                        'root_an': matched_row['accession_number'].split(' ')[0] if pd.notna(matched_row['accession_number']) else '',
                                        'root_ap': matched_row['app_number'].split(' ')[0] if pd.notna(matched_row['app_number']) else '',
                                        'root_pn': str(matched_row['pub_number']) + str(matched_row['pub_kind']),
                                        'root_pr': matched_row['priority_numbers'],
                                        'root_orap': get_first_orap(matched_row['orap']) if pd.notna(matched_row['orap']) else '',
                                        'root_evnt': matched_row['legal_events'],
                                        'root_ad':   matched_row.get('app_date', '') or '',
                                        'root_pd':   matched_row.get('pub_date', '') or '',
                                        'root_prd':  matched_row.get('priority_dates', {}) or {}
                                    }

                                    already_exists = any(
                                        isinstance(k, int) and new_root['root_ap'] == existing['root_ap']
                                        for k, existing in root.items()
                                    )
                                    
                                    if not already_exists:
                                        r = root['0'] + 1
                                        root[r] = new_root
                                        root['0'] = r

                        else:
                            # print("orap_value, app_number:", orap_value, app_number)
                            # print("If no matching 'orap' root, create a fallback root")
                            fallback_orap = get_first_orap(current_row['orap']) if pd.notna(current_row['orap']) else ''

                            # Make sure root is initialized as a dictionary
                            if not isinstance(root, dict):
                                root = {}
                            if '0' not in root:
                                root['0'] = 0

                            if fallback_orap and fallback_orap not in listAPs:
                                # External orap: create ONE synthetic root with root_ap = orap value.
                                # Deduplicate: skip if already present.
                                already_has = any(
                                    isinstance(v, dict) and v.get('root_ap') == fallback_orap
                                    for v in root.values() if v != 0
                                )
                                if not already_has:
                                    r = root['0'] + 1
                                    root[r] = {
                                        'root_an': fallback_orap,
                                        'root_ap': fallback_orap,
                                        'root_pn': fallback_orap,
                                        'root_pr': [],
                                        'root_orap': '',
                                        'root_evnt': [],
                                        'root_ad':   '',
                                        'root_pd':   '',
                                        'root_prd':  {}
                                    }
                                    root['0'] = r
                            else:
                                # No external orap: use the member itself as fallback root.
                                fallback_root = {
                                    'root_an': current_row['accession_number'].split(' ')[0] if pd.notna(current_row['accession_number']) else '',
                                    'root_ap': current_row['app_number'].split(' ')[0] if pd.notna(current_row['app_number']) else '',
                                    'root_pn': str(current_row['pub_number']) + str(current_row['pub_kind']),
                                    'root_pr': current_row['priority_numbers'],
                                    'root_orap': fallback_orap,
                                    'root_evnt': current_row['legal_events'],
                                    'root_ad':   current_row.get('app_date', '') or '',
                                    'root_pd':   current_row.get('pub_date', '') or '',
                                    'root_prd':  current_row.get('priority_dates', {}) or {}
                                }
                                if not any(
                                    isinstance(v, dict) and v.get('root_ap') == fallback_root['root_ap']
                                    for v in root.values()
                                ):
                                    r = root['0'] + 1
                                    root[r] = fallback_root
                                    root['0'] = r

                            # Debugging output to verify the final root structure
                            # print("Updated root structure after fallback:", root)
    
                    return root

                root = apply_third_pass_and_fallback(df, root)

                # Deduplicate and sort listORAPs by earliest priority or filing date
                listORAPs_raw = df['orap'].apply(get_first_orap).dropna().tolist()
                unique_oraps = list(set(listORAPs_raw))

                def to_datetime_safe(value):
                    try:
                        return pd.to_datetime(value, errors='coerce')
                    except Exception:
                        return pd.NaT

                def get_earliest_date_for_orap(orap_value):
                    rows = df[df['app_number'].str.startswith(orap_value)]
                    if rows.empty:
                        return pd.NaT

                    def extract_earliest(row):
                        pd_dates = row.get('priority_dates', {})
                        filing = to_datetime_safe(row.get('appln_filing_date'))

                        if isinstance(pd_dates, dict):
                            pd_dates_converted = [to_datetime_safe(d) for d in pd_dates.values()]
                            pd_dates_valid = [d for d in pd_dates_converted if pd.notna(d)]
                            if pd_dates_valid:
                                return min(pd_dates_valid + [filing]) if pd.notna(filing) else min(pd_dates_valid)
                        return filing if pd.notna(filing) else pd.NaT

                    rows = rows.copy()
                    rows['earliest_date'] = rows.apply(extract_earliest, axis=1)
                    return rows['earliest_date'].min()
                    
                # print(df[['app_number', 'priority_numbers', 'orap']].head(100))

                # print("df['orap']:", df['orap'])
                # print("df in tree_creation")
                # display(df.head(500))        
               
                listORAPs = sorted(unique_oraps, key=get_earliest_date_for_orap)
                # print("listORAPs:", listORAPs)
                
        return root, listAPs, listORAPs, df        

class TreeNode:
    def __init__(self, name=None, children=None, **kwargs): 
        self.name = name
        self.children = children or []
        for key, value in kwargs.items():
            setattr(self, key, value)

    def dict_to_object(data):
        if isinstance(data, list):
            return [TreeNode.dict_to_object(item) for item in data]
        elif isinstance(data, dict):
            return TreeNode(**{key: dict_to_object(value) for key, value in data.items()})
        else:
            return data
        