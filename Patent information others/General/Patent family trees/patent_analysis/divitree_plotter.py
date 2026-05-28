# /patent_analysis/divitree_plotter.py

import os
import re
import ast
import pandas as pd
import numpy as np

from IPython.display import display, HTML, clear_output

import plotly.graph_objects as go
import plotly.offline as pyo
from ipywidgets import widgets, HBox, Output
from plotly.graph_objs import FigureWidget
from plotly.subplots import make_subplots

# Initialize Plotly offline mode
pyo.init_notebook_mode(connected=True)

# Map interleaving_type --> color (for this whole plot OR per row if type column exists)
COLOR_MAP = {
    "Priorities":       "#8fbbda",  # blue
    "Applications":     "#ffbf86",  # orange
    "Parents":          "#96d096",  # green
    "Publications":     "#eb9393",  # red/pink
    "Citations":        "#cab3de",  # purple/mauve
    "Classifications":  "#c6aba5",  # taupe/neutral
    "Parties":          "#f1bbe0",  # pink/lilac
    "Legal Events":     "#bfbfbf",  # light gray
    "Procedural Codes": "#7ec3a7",  # teal
    "Images":           "#dede90",  # yellow-green
    "Bibliographic Register Codes": "#ffde59",  # bright yellow
    "Events Register Codes":        "#ff7f50",  # coral
    "Procedural Steps":             "#6495ED",  # cornflower blue
    "UPP Codes":                    "#20b2aa",  # light sea green
    "Claims":                       "#F5DEB3",  # wheat — neutral annotation
    "Concepts":                     "#FFD700",  # gold — XFR concept highlight
}

def detect_indent_and_type(line, known_applications, interleaving_type=None):
    # print("known_applications:", known_applications)
    # print("5. interleaving_type:", interleaving_type)
    line = line.rstrip('\n')
    if line.strip() == "":
        return None, None, None, None

    prefix_match = re.match(r'^([., ]+)', line)
    indent = prefix_match.group(1) if prefix_match else ''
    indent_level = indent.count(',') + indent.count('.')
    content = line[len(indent):].strip()

    if indent.startswith(','):
        entry_type = "Application"
        line_type = "comma"
        known_applications.add(content)
    elif indent.startswith('.'):
        # [Claim 1] and [Concept] lines have fixed types regardless of interleaving_type
        if content.startswith('[Claim 1]'):
            entry_type = "Claims"
        elif content.startswith('[Concept]'):
            entry_type = "Concepts"
        else:
            entry_type = "Application" if content in known_applications else interleaving_type or "No selection"
        line_type = "dot"
    else:
        entry_type = "no further selection"
        line_type = "unknown"

    # print("entry_type:", entry_type)
    return indent_level, content, entry_type, line_type

def parse_item_list(item_str):
    try:
        items = ast.literal_eval(item_str)
        if not isinstance(items, list):
            items = [items]
    except:
        items = str(item_str).split(',')
    return [str(i).strip().strip("'\"") for i in items if str(i).strip().upper() not in ['NONE', 'NULL', '']]

class DiviTreePlotter:
    def __init__(self, workdir="/home/jovyan/output"):
        self.workdir = workdir
        self.image_width = 700  # pixels
        self.persistent_color_map = {}  # maps node id → color
        self.persistent_type_map = {}   # maps node id → type
        if not hasattr(self, "last_selected_fixed_type"):
            self.last_selected_fixed_type = None
        # Pub-serial → application-number map populated by patent_processor
        # from FamilyRecord.get_pub_to_app_map(). Used to embed the true
        # application number in tooltip data-appid for EP Register links.
        self.pub_to_app_map = {}
        # print("Recorded last_selected_fixed_type:", self.last_selected_fixed_type)
    
    def assign_branch_colors(self, df, fromWhere=None):
        """
        Apply COLOR_MAP for all node types,
        and refine 'Applications' with distinct per-app colours,
        while preserving fixed colours like UPP Codes.
        """
        # print("🚨 assign_branch_colors ENTERED fromWhere=", fromWhere, flush=True)
        
        # Ensure 'color' and 'hover_bg' columns exist
        for col in ['color', 'hover_bg']:
            if col not in df.columns:
                df[col] = None
                
        if "type" in df.columns:
            df["type"] = df["type"].fillna("").astype(str).str.strip()

        # 🔄 Identify which fixed types (from COLOR_MAP) exist in this data
        present_fixed_types = [t for t in COLOR_MAP.keys() 
                               if t in df["type"].values and t != 'Application']
        # print("Present COLOR_MAP types in this df (excluding 'Application'):", present_fixed_types)

        # Record the user's first-run selection if available and not already recorded
        prev_type = self.last_selected_fixed_type
        if present_fixed_types: # and self.last_selected_fixed_type is None:
            # prefer to store the explicit present fixed type the user chose in their first run
            self.last_selected_fixed_type = present_fixed_types[0]
            # if self.last_selected_fixed_type != prev_type:
            #     print("Updated last_selected_fixed_type to:", self.last_selected_fixed_type)
            # else:
            #     print("Recorded last_selected_fixed_type:", self.last_selected_fixed_type)
        
        # 1️⃣ Replace "No selection" only if there are fixed types to remap to
        if "No selection" in df["type"].values:
            candidates = present_fixed_types + ([self.last_selected_fixed_type] if self.last_selected_fixed_type else []) \
                         + list(reversed(self.persistent_type_map.values())) \
                         + [t for c in self.persistent_color_map.values() for t, col in COLOR_MAP.items() if col == c]
            target_type = next((t for t in candidates if t in COLOR_MAP), None)
            if target_type:
                df["type"] = df["type"].replace({"No selection": target_type})
                print(f"Mapped 'No selection' to {target_type} (persistent-aware)")
            else:
                print("No valid target_type found for 'No selection' — leaving as-is for now")
            
        # 2️⃣ Start with fixed colour map (may assign NaN for Applications if not in COLOR_MAP)
        df["color"] = df["type"].map(COLOR_MAP)
        df["hover_bg"] = df["color"].copy()
        
        # 3️⃣ Assign colors for static types
        #    (covers any fixed type present, not only the small subset)
        for typ in df["type"].unique():
            if typ in COLOR_MAP:
                mask = df["type"] == typ
                df.loc[mask, ["color", "hover_bg"]] = COLOR_MAP[typ]
                # store persistent mapping using a stable key derived from each node id
                for raw_nid in df.loc[mask, "id"]:
                    stable_nid = str(raw_nid).split('[')[0].strip()
                    self.persistent_color_map[stable_nid] = COLOR_MAP[typ]
                    self.persistent_type_map[stable_nid] = typ
                # also remember this typ as last_selected_fixed_type for future No selection mapping
                if self.last_selected_fixed_type is None:
                    self.last_selected_fixed_type = typ

        # 4️⃣ Handle Application nodes, 🔑 Applications: restore or save
        if "id" in df.columns:
            # Extract stable IDs
            df['stable_id'] = df['id'].astype(str).str.split('[').str[0].str.strip()
    
            # Step 1: Restore persistent colors where available
            persistent_colors = df['stable_id'].map(self.persistent_color_map)
            df['color'] = persistent_colors.combine_first(df.get('color'))  # keep existing colors if persistent missing
            df['hover_bg'] = df['color']
    
            # Record types for persistent_type_map if not already present
            missing_types_mask = df['stable_id'].isin(self.persistent_color_map) & \
                                 ~df['stable_id'].isin(self.persistent_type_map) & \
                                 df['type'].notna()
            for sid, typ in zip(df.loc[missing_types_mask, 'stable_id'], df.loc[missing_types_mask, 'type']):
                self.persistent_type_map[sid] = typ

            # Step 2: Assign colors from COLOR_MAP where color is still missing
            missing_color_mask = df['color'].isna() & df['type'].isin(COLOR_MAP)
            df.loc[missing_color_mask, 'color'] = df.loc[missing_color_mask, 'type'].map(COLOR_MAP)
            df.loc[missing_color_mask, 'hover_bg'] = df.loc[missing_color_mask, 'color']

            # Step 3: Update persistent maps for newly assigned colors
            for sid, col, typ in zip(df.loc[missing_color_mask, 'stable_id'], 
                                     df.loc[missing_color_mask, 'color'], 
                                     df.loc[missing_color_mask, 'type']):
                self.persistent_color_map[sid] = col
                self.persistent_type_map[sid] = typ

            # Step 4: Log remaining nodes with no color            
            mask_log = df['color'].isna() & (df['type'] != 'Application')
            for sid, node_type, node_id_raw in zip(df.loc[mask_log, 'stable_id'], 
                                                   df.loc[mask_log, 'type'], 
                                                   df.loc[mask_log, 'id']):
                print(f"⚠️ Skipping node {node_id_raw} of type '{node_type}': no color available")

            # Optional: drop helper column
            df.drop(columns='stable_id', inplace=True)

        # 5️⃣ Warn about truly missing types
        missing_types = set(df["type"].unique()) - set(COLOR_MAP.keys()) - {"Application"}
        if missing_types:
            print(f"Warning: missing color for {missing_types}")

        # print("Unique types in df after filtering:", df["type"].unique())
        # print("Sample color assignments:", df[["type", "color", "hover_bg"]].drop_duplicates().head(10))
    
        return df
        
    def find_latest_output_files(self):
        directory = self.workdir
        # print("directory:", directory)
        first_files = [f for f in os.listdir(directory) if f.endswith("_first_output.txt")]
        # print("first_files:", first_files)
        second_files = [f for f in os.listdir(directory) if f.endswith("_second_output.txt")]
        # print("second_files:", second_files)
        
        first_files.sort(key=lambda f: os.path.getmtime(os.path.join(directory, f)), reverse=True)
        second_files.sort(key=lambda f: os.path.getmtime(os.path.join(directory, f)), reverse=True)
        
        first_path = os.path.join(directory, first_files[0]) if first_files else ""
        second_path = os.path.join(directory, second_files[0]) if second_files else ""
        # print("first_path:", first_path)    
        # print("second_path:", second_path)
        
        return (first_path, second_path)  # Tuple, no None
    
    def read_tree_data(self, file_path, interleaving_type=None, reference_mode=None):
        root_display_name = os.path.basename(file_path).split("_")[0]
        root_node = {
                     'display_name': root_display_name, 
                     'type': 'Application',
                     'children': []
                    }
        current_level = {0: root_node}
        known_applications = {root_display_name}
    
        # Map comment lines to interleaving types
        comment_to_type = {
            "Tree displays application data": "Applications",
            "Tree displays priority data": "Priorities",
            "Tree displays parent data": "Parents",
            "Tree displays publication data": "Publications",
            "Tree displays citation data": "Citations",
            "Tree displays classification data": "Classifications",
            "Tree displays parties data": "Parties",
            "Tree includes images": "Images",
            "Tree includes legal events": "Legal Events",
            "Tree includes procedural codes": "Procedural Codes",
            "Tree includes bibliographic register codes": "Bibliographic Register Codes",
            "Tree includes events register codes": "Events Register Codes",
            "Tree includes procedural steps": "Procedural Steps",
            "Tree includes unitary patent protection (UPP) codes": "UPP Codes",
            "Tree includes first independent claim": "Claims",
            "Tree includes XFR concepts from first independent claim": "Concepts",
        }
            
        seen_rows = set()  # To avoid duplicate entries
        
        # print("read_tree_data before instruction to check: interleaving_type, reference_mode:", interleaving_type, reference_mode)
        
        with open(file_path, 'r') as file:
            for line in file:
                line = line.rstrip()

                # Only scan header/comment lines (not tree data lines) for type detection.
                # Tree data lines start with ',' or '.' — skip type detection for those.
                is_data_line = line.startswith((',', '.'))

                if not is_data_line:
                    # Check for reference mode pattern in header lines only
                    if 'in the reference mode publication' in line.lower():
                        reference_mode = 'publication'
                    elif 'in the reference mode application' in line.lower():
                        reference_mode = 'application'

                    # Detect interleaving_type from header comment lines only,
                    # and only if not already supplied by the caller or detected earlier.
                    if interleaving_type is None:
                        for key, value in comment_to_type.items():
                            if key.lower() in line.lower():
                                interleaving_type = value
                                # print(f"🔎 Found interleaving_type: {interleaving_type} from line: {line}")
                                break

                if not line or not is_data_line:
                    continue
                
                result = detect_indent_and_type(line, known_applications, interleaving_type)
                if not result:
                    continue
                    
                indent_level, display_name, entry_type, line_type = result
                # print("1. interleaving_type:", interleaving_type)
                # print("indent_level:", indent_level)            
                # print("display_name:", display_name)
                # print("entry_type:", entry_type)

                # ✅ SKIP root-level lines entirely (no node, no DataFrame row)
                if indent_level == 0:
                    continue
                
                parent_level = max(l for l in current_level if l <= indent_level)
                parent_node = current_level[parent_level]
            
                if parent_node is None or display_name == parent_node['display_name']:
                    # print(f"Error: No parent found at indent level {indent_level} for line: {line}")
                    continue
                
                row_path = f"{parent_node['display_name']} > {display_name}"
                if row_path in seen_rows:
                    continue
                seen_rows.add(row_path)
            
                node = {
                    'display_name': display_name,
                    'type': entry_type,
                    'line_type': line_type,
                    'depth': indent_level,
                    'children': []
                }
            
                parent_node['children'].append(node)
                current_level[indent_level + 1] = node
                
        if interleaving_type is None:
            interleaving_type = "no further selection"
            
        # print("read_tree_data as detected: interleaving_type, reference_mode:", interleaving_type, reference_mode)
        return {'Root': root_node}, interleaving_type, reference_mode
        
    def _extract_date_from_label(self, label: str):
        """
        Extracts the first valid date from a label string like:
          "RFEE: Renewal fee payment; 03 [20040526]"
          "GRAA: (Expected) grant [2012-04-27]"
            "0009199INVT: Change - inventor [🟠20040319]"
        Returns pd.Timestamp or NaT, with a date (YYYY-MM-DD or YYYYMMDD) from procedural/legal labels.
        """
        if not isinstance(label, str):
            return pd.NaT
                
        # Match YYYY-MM-DD or YYYYMMDD, with or without emojis/brackets
        patterns = [
            r"\[🟠?(\d{4}-\d{2}-\d{2})\]",   # e.g. [🟠2012-04-27]
            r"\[🟠?(\d{8})\]",               # e.g. [🟠20040526]
            r"(\d{4}-\d{2}-\d{2})",          # plain YYYY-MM-DD
            r"(\d{8})"                       # plain YYYYMMDD
        ]

        for pat in patterns:
            m = re.search(pat, label)
            if m:
                val = m.group(1)
                try:
                    return pd.to_datetime(val, errors='coerce')
                except Exception:
                    continue
        return pd.NaT
        
    # @staticmethod
    def create_grouped_items_df(self, data, parent_key=''):
        ids, parents, values, types, depths, line_types, event_dates = [], [], [], [], [], [], []

        def compute_node_value(node):
            """Compute value as sum of all descendant leaves."""
            tooltip_types = {
                "Parents", "Priorities", "Publications", "Citations", "Classifications",
                "Parties", "Legal Events", "Procedural Codes", "Bibliographic Register Codes",
                "Events Register Codes", "Procedural Steps", "UPP Codes", "Claims", "Concepts"
            }
            if node.get('type') in tooltip_types:
                try:
                    items = parse_item_list(node.get('display_name', ''))
                    return len([c for c in items if str(c).strip().upper() != 'NONE'])
                except Exception:
                    return 1
            if not node.get('children'):
                return 1
            return sum(compute_node_value(c) for c in node.get('children', []))

        for key, node in data.items():
            if not isinstance(node, dict):
                continue

            display_name = node.get('display_name', key)
            node_type = node.get('type', 'Unknown')
            node_value = compute_node_value(node)
            date_val = self._extract_date_from_label(display_name)

            # --- Special case: root node ---
            if parent_key == '' and key == 'Root':
                for child in node.get('children', []):
                    child_df = self.create_grouped_items_df({child['display_name']: child}, parent_key='')
                    for col in ['id', 'parent', 'value', 'type', 'depth', 'line_type', 'event_date']:
                        locals()[col + 's'].extend(child_df[col])
                continue

            tooltip_types = {
                "Parents", "Priorities", "Publications", "Citations", "Classifications",
                "Parties", "Legal Events", "Procedural Codes", "Bibliographic Register Codes",
                "Events Register Codes", "Procedural Steps", "UPP Codes"
            }

            # --- Add current node ---
            # Claims and Concepts store as single strings (not split by parse_item_list)
            if node_type in tooltip_types and node_type not in {"Claims", "Concepts"}:
                try:
                    items = parse_item_list(display_name)
                    items = [c for c in items if str(c).strip().upper() != 'NONE']
                except Exception:
                    items = [] if str(display_name).strip().upper() == 'NONE' else [display_name]
                if items:
                    ids.append(f"{parent_key}: {items}")
                    parents.append(parent_key)
                    values.append(len(items))
                    types.append(node_type)
                    depths.append(node.get('depth', 0))
                    line_types.append(node.get('line_type'))
                    event_dates.append(date_val)
            else:
                if display_name != parent_key:
                    ids.append(display_name)
                    parents.append(parent_key)
                    values.append(node_value)
                    types.append(node_type)
                    depths.append(node.get('depth', 0))
                    line_types.append(node.get('line_type'))
                    event_dates.append(date_val)

            # --- Recurse into children ---
            for child in node.get('children', []):
                child_df = self.create_grouped_items_df({child['display_name']: child}, parent_key=display_name)
                for col in ['id', 'parent', 'value', 'type', 'depth', 'line_type', 'event_date']:
                    locals()[col + 's'].extend(child_df[col])

        # --- Sanity check: all lists must have equal length ---
        n = len(ids)
        assert all(len(lst) == n for lst in [parents, values, types, depths, line_types, event_dates]), (
            f"Length mismatch: ids={len(ids)}, parents={len(parents)}, values={len(values)}, "
            f"types={len(types)}, depths={len(depths)}, line_types={len(line_types)}, event_dates={len(event_dates)}"
        )

        # --- Construct DataFrame ---
        df = pd.DataFrame({
            'id': ids,
            'parent': parents,
            'value': values,
            'type': types,
            'depth': depths,
            'line_type': line_types,
            'event_date': event_dates
        })

        # --- Extract event_date from id text if needed ---
        df['event_date'] = df['id'].apply(self._extract_date_from_label).fillna(df['event_date'])
        df['event_date'] = pd.to_datetime(df['event_date'], errors='coerce')

        # --- Propagate missing event dates downwards ---
        id_to_date = dict(zip(df['id'], df['event_date']))
        for idx, row in df.iterrows():
            if pd.isna(row['event_date']) and row['parent'] in id_to_date:
                df.at[idx, 'event_date'] = id_to_date[row['parent']]

        # Fill remaining missing with earliest
        earliest = df['event_date'].dropna().min()
        if pd.notna(earliest):
            df['event_date'] = df['event_date'].fillna(earliest)

        # # --- Debug summary ---
        # valid_dates = df['event_date'].dropna()
        # print(f"🧭 Extracted {len(valid_dates)} valid event dates "
        #       f"({valid_dates.min() if len(valid_dates)>0 else 'None'} → {valid_dates.max() if len(valid_dates)>0 else 'None'})")

        return df

    # Improved tooltip formatter for both citations and classifications
    def build_tooltip(self, row, df=None): # def build_tooltip(self, row, interleaving_type, df=None):
        """
        Generate a formatted tooltip for a sunburst node.
        Handles ANSI codes, lists, images, and general formatting.
        """
        # Regex pattern to catch all ANSI escape sequences like \x1b[38;5;214m or \x1b[0m
        ANSI_ESCAPE_RE = re.compile(r'\x1B(?:\[[0-?]*[ -/]*[@-~])')

        def remove_ansi(text: str) -> str:
            """Remove ANSI escape codes from a string."""
            return ANSI_ESCAPE_RE.sub('', text)
    
        node_id = row['id']
        value = row['value']
        app_id = row.get('app_id', '')  # ✅ ensures we can prepend parent app
        pub_id = row.get('pub_id', '')  # publication/patent propagated
        parent = row['parent']
        depth = row.get('depth', 0)
        line_type = row.get('line_type', 'unknown')
        
        interleaving_type = row.get('interleaving_type', 'no further selection')
        reference_mode = row.get('reference_mode', 'Application')
    
        if not value or str(value).strip().upper() == "NONE":
           return ""

        # Images are handled entirely by the _prepare_sunburst_df assignment block.
        # build_tooltip must not generate any text output for them.
        if interleaving_type == "Images":
            return ""
        
        text = str(node_id).strip()
        prefix = text
        
        # For Claims nodes the node_id is "EP1234567B1: ['[Claim 1] 1. …']"
        # Set prefix to the publication number (parent) immediately so the
        # bold header shows the pub number, not the raw claim text.
        if interleaving_type == "Claims":
            prefix = str(parent).strip() if parent else str(app_id).strip()

        # For dotted application lines, show parent publication instead
        if interleaving_type == "Applications" and line_type == "dot" and parent:
            # pub_id may be empty string; fallback to prefix
            prefix = str(parent).strip()
        elif interleaving_type == "Parents" and line_type == "dot" and node_id:
            prefix = str(node_id).strip()
            
        # print("prefix:", prefix)
        
        # Split out any leading "prefix: [list...]" pattern
        # For Claims/Concepts the prefix is already set above — preserve it.
        _prefix_locked = interleaving_type in ("Claims", "Concepts")
        if ": [" in text:
            _split_prefix, remainder = text.split(": [", 1)
            if not _prefix_locked:
                prefix = _split_prefix.strip()
            text = "[" + remainder.strip().rstrip("]") + "]"  # ensure list string is well-formed

        if text.startswith("[") and text.endswith("]"):
            try:
                items = ast.literal_eval(text)
                if not isinstance(items, list):
                    items = [str(items)]
            except Exception:
                # Remove brackets and keep as single-item list
                items = [text[1:-1].strip()]
        else:
            # Single string without brackets, keep as one-item list
            items = [text]

        # For Concepts nodes: extract clean phrase for the bold header label.
        # Must be done AFTER items is built (items[0] needed).
        # If the phrase is a publication number (leaked through extraction) or
        # empty, fall back to a 'Publication:' header using the parent pub id.
        # 2-3 letter country prefix + 4+ digits + optional kind code + optional *
        # Also strips '/' and bare '*' separators before token-testing.
        _re_pub_tok_hdr = re.compile(r'^[A-Z]{2,3}\d{4,}[A-Z0-9]*\*?$')
        if interleaving_type == "Concepts":
            raw = items[0].strip() if items else text
            if raw.startswith("[Concept]"):
                raw = raw[len("[Concept]"):].strip()
            # Check if raw is a publication/application number phrase or empty.
            # Strip P-operator, slash separators and bare * before testing.
            _raw_tokens = [t for t in raw.split() if t not in ('P', '/', '*')]
            _is_pub_phrase = (not raw or
                             (_raw_tokens and
                              all(_re_pub_tok_hdr.match(t) for t in _raw_tokens)))
            if _is_pub_phrase:
                # Show as a publication node, not a concept.
                # Set prefix to the bare pub id — the header builder will prepend
                # reference_mode (e.g. 'Publication') so we must NOT add it here.
                _pub_hdr = str(parent).strip() if parent else str(app_id).strip()
                prefix = _pub_hdr
                interleaving_type = "_concepts_as_pub"  # suppress concept body
            else:
                try:
                    from patent_analysis.claim_concepts import format_concept_for_display as _fcd3
                except ImportError:
                    _fcd3 = lambda c: c.replace(' P ', ' NEAR ')
                prefix = f"Concept: {_fcd3(raw)}"

        # print("items:", items)
                
        # Remove ANSI escape codes from everything
        items = [remove_ansi(str(i)).strip() for i in items if str(i).strip()]
        # print("items:", items)

        # ── Rejoin comma-split fragments for event / procedural nodes ────────
        # parse_item_list splits on commas, so a single tree line like:
        #   UREG: Date of registration of unitary effect; AT, BE, …, SI [20231213]
        # becomes ['UREG: ...; AT', 'BE', 'BG', …, 'SI [20231213]'].
        # Without rejoining, the 2-letter country fragments are hijacked by the
        # designation_buffer logic below and appear as a spurious DESIGNATION
        # row in the tooltip. The date bracket also ends up stranded in the
        # last fragment instead of attached to the code.
        # Fix: for these node types, when items[0] has a code:description
        # structure and none of items[1:] look like independent entries (no ':'),
        # rejoin everything into one display string and normalise the date.
        # NOTE: _prepare_sunburst_df stores interleaving_type with .capitalize() which
        # destroys multi-word capitalisation: "Procedural Codes" → "Procedural codes",
        # "Legal Events" → "Legal events", "UPP Codes" → "Upp codes", etc.
        # All comparisons must therefore use .lower() to match reliably.
        _event_tooltip_types_lower = {
            "legal events", "procedural codes", "bibliographic register codes",
            "events register codes", "procedural steps", "upp codes"
        }
        _is_event_type = interleaving_type.lower() in _event_tooltip_types_lower
        if (_is_event_type
                and len(items) > 1
                and ': ' in items[0]
                and not any(': ' in it for it in items[1:])):
            rejoined = ', '.join(items)
            # Normalise compact date YYYYMMDD → YYYY-MM-DD
            rejoined = re.sub(r'\[(\d{4})(\d{2})(\d{2})\]', r'[\1-\2-\3]', rejoined)
            items = [rejoined]
        else:
            # Single-item case: still normalise compact dates for event types
            items = [re.sub(r'\[(\d{4})(\d{2})(\d{2})\]', r'[\1-\2-\3]', it)
                     if _is_event_type else it
                     for it in items]

        # General nodes (Applications, Publications, etc.)
        bullet_items = []
        designation_buffer = []
        for item in items:
            # Normalise: drop matched outer quotes, then any trailing punctuation
            # (comma/period/semicolon/colon/closing-bracket) that NPL records
            # often glue onto the identifier — e.g. "XP000539528," or
            # "XP000539528;". Apply only to the trailing side so leading
            # markers like '+' (new-in-this-branch) survive.
            item = str(item).strip().strip("'\"")
            item = item.rstrip(",.;:)]}\u00a0 ").strip()
         
            if item.upper() in ['NONE', 'NULL', '']:
                continue
                
            if item.startswith("DESIGNATION:"):
                designation_buffer.append(item.replace("DESIGNATION:", "").strip())                
            elif len(item) == 2 and item.isalpha() and item.isupper():
                designation_buffer.append(item)
            else:
                # ── New-item marker: items prefixed with '+' are newly added
                # members (country-filtered additions). Render them with the
                # same green colour used in the textual tree output so the
                # popup tooltip stays visually consistent.
                _S_NEW = "color:#2e7d32;font-weight:bold"
                is_new = item.startswith('+')
                item_body = item[1:] if is_new else item  # text without the leading +

                if interleaving_type == "Applications":
                    if line_type == "dot":  # ✅ only add app numbers, not the parent itself
                        indent = "&nbsp;" * depth
                        if is_new:
                            bullet_items.append(
                                f"{indent}• <span style='{_S_NEW}'>+{item_body}</span>"
                            )
                        else:
                            bullet_items.append(f"{indent}• {item}")
                else:
                    if line_type == "comma":
                        if is_new:
                            bullet_items.append(
                                f"<b><span style='{_S_NEW}'>+{item_body}</span></b>"
                            )
                        else:
                            bullet_items.append(f"<b>{item}</b>")
                    elif line_type == "dot":
                        indent = "&nbsp;" * depth
                        if is_new:
                            bullet_items.append(
                                f"{indent}• <span style='{_S_NEW}'>+{item_body}</span>"
                            )
                        else:
                            bullet_items.append(f"{indent}• {item}")
                    else:
                        if is_new:
                            bullet_items.append(
                                f"<span style='{_S_NEW}'>+{item_body}</span>"
                            )
                        else:
                            bullet_items.append(item)
        
        # For Classifications: stash ALL items in a hidden span BEFORE truncating,
        # so the sticky popup can retrieve the full list while the hover tooltip
        # stays capped at 20 to avoid overwhelming the user.
        _all_cpc_attr = ''
        if interleaving_type == 'Classifications':
            # Stash ALL items in a hidden span (before any truncation)
            # so the sticky popup can retrieve the full list.
            import re as _re_cpc
            def _strip_to_sym(s):
                import re as _r
                s = _r.sub(r'<[^>]+>', '', s)   # strip HTML tags
                s = _r.sub(r'&[a-z]+;', '', s)  # strip HTML entities (&nbsp; etc)
                s = s.strip().lstrip('•·\u2022').strip()  # strip bullets
                s = s.lstrip('+').strip()               # strip '+' novelty marker
                return s
            _clean = [_strip_to_sym(b)
                      for b in bullet_items if b.strip() not in ('', '...')]
            _all_cpc_attr = ','.join(c for c in _clean if c)

        if len(bullet_items) > 20:
            bullet_items = bullet_items[:20] + ["..."]
            
        # ✅ If we collected DESIGNATION states, add as one compact line
        if designation_buffer:
            bullet_items.append("• DESIGNATION: " + ", ".join(designation_buffer))
        
        # print("bullet_items:", bullet_items)
        
        # The bold header is built here so each node type can control its own label.
        # Claims  → "<b>publication: EP1234567B1</b>"
        # Concepts → "<b>Concept: optical P disk</b>"  (no "publication:" prefix)
        # All others → "<b>{reference_mode}: {prefix}</b>"
        # Embed app_id as data-appid so JS can use the application number
        # (e.g. EP2019188854) for EP Register links instead of the pub number.
        _appid_attr = f" data-appid='{app_id}'" if app_id else ''

        # When the upstream tree generator formats a node identifier as
        # "appNum / pubNum" (e.g. "EP2009164213* / EP2101496B1*"), the
        # Publication: header should show only the publication side, so that
        # the user reads "Publication: EP2101496B1*" and not the redundant
        # combined form. The application number is still preserved on the
        # node via data-appid for EP Register / OPS lookups.
        _header_prefix = prefix
        if interleaving_type != "Concepts" and isinstance(_header_prefix, str) and " / " in _header_prefix:
            _left, _right = _header_prefix.rsplit(" / ", 1)
            # Heuristic: keep the right-hand side only if it looks like a
            # publication number (letters then digits, optional kind code
            # and the trailing "*" the tree generator appends to roots).
            # Otherwise leave the prefix unchanged.
            if re.match(r'^[A-Z]{2,3}\d+[A-Z0-9]*\*?$', _right.strip()):
                _header_prefix = _right.strip()

        if interleaving_type == "Concepts":
            tooltip = f"<b{_appid_attr}>{prefix}</b><br>"          # prefix = "Concept: optical P disk"
        elif interleaving_type == "Claims":
            tooltip = f"<b{_appid_attr}>{reference_mode}: {_header_prefix}</b><br>"  # prefix = pub number
        else:
            tooltip = f"<b{_appid_attr}>{reference_mode}: {_header_prefix}</b><br>"

        if interleaving_type == "Images":
            # Items can be:
            #   (a) a ready-made <img …> tag produced by format_image_link
            #       (contains base64 data URI – already fully formed)
            #   (b) a remote URL (http/https) – legacy fallback
            #   (c) anything else – show as plain text
            for img in items:
                if not isinstance(img, str) or not img.strip():
                    continue
                img_stripped = img.strip()
                if img_stripped.lower().startswith("<img "):
                    # Already a complete <img> tag (base64 or URL) – use as-is
                    tooltip += img_stripped + "<br>"
                elif img_stripped.lower().startswith(("http://", "https://", "data:image")):
                    # Raw URL or bare data URI – wrap in an <img> tag
                    tooltip += (f"<img src='{img_stripped}' width='400' "
                                f"style='max-height:600px; object-fit:contain;'><br>")
                else:
                    # Plain text fallback
                    tooltip += f"{img_stripped}<br>"
        elif interleaving_type == "Claims":
            # items[0] is the full stored string: "[Claim 1] 1. An optical disk…"
            # Strip the '[Claim 1] ' sentinel, then format for Plotly popup.
            claim_text = items[0].strip() if items else ""
            if claim_text.startswith("[Claim 1]"):
                claim_text = claim_text[len("[Claim 1]"):].strip()
            # Suppress non-English (non-ASCII-majority) claim text silently
            non_ascii = sum(1 for c in claim_text if ord(c) > 127)
            if non_ascii > len(claim_text) * 0.3:
                tooltip += "<i>Claim 1 not available in English</i>"
            elif claim_text:
                # ── XFR concept extraction (done before rendering claim text) ─
                # Concepts are extracted first so we can highlight new ones
                # inside the claim text itself.
                _C_GREEN  = "color:#2e7d32;font-weight:bold"
                _C_PLAIN  = "color:#333333"
                _re_angle_c   = re.compile(r'[A-Za-z]?<[^>]+>')
                _re_pub_tok_c = re.compile(r'^[A-Z]{2,3}\d{4,}[A-Z0-9]*\*?$')

                concepts = []
                try:
                    from patent_analysis.claim_concepts import extract_concepts as _xfr
                    _raw = _xfr(claim_text, language='EN', max_concepts=8)
                    # Display-level safety net
                    def _is_bad_c(ph):
                        if _re_angle_c.search(ph): return True
                        tks = ph.strip().split()
                        if len(tks) == 1:
                            tk = tks[0].rstrip('+')
                            if len(tk) <= 2 and tk.isalpha(): return True
                        dt = [t for t in tks if t not in ('P','/',  '*')]
                        return bool(dt and all(_re_pub_tok_c.match(t) for t in dt))
                    concepts = [c for c in _raw if not _is_bad_c(c)]
                except Exception:
                    pass

                # ── Build ancestor concept token set ──────────────────────────
                # Walk shallower Claims rows in df to find the root-branch claim.
                # A concept is NEW if none of its stemmed tokens match any
                # token already seen in an ancestor claim's concept set.
                _new_concept_flags = [True] * len(concepts)   # default: all new
                _anc_token_roots: set = set()

                def _norm(tok):
                    return tok.upper().rstrip('+').rstrip('?')

                try:
                    if df is not None and 'type' in df.columns and 'depth' in df.columns:
                        cur_depth = int(row.get('depth', 9999))
                        _claims_df = df[df['type'].str.strip() == 'Claims']
                        for _, _anc_row in _claims_df.iterrows():
                            if int(_anc_row.get('depth', 9999)) >= cur_depth:
                                continue   # only look at shallower (ancestor) rows
                            _anc_id = str(_anc_row.get('id', ''))
                            if '[Claim 1]' in _anc_id:
                                _anc_id = _anc_id.split('[Claim 1]', 1)[1].strip()
                            _anc_concepts = _xfr(_anc_id, language='EN', max_concepts=12)
                            for _ac in _anc_concepts:
                                for _tok in _ac.split(' P '):
                                    _root = _norm(_tok)
                                    if len(_root) >= 4:
                                        _anc_token_roots.add(_root)

                        # Mark each concept as new or inherited
                        for _i, _c in enumerate(concepts):
                            _cur_roots = {_norm(t) for t in _c.split(' P ')}
                            # Inherited if ALL its roots are already in ancestors
                            if _cur_roots.issubset(_anc_token_roots):
                                _new_concept_flags[_i] = False
                except Exception:
                    pass   # ancestor lookup is best-effort; fall back to all-new

                # ── Build set of new-concept word roots for in-claim highlight ─
                _highlight_roots: set = set()
                for _c, _is_new in zip(concepts, _new_concept_flags):
                    if _is_new:
                        for _tok in _c.split(' P '):
                            _root = _norm(_tok)
                            if len(_root) >= 3:
                                _highlight_roots.add(_root)

                # ── Highlight new-concept words inside the claim text ─────────
                def _highlight_word(word, roots, style):
                    """Return word wrapped in style span if its root matches any root."""
                    w_up = re.sub(r'[.,;:()\[\]]', '', word).upper()
                    for nr in roots:
                        if len(nr) >= 4 and w_up.startswith(nr):
                            return f"<span style='{style}'>{word}</span>"
                    return word

                # ── Word-wrap PLAIN text first, then highlight per line ───────
                # Critical: never insert spans before wrapping — spaces inside
                # style='color:…;font-weight:bold' would be treated as word
                # boundaries and shred the tag across lines.
                words = claim_text.split()
                lines, current, current_len = [], [], 0
                for word in words:
                    wlen = len(word)
                    if current_len + wlen + (1 if current else 0) > 80:
                        lines.append(" ".join(current))
                        current = [word]
                        current_len = wlen
                    else:
                        current.append(word)
                        current_len += wlen + (1 if len(current) > 1 else 0)
                if current:
                    lines.append(" ".join(current))

                # Now highlight each line's words individually — spans stay atomic
                if _highlight_roots:
                    lines = [
                        " ".join(
                            _highlight_word(w, _highlight_roots, _C_GREEN)
                            for w in line.split()
                        )
                        for line in lines
                    ]

                tooltip += "<i>Claim 1:</i><br>" + "<br>".join(lines)

                # ── Render concept list with new/inherited distinction ────────
                if concepts:
                    try:
                        from patent_analysis.claim_concepts import format_concept_for_display as _fcd
                    except ImportError:
                        _fcd = lambda c: c.replace(' P ', ' NEAR ')
                    try:
                        from patent_analysis.claim_concepts import split_concept_to_pairs as _scp
                    except ImportError:
                        _scp = lambda c: [c]
                    tooltip += "<br><br><b>Concepts:</b><br>"
                    for _c, _is_new in zip(concepts, _new_concept_flags):
                        for _cd in _scp(_fcd(_c)):
                            if _is_new:
                                tooltip += (f"• <span style='{_C_GREEN}'>{_cd}</span><br>")
                            else:
                                tooltip += f"• {_cd}<br>"
            else:
                tooltip += "<i>Claim 1 not available</i>"
        elif interleaving_type == "Concepts":
            # prefix already holds "Concept: optical P disk" (set above).
            # Show only the parent publication number beneath it — no redundant
            # repetition of the concept text or a Query block.
            pub = str(parent).strip() if parent else str(app_id).strip()
            if pub:
                tooltip += f"<i>{pub}</i>"
        elif bullet_items and line_type == "dot":
            tooltip += f"<i>{interleaving_type}:</i><br>" + "<br>".join(bullet_items)
            # Embed hidden span with ALL CPC symbols for the sticky popup JS
            if _all_cpc_attr:
                tooltip += (f"<span id='dt-all-cpc' "
                            f"data-cpc='{_all_cpc_attr}' "
                            f"style='display:none'></span>")

        # print("2. tooltip:", tooltip)
        
        return tooltip

    # Set this to True to re-enable the [_prime_cpc_cache] / [data-pivot-cpc patch]
    # diagnostic traces that were used while developing the XFR-Pivot anchor.
    _DEBUG_PIVOT_CPC = False

    def _prime_cpc_cache(self, df, interleaving_type):
        """
        Build self._tooltip_cpc_cache (parent/app_id/pub_id -> CPC string)
        BEFORE any tooltip is built. The cache is later consulted by the
        data-pivot-cpc patch pass to embed full CPC subgroup anchors into
        Concepts/Claims/Parties tooltips, so XFR-Pivot Ranking queries can
        prepend (cpc="G11B20/10527" OR cpc="H04N13/144" OR ...) AND ... .

        Strategy:
          1. If df already contains Classifications rows, use them directly.
          2. Otherwise scan self.workdir for a *_Classifications_*_output.txt
             file and parse it.
        """
        import time
        t0 = time.time()
        _dbg = print if self._DEBUG_PIVOT_CPC else (lambda *a, **kw: None)

        if hasattr(self, "_tooltip_cpc_cache") and self._tooltip_cpc_cache:
            _dbg(f"🔵 [_prime_cpc_cache] reuse cache "
                 f"({len(self._tooltip_cpc_cache)} entries) "
                 f"for chart='{interleaving_type}' — {time.time()-t0:.3f}s")
            return
        self._tooltip_cpc_cache = {}
        _dbg(f"🔵 [_prime_cpc_cache] building cache for chart='{interleaving_type}'")

        def _fill_from_df(source_df, label=""):
            n_before = len(self._tooltip_cpc_cache)
            n_with_syms = 0
            for _, r in source_df.iterrows():
                # Classifications rows from create_grouped_items_df look like:
                #   id     = "PARENT: ['G11B20/00007', 'G11B20/10222', ...]"
                #   parent = the publication / application identifier
                rid    = str(r.get("id",     "") or "").strip()
                parent = str(r.get("parent", "") or "").strip()
                lb = rid.find("[")
                rb = rid.rfind("]")
                syms = []
                if lb != -1 and rb != -1 and rb > lb:
                    inner = rid[lb + 1 : rb]
                    for piece in inner.split(","):
                        s = piece.strip().strip("'").strip('"').strip()
                        s = re.sub(r"<[^>]+>", "", s).lstrip("•·").lstrip("+").strip()
                        if s and s.upper() != "NONE":
                            syms.append(s)
                app = str(r.get("app_id", "") or "").strip()
                pub = str(r.get("pub_id", "") or "").strip()
                if syms:
                    n_with_syms += 1
                    joined = ",".join(syms)
                    if parent:
                        self._tooltip_cpc_cache.setdefault(("pub", parent), joined)
                        self._tooltip_cpc_cache.setdefault(("app", parent), joined)
                    if app:
                        self._tooltip_cpc_cache.setdefault(("app", app), joined)
                    if pub:
                        self._tooltip_cpc_cache.setdefault(("pub", pub), joined)
            _dbg(f"🔵 [_prime_cpc_cache] {label}: filled "
                  f"{len(self._tooltip_cpc_cache)-n_before} entries from "
                  f"{len(source_df)} rows ({n_with_syms} had symbols)")

        # Path 1: current df has Classifications rows
        if "interleaving_type" in df.columns:
            cls_rows = df[df["interleaving_type"].str.capitalize() == "Classifications"]
            if not cls_rows.empty:
                _fill_from_df(cls_rows, label="Path 1 (current df)")
                _dbg(f"🔵 [_prime_cpc_cache] done — {time.time()-t0:.3f}s, "
                      f"{len(self._tooltip_cpc_cache)} cache entries")
                return

        # Path 2: companion Classifications tree file in workdir
        workdir = getattr(self, "workdir", None)
        if not workdir:
            _dbg(f"🔴 [_prime_cpc_cache] no workdir — giving up ({time.time()-t0:.3f}s)")
            return
        import glob as _glob

        # Optimisation: try the seed-family-specific Classifications file FIRST.
        # On a workdir with 100+ files this avoids globbing everything.
        # The seed stem is typically available via self.seed_pub or similar.
        seed_stem = None
        for attr in ("seed_pub", "seed_app", "seed", "seed_id"):
            v = getattr(self, attr, None)
            if v:
                seed_stem = re.sub(r"[^A-Za-z0-9]", "", str(v))
                break
        # Also try to derive the stem from the df itself (its first parent or
        # seed-publication identifier in the 'parent' column of root rows)
        if not seed_stem and "parent" in df.columns:
            try:
                _root_parents = df[df.get("depth", 0) == 0]["parent"]
                if not _root_parents.empty:
                    _cand = str(_root_parents.iloc[0]).strip()
                    if _cand:
                        seed_stem = re.sub(r"[^A-Za-z0-9]", "", _cand)
            except Exception:
                pass

        candidates = []
        if seed_stem:
            fast = (
                _glob.glob(os.path.join(workdir, f"{seed_stem}_Classifications_*_output.txt"))
            )
            if fast:
                candidates = sorted(fast, key=os.path.getmtime, reverse=True)
                _dbg(f"🔵 [_prime_cpc_cache] Path 2 fast: {len(candidates)} "
                      f"candidate(s) for seed='{seed_stem}'")

        if not candidates:
            # General fallback — slower, scans every tree file
            all_candidates = (
                _glob.glob(os.path.join(workdir, "*_first_output.txt")) +
                _glob.glob(os.path.join(workdir, "*_second_output.txt"))
            )
            cls_explicit = [p for p in all_candidates if "_Classifications_" in p]
            candidates = sorted(cls_explicit, key=os.path.getmtime, reverse=True)
            if not candidates:
                candidates = sorted(all_candidates, key=os.path.getmtime, reverse=True)
            _dbg(f"🔵 [_prime_cpc_cache] Path 2: {len(candidates)} candidate(s) "
                  f"in {workdir} (filtered from {len(all_candidates)} total)")
        for cand_path in candidates:
            try:
                cand_data, cand_type, _ = self.read_tree_data(cand_path)
                if str(cand_type).capitalize() != "Classifications":
                    continue
                cand_df = self.create_grouped_items_df(cand_data)
                if cand_df.empty:
                    continue
                cand_df["interleaving_type"] = "Classifications"
                _fill_from_df(cand_df, label=f"Path 2 ({os.path.basename(cand_path)})")
                if self._tooltip_cpc_cache:
                    break
            except Exception as e:
                _dbg(f"⚠️  [_prime_cpc_cache] error reading "
                      f"{os.path.basename(cand_path)}: {e}")
                continue
        if self._tooltip_cpc_cache:
            _dbg(f"✅ [_prime_cpc_cache] done — {time.time()-t0:.3f}s, "
                  f"{len(self._tooltip_cpc_cache)} cache entries")
            for i, (k, v) in enumerate(self._tooltip_cpc_cache.items()):
                if i >= 3: break
                _dbg(f"      sample: {k} -> {v[:80]}{'...' if len(v) > 80 else ''}")
        else:
            _dbg(f"🔴 [_prime_cpc_cache] FAILED — no CPC data sourced "
                  f"({time.time()-t0:.3f}s)")

    def _prepare_sunburst_df(self, df, interleaving_type, reference_mode):
        """Shared preprocessing for single & multiple sunburst plots."""
        
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        # Define tooltip types
        tooltip_types = {
            "Applications", "Parents", "Priorities", "Publications", "Citations", 
            "Classifications", "Parties", "Legal Events", "Procedural Codes", 
            "Bibliographic Register Codes", "Events Register Codes", 
            "Procedural Steps", "UPP Codes", "Claims", "Concepts"
        }
        # print("_prepare_sunburst_df 1.: interleaving_type, reference_mode):", interleaving_type, reference_mode)
        # Copy to avoid mutating original
        df = df.copy()
        df['interleaving_type'] = str(interleaving_type).capitalize()
        df['reference_mode'] = str(reference_mode).capitalize()
        df = df[df['id'].apply(lambda x: isinstance(x, str) and x.strip() != '')] 

        # Prime the CPC cache so the patch pass below can embed data-pivot-cpc.
        self._prime_cpc_cache(df, interleaving_type)
        
        # ✅ Propagate event_date if present (for animations)
        if "event_date" in df.columns:
            df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")

        # Normalize type column and drop empty/non-string IDs
        df['type'] = df['type'].str.strip()
        
        # Prepare formatted IDs for labels
        df['formatted_id'] = df['id'].apply(lambda x: str(x).split(":")[0] if ": [" in str(x) else str(x))

        # Strip the '[Concept] ' sentinel from sunburst labels — the sector colour
        # (gold) already identifies these nodes; repeating the prefix on every
        # sector is redundant and clutters the chart.
        _concept_mask = df['type'].str.strip() == 'Concepts'
        df.loc[_concept_mask, 'formatted_id'] = (
            df.loc[_concept_mask, 'formatted_id']
            .str.replace(r'^\[Concept\]\s*', '', regex=True)
        )

        # ── Legal / procedural event nodes: show  CODE [date]  as sector label ──
        # The generic formatted_id (line 729) strips to the parent publication number,
        # which is redundant with the blue/red ring it belongs to.
        # This block replaces that with the short event/procedural code plus the event
        # date (if any), covering every format found in the two tree types:
        #
        #   Legal events  : "[EP] GRAA: (EXPECTED) GRANT [2023-11-10]"
        #                   "[WO] 121: EP: THE EPO HAS BEEN INFORMED… [2021-09-01]"
        #   Procedural    : "RFEE: Renewal fee payment; 03 [20220706]"
        #                   "EPIDOS PCT: PCT data prior to European publication [20200129]"
        #                   "0009250: Lapse of the patent in a contracting state [20240412]"
        #                   "0: unknown [20200129]"
        #                   "VAPT: Validation of the patent; KH"  (no date)
        #
        # Country-code brackets like [EP] / [WO] are stripped from the label
        # (they are already implicit in which ring the node lives in).
        # Full descriptions are kept intact in the tooltip.
        _event_label_types = {
            "Legal Events", "Procedural Codes", "Bibliographic Register Codes",
            "Events Register Codes", "Procedural Steps", "UPP Codes"
        }
        # Match BOTH the real escape byte (0x1B) AND its 4-char repr form (\x1b)
        # because create_grouped_items_df stores ids via f'{parent}: {items_list}'
        # which calls str(list) → repr() on each element, converting the actual
        # byte 0x1B into the literal character sequence \, x, 1, b.
        #   r'\x1b'  in a raw-string → regex hex-escape → matches byte 0x1B
        #   r'\\x1b' in a raw-string → regex \\x1b     → matches literal \x1b
        _ansi_re_label = re.compile(r'(?:\x1b|\\x1b)\[[0-9;]*m')
        # Compiled patterns reused inside the inner function
        _re_quoted_item  = re.compile(r"'([^']+)'")          # first 'item' in list repr
        _re_dquoted_item = re.compile(r'"([^"]+)"')           # fallback: first "item"
        _re_cc_prefix    = re.compile(r'^\[[A-Z]{2,3}\]\s*') # [EP] / [WO] / [KR] …
        _re_date_iso     = re.compile(r'\[(\d{4}-\d{2}-\d{2})\]')  # [YYYY-MM-DD]
        _re_date_compact = re.compile(r'\[(\d{8})\]')              # [YYYYMMDD]

        def _event_code_label(raw_id: str) -> str:
            """
            Return  "CODE [YYYY-MM-DD]"  (or just  "CODE"  when no date is present)
            for a legal-event or procedural-code node id.

            id format:  "parent_key: ['[CC] CODE: description [date]']"
                    or  "parent_key: ['CODE: description [date]']"
                    or  "parent_key: ['CODE: desc; CC1', 'CC2', ..., 'CCn [date]']"
                        ← comma-split (e.g. UREG with country list)

            Steps
            -----
            1. Strip ANSI colour codes (real byte 0x1B and repr form \\x1b).
            2a. CODE — from the first quoted item only (code is always first).
            2b. DATE — from the entire items_part string, not just the first item,
                because parse_item_list splits on commas: entries like
                  UREG: ...; AT, BE, ..., SI [20231213]
                become ['UREG: ...; AT', 'BE', ..., 'SI [20231213]']
                so the date bracket lands in the *last* element.
            3. Drop the optional [CC] country-code prefix before extracting code.
            4. Code token = everything before the first ':'.
            5. Normalise date: ISO YYYY-MM-DD kept as-is; compact YYYYMMDD converted.
            6. Return "CODE [date]" or "CODE".
            """
            clean = _ansi_re_label.sub('', raw_id)

            # ── Steps 2a+2b: split off the list part ──────────────────────────
            items_part = None
            item = clean          # fallback if no list repr found
            if ': [' in clean:
                items_part = clean.split(': [', 1)[1]
                m = _re_quoted_item.search(items_part)
                if m:
                    item = m.group(1)   # first quoted element → code lives here
                else:
                    m2 = _re_dquoted_item.search(items_part)
                    item = m2.group(1) if m2 else items_part.split(',')[0].strip().strip("'\"")

            # ── Step 3: remove [CC] prefix (country code in brackets) ─────────
            item = _re_cc_prefix.sub('', item).strip()

            # ── Step 4: code = everything before the first ':' ────────────────
            code = item.split(':', 1)[0].strip() if ':' in item else item.strip()

            # ── Step 5: find date — search full items_part so comma-split     ──
            # entries like UREG whose date is in the last fragment are still found
            search_scope = items_part if items_part is not None else item
            date_str = None
            m = _re_date_iso.search(search_scope)
            if m:
                date_str = m.group(1)                  # already YYYY-MM-DD
            else:
                m = _re_date_compact.search(search_scope)
                if m:
                    d = m.group(1)                     # YYYYMMDD → YYYY-MM-DD
                    date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"

            # ── Step 6: assemble label ────────────────────────────────────────
            return f"{code} [{date_str}]" if date_str else code

        _event_mask = df['type'].isin(_event_label_types)
        if _event_mask.any():
            df.loc[_event_mask, 'formatted_id'] = df.loc[_event_mask, 'id'].apply(
                lambda x: _event_code_label(str(x))
            )

        # create a cleaned key used for tooltip lookups from the original id (not formatted_id)
        df['id_clean'] = df['id'].apply(lambda x: ansi_escape.sub('', str(x)).strip())
        df['tooltip'] = df.get('tooltip', '')

        def normalize_key(s):
            return ansi_escape.sub('', str(s)).strip().split(' ', 1)[0]

        # ── Compute and enrich app_id FIRST so build_tooltip gets correct data-appid ──
        _app_ids, _pub_ids, _cur_app, _cur_pub = [], [], "", ""
        _app_types = ("Application", "Applications", "Publications", "Parents", "Priorities")
        for _, _row in df.iterrows():
            _typ = str(_row.get('type', '')).strip()
            if _typ in ("Publications", "Parents", "Priorities"):
                _cur_pub = _row['formatted_id']
            if _typ in _app_types:
                _cur_app = _row['formatted_id']
            _app_ids.append(_cur_app if _cur_app else _row['formatted_id'])
            _pub_ids.append(_cur_pub if _cur_pub else _row['formatted_id'])
        df['app_id'] = _app_ids
        df['pub_id'] = _pub_ids
        # Enrich: replace publication serial with true EP application number
        if self.pub_to_app_map:
            import re as _re_appid
            _sk_appid = _re_appid.compile(r'^([A-Z]{2}\d+)[A-Z]\d?$')
            def _enrich_appid(v):
                s = str(v or '').strip()
                m = _sk_appid.match(s)
                return self.pub_to_app_map.get(m.group(1) if m else s, s)
            df['app_id'] = df['app_id'].apply(_enrich_appid)
        # Also patch data-appid in any already-cached tooltips in full_tooltip_map
        if self.pub_to_app_map and hasattr(self, 'full_tooltip_map') and self.full_tooltip_map:
            import re as _re_cache
            for _k, _tv in list(self.full_tooltip_map.items()):
                _t = _tv['tooltip'] if isinstance(_tv, dict) else str(_tv)
                def _fix_appid(mat):
                    old_val = mat.group(1)
                    _m2 = _sk_appid.match(old_val) if self.pub_to_app_map else None
                    new_val = self.pub_to_app_map.get(_m2.group(1) if _m2 else old_val, old_val)
                    return f"data-appid='{new_val}'"
                _t2 = _re_cache.sub(r"data-appid='([^']+)'", _fix_appid, _t)
                if _t2 != _t:
                    if isinstance(_tv, dict):
                        self.full_tooltip_map[_k] = dict(_tv, tooltip=_t2)
                    else:
                        self.full_tooltip_map[_k] = _t2

        # --- Ensure full_tooltip_map exists ---
        if not hasattr(self, 'full_tooltip_map') or self.full_tooltip_map is None:
            self.full_tooltip_map = {}

        # Pre-populate full_tooltip_map from existing tooltips (never cache Images tooltips)
        if interleaving_type != "Images":
            for _, row in df.iterrows():
                key = normalize_key(row['formatted_id'])
                if row.get('tooltip') and key not in self.full_tooltip_map:
                    self.full_tooltip_map[key] = {
                        'tooltip': row['tooltip'],
                        'interleaving_type': str(interleaving_type).capitalize(),
                        'reference_mode': str(reference_mode).capitalize()
                    }
        
        # Assign tooltips for filtered DataFrame
        # Skip entirely for Images — will be set unconditionally at the end
        if interleaving_type != "Images":
            for idx, row in df.iterrows():
                key = normalize_key(row['id'])
                # Try restoring from persistent map
                tooltip_val = self.full_tooltip_map.get(key)
                if tooltip_val:
                    df.at[idx, 'tooltip'] = tooltip_val['tooltip'] if isinstance(tooltip_val, dict) else tooltip_val
                else:
                    df.at[idx, 'tooltip'] = self.build_tooltip(row)
        
        # app_id and pub_id already set and enriched above
        
        # # --- DEBUG: Before restoration ---
        # print("💡 Tooltip column before restoration:")
        # print(df[['id', 'tooltip']].head(5))
    
        # 🔥 Restore tooltips from full tree if available (skip for Images)
        restored_count = 0
        if interleaving_type != "Images" and getattr(self, 'full_tooltip_map') and self.full_tooltip_map:
            # Make sure keys match the filtered df
            for idx, row in df.iterrows():
                key = row['formatted_id']
                cached = self.full_tooltip_map.get(key)
                if cached:
                    df.at[idx, 'tooltip'] = cached['tooltip'] if isinstance(cached, dict) else cached
                    df.at[idx, 'interleaving_type'] = cached['interleaving_type'] if isinstance(cached, dict) else interleaving_type
                    df.at[idx, 'reference_mode'] = cached['reference_mode'] if isinstance(cached, dict) else reference_mode
                    restored_count += 1                
                    
        # print(f"✅ Restored {restored_count} tooltips from full_tooltip_map (filtered DF has {len(df)} rows)")
    
        # # --- DEBUG: After restoration ---
        # print("💡 Tooltip column after restoration:")
        # print(df[['id', 'tooltip']].head(5))        
        # print("reference_mode, interleaving_type:", reference_mode, interleaving_type)
        
        # ✅ Generate tooltip only for rows missing one
        # Generate missing tooltips
        generated_count = 0
        for idx, row in df.iterrows():
            if pd.isna(row['tooltip']) or row['tooltip'] == '':
                if interleaving_type in tooltip_types:
                    df.at[idx, 'tooltip'] = self.build_tooltip(row) # , interleaving_type)
                elif interleaving_type == "Images":
                    _id = str(row['id']).strip()
                    if _id.lower().startswith('<img '):
                        df.at[idx, 'tooltip'] = _id        # already a complete <img> tag
                    elif _id.lower().startswith(('http://', 'https://', 'data:image')):
                        df.at[idx, 'tooltip'] = f"<img src='{_id}' width='320'>"
                    else:
                        df.at[idx, 'tooltip'] = ''         # structural node — no hover text
                else:
                    df.at[idx, 'tooltip'] = row['id']
                generated_count += 1

        # print(f"✅ Generated {generated_count} missing tooltips")
    
        # # --- DEBUG: Final tooltip snapshot ---
        # print("💡 Final tooltip snapshot (first 10 rows):")
        # print(df[['id', 'tooltip']].head(10))

        df = self.assign_branch_colors(df, fromWhere=1)

        # ── Deduplicate family-wide event nodes with identical content ────────
        # Some procedural/legal codes (DESIGNATION, VAPT, EXPT, OPPOSITION, …)
        # are written once per patent member by tree_processor, so the sunburst
        # shows multiple identical-label sectors (e.g. 'DESIGNATION' × 3).
        # Fix: for event/procedural rows, build a dedup key from
        #   (formatted_id,  items-content of the raw id)
        # and keep only the shallowest occurrence (lowest depth → closest to root).
        # Non-event rows and rows with unique content are never touched.
        _dedup_types_lower = {
            'legal events', 'procedural codes', 'bibliographic register codes',
            'events register codes', 'procedural steps', 'upp codes'
        }
        if interleaving_type.lower() in _dedup_types_lower and 'depth' in df.columns:
            def _items_content(raw_id):
                """Return the list-repr part of the id (after the first ': [')."""
                s = str(raw_id)
                return s.split(': [', 1)[1] if ': [' in s else s

            df = df.sort_values('depth', ascending=True, kind='stable').reset_index(drop=True)
            dedup_key = list(zip(
                df['parent'].astype(str),        # ← was df['formatted_id']
                df['id'].apply(_items_content)
            ))            
            seen = set()
            keep_mask = []

            for idx, k in enumerate(dedup_key):

                row = df.iloc[idx]
                label = str(row.get("formatted_id", ""))

                # extract legal event code at start of label
                code = label.split()[0].strip().upper() if label else ""

                # AC events are legitimate repeated chronological events
                # never deduplicate them
                if code == "AC":
                    keep_mask.append(True)
                    continue

                # normal duplicate filtering for everything else
                if k in seen:
                    keep_mask.append(False)
                else:
                    seen.add(k)
                    keep_mask.append(True)
        
            n_dropped = keep_mask.count(False)
            if n_dropped:
                df = df[keep_mask].reset_index(drop=True)
                # print(f'Dedup: dropped {n_dropped} duplicate event sector(s)')

        # ── Aggregate Concepts: one sector per publication ─────────────────────
        # Each [Concept] line in the tree produces a separate row → a separate
        # outer-ring sector.  All concepts of one publication should share ONE
        # sector with a combined colour-coded tooltip.
        # Rules:
        #   • Concept phrases starting with '+' are new (green in the textual
        #     tree) → rendered in green in the tooltip.
        #   • Concept text is shown as-is; no bracket content is extracted.
        #   • The sector label shows the concept count (e.g. '8 concepts').
        if interleaving_type.lower() == 'concepts' and 'parent' in df.columns:
            _C_GREEN_CONCEPT = 'color:#2e7d32;font-weight:bold'
            _concept_row_mask = df['type'].str.strip() == 'Concepts'
            _concept_df = df[_concept_row_mask].copy()
            _other_df   = df[~_concept_row_mask].copy()

            # Angle-bracket and bare-symbol filtering is now handled at source
            # in claim_concepts.py. Only the publication-number safety net stays
            # here as a display-level guard for trees generated before that fix.
            _re_pub_token = re.compile(r'^[A-Z]{2}\d{4,}[A-Z0-9]*\*?$')
            def _bad_concept(phrase: str) -> bool:
                p = re.sub(r'^\+', '', phrase.strip())
                p = re.sub(r'^\[Concept\]\s*', '', p)
                tokens = p.strip().split()
                # Display-level safety net: drop phrases that are entirely pub numbers
                data_tokens = [t for t in tokens if t != 'P']
                return bool(data_tokens and
                            all(_re_pub_token.match(t) for t in data_tokens))

            if not _concept_df.empty:
                # Apply filter to both the df rows and inside the tooltip loop
                _concept_df = _concept_df[
                    ~_concept_df['formatted_id'].apply(_bad_concept)
                ].copy()
                _agg_rows = []
                # Group by parent publication; preserve original order via first occurrence
                for _parent_val, _grp in _concept_df.groupby(
                        _concept_df['parent'], sort=False):
                    _phrases = _grp['formatted_id'].astype(str).tolist()
                    _n = len(_phrases)

                    # Build colour-coded tooltip
                    _ref_cap = str(reference_mode).capitalize()
                    _pub     = str(_parent_val).strip()
                    _lines   = []
                    for _p in _phrases:
                        # Strip [Concept] sentinel if still present
                        _p = re.sub(r'^\[Concept\]\s*', '', _p).strip()
                        try:
                            from patent_analysis.claim_concepts import format_concept_for_display as _fcd2
                        except ImportError:
                            _fcd2 = lambda c: c.replace(' P ', ' NEAR ')
                        try:
                            from patent_analysis.claim_concepts import split_concept_to_pairs as _scp2
                        except ImportError:
                            _scp2 = lambda c: [c]
                        _is_new = _p.startswith('+')
                        for _p_disp in _scp2(_fcd2(_p)):
                            if _is_new:
                                _lines.append(
                                    f"\u2022 <span style='{_C_GREEN_CONCEPT}'>{_p_disp}</span>"
                                )
                            else:
                                _lines.append(f'\u2022 {_p_disp}')
                    _combined_tooltip = (
                        f"<b>{_ref_cap}: {_pub}</b><br>"
                        f"<i>Concepts:</i><br>"
                        + "<br>".join(_lines)
                    )

                    # Build one aggregated row from the first group member
                    _agg = _grp.iloc[0].copy()
                    _agg['formatted_id'] = f"{_n} concept{'s' if _n != 1 else ''}"
                    _agg['tooltip']      = _combined_tooltip
                    _agg['value']        = _n
                    # Synthetic unique id so uid assignment never collides
                    _agg['id']           = f"{_parent_val}: [×{_n} Concepts]"
                    _agg_rows.append(_agg)

                import pandas as _pd_alias
                df = _pd_alias.concat(
                    [_other_df, _pd_alias.DataFrame(_agg_rows)],
                    ignore_index=True
                )

        # ── Global dedup: drop fully identical (id, parent) rows ─────────────
        # The df can arrive doubled when patent_processor concatenates results
        # from two output files with identical content (e.g. a filtered tree
        # that has only one patent member).  This covers ALL row types, not
        # just event types, and must run after the event-specific dedup so the
        # event dedup's content-based key still gets first pick.
        _before = len(df)
        df = df.drop_duplicates(subset=['id', 'parent'], keep='first').reset_index(drop=True)
        # if len(df) < _before:
        #     print(f'Global dedup: dropped {_before - len(df)} fully-duplicate row(s)')

        # --- KEY PART: make node ids unique while preserving parent links ---
        # Reset index to get stable numeric row IDs
        df = df.reset_index(drop=True)
        
        # Unique ids and parent mapping
        df['uid'] = df.index.astype(str) + '|' + df['id'].astype(str)

        # Build a mapping from original id -> list of indices where it appears
        id_to_indices = {}
        for idx, orig_id in df['id'].items():
            id_to_indices.setdefault(orig_id, []).append(idx)
            
        # Compute parent_uid for every row.
        # We'll map parent string -> the first row index that has that original id.
        # This works when parent row is present in the DataFrame (typical for a tree built from the file).
        def find_parent_uid(parent_val):
            matches = id_to_indices.get(parent_val, [])
            return df.at[matches[0], 'uid'] if matches else ""
        
        df['parent_uid'] = df['parent'].apply(find_parent_uid)

        # Ensure numeric values
        df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(1)

        # For Images mode, unconditionally overwrite every tooltip so that
        # no cached text (from a previous button press) survives.
        # Image nodes get their <img> tag (with pub number embedded as data-pub);
        # structural nodes get empty string.
        if interleaving_type == "Images":
            def _image_tooltip(row):
                _id = str(row['id']).strip()
                pub = str(row.get('app_id', '') or row.get('parent', '') or '').strip()
                # Strip any uid prefix "N|" from pub if present
                if '|' in pub:
                    pub = pub.split('|', 1)[1]
                caption = (f"<div style='font-size:11px;font-family:sans-serif;"
                           f"color:#333;margin-top:4px;border-top:1px solid #ddd;"
                           f"padding-top:3px;'>"
                           f"<b>First page image published in:</b> {pub}</div>")
                if _id.lower().startswith('<img '):
                    # Inject data-pub into existing tag and append caption
                    img = _id.replace('<img ', f'<img data-pub="{pub}" ', 1)
                    return img + caption
                if _id.lower().startswith(('http://', 'https://', 'data:image')):
                    return (f"<img data-pub=\"{pub}\" src='{_id}' width='320'>" + caption)
                return ''
            df['tooltip'] = df.apply(_image_tooltip, axis=1)

        # ── Inject data-pivot-cpc into Concepts/Claims/Parties tooltips ──────
        # This MUST run as the last step, AFTER the Concepts aggregation block
        # which rebuilds tooltips from scratch and would otherwise discard our
        # patched data-pivot-cpc span. We use a DEDICATED attribute name
        # (data-pivot-cpc, not data-cpc) so the existing CLR-rendering path —
        # which keys off data-cpc — does NOT fire on Concepts/Claims tooltips
        # and replace the XFR-Pivot buttons.
        if (interleaving_type in ("Concepts", "Claims", "Parties")
                and hasattr(self, "_tooltip_cpc_cache")
                and self._tooltip_cpc_cache):
            import time as _t_mod
            _t0 = _t_mod.time()
            _dbg = print if self._DEBUG_PIVOT_CPC else (lambda *a, **kw: None)
            cpc_cache = self._tooltip_cpc_cache
            _dbg(f"🔵 [data-pivot-cpc patch] cache has {len(cpc_cache)} entries; "
                 f"sample keys:")
            for _i, _k in enumerate(list(cpc_cache.keys())[:5]):
                _dbg(f"      {_k}")
            patched = 0
            already_had = 0
            no_match_count = 0
            no_match_examples = []
            for idx, row in df.iterrows():
                tip = row.get("tooltip", "")
                if not tip:
                    continue
                if "data-pivot-cpc=" in tip:
                    already_had += 1
                    continue
                app_id = str(row.get("app_id", "") or "").strip()
                pub_id = str(row.get("pub_id", "") or "").strip()
                parent = str(row.get("parent", "") or "").strip()
                rid = str(row.get("id", "") or "").strip()
                rid_prefix = rid.split(":", 1)[0].strip() if ":" in rid else rid
                # Concepts aggregation builds id as "PUB: [×N Concepts]" — the
                # PUB prefix is the publication identifier we need.
                cpc_val = (
                    cpc_cache.get(("app", app_id), "") or
                    cpc_cache.get(("pub", pub_id), "") or
                    cpc_cache.get(("pub", parent), "") or
                    cpc_cache.get(("app", parent), "") or
                    cpc_cache.get(("pub", rid_prefix), "") or
                    cpc_cache.get(("app", rid_prefix), "")
                )
                if cpc_val:
                    df.at[idx, "tooltip"] = tip + (
                        f"<span class='dt-pivot-cpc-meta' "
                        f"data-pivot-cpc='{cpc_val}' "
                        f"style='display:none'></span>"
                    )
                    patched += 1
                else:
                    no_match_count += 1
                    if len(no_match_examples) < 5:
                        no_match_examples.append(
                            (app_id, pub_id, parent, rid_prefix)
                        )
            _dbg(f"✅ [data-pivot-cpc patch] type={interleaving_type}: "
                 f"patched={patched}, already_had={already_had}, "
                 f"no_match={no_match_count} — {_t_mod.time()-_t0:.3f}s")
            for ex in no_match_examples:
                _dbg(f"      no_match row: app_id={ex[0]!r} pub_id={ex[1]!r} "
                     f"parent={ex[2]!r} rid_prefix={ex[3]!r}")
        elif interleaving_type in ("Concepts", "Claims", "Parties"):
            if self._DEBUG_PIVOT_CPC:
                print(f"🔴 [data-pivot-cpc patch] type={interleaving_type} but "
                      f"cache empty — XFR-Pivot will lack cpc anchor")

        # ── Trim "appNum / pubNum" tooltip headers to just "pubNum" ─────────
        # build_tooltip already does this for freshly built tooltips, but
        # tooltips restored from full_tooltip_map (cached from a prior run,
        # potentially with the old code) still carry the slashed form. We
        # rewrite the bold header here so the user-visible label is always
        # "Publication: EP2101496B1*" rather than
        # "Publication: EP2009164213* / EP2101496B1*", regardless of whether
        # the tooltip came fresh or from cache. The data-appid attribute on
        # the same <b> tag is preserved so EP Register / OPS lookups keep
        # using the application number.
        if interleaving_type != "Concepts" and "tooltip" in df.columns:
            # Match: <b ...optional attrs...>{label}: {appNum} / {pubNum}</b>
            # where both numbers have a patent-number shape. Replace with
            # just pubNum. The appNum side allows 2-3 letter country/kind
            # prefixes (EP, US, JPW, ...) to handle JP/WO twin headers.
            _hdr_re = re.compile(
                r"(<b\b[^>]*>)"                              # opening tag (with attrs)
                r"([^<:]+:\s*)"                              # "Publication: " or similar label
                r"[A-Z]{2,3}\d+[A-Z0-9]*\*?\s*/\s*"          # appNum + " / "
                r"([A-Z]{2,3}\d+[A-Z0-9]*\*?)"               # pubNum (kept)
                r"(\s*</b>)",                                # closing tag
                re.IGNORECASE,
            )
            def _trim_header(s):
                if not isinstance(s, str) or " / " not in s:
                    return s
                return _hdr_re.sub(r"\1\2\3\4", s, count=1)
            df["tooltip"] = df["tooltip"].apply(_trim_header)

        return df
    
    def plot_sunburst(self, df, interleaving_type, reference_mode, output_path=None):
        if df is None or not hasattr(df, "empty") or df.empty:
            print("No data to plot.")
            return None, None
            
        # reference_mode = df['reference_mode'].iloc[0] if 'reference_mode' in df.columns else 'Application'
        # interleaving_type = df['interleaving_type'].iloc[0] if 'interleaving_type' in df.columns else 'no further selection'
        # print("plot_sunburst: reference_mode, interleaving_type, output_path:", reference_mode, interleaving_type, output_path)
        
        df = self._prepare_sunburst_df(df, interleaving_type, reference_mode)  # ✅ factor out shared logic
        
        # Right before setting hovertemplate:

            
        hovertemplate = "%{customdata[1]}<extra></extra>"
        if interleaving_type == "Images":
            hovertemplate = "<extra></extra>"  # custom JS handles display
        # print("hovertemplate:", hovertemplate)

        # print("hovertemplate:", hovertemplate)
        fig = go.Figure(
            go.Sunburst(
                ids=df['uid'],
                labels=df['formatted_id'],
                text=df['formatted_id'],
                parents=df['parent_uid'],
                values=df['value'],
                customdata=np.stack([df['app_id'], df['tooltip'], df['hover_bg']], axis=-1),
                hovertemplate=hovertemplate,                
                branchvalues='remainder' if interleaving_type == "Priorities" else 'total',
                marker=dict(colors=df['color'], line=dict(color='white', width=0.5)) 
            )
        )
    
        fig.update_traces(
            textinfo='text',
            textfont_size=12,
            hoverlabel=dict(
                bgcolor=None,
                font_color="black",
                bordercolor="rgba(0,0,0,0.1)"
            ),
        )
        
        fig.update_layout(
            title=dict(
                text=f'Patent Tree: {interleaving_type}',
                x=0.5,
                xanchor='center',
            ),            
            margin=dict(t=30, l=0, r=0, b=0),
            width=1200,
            height=900,
            showlegend=False
        )        
                
        if output_path is None:
            output_path = os.path.join(self.workdir, "filtered_tree.html")
        fig.write_html(output_path, include_plotlyjs='cdn') # Save silently (no auto display)
        self.append_sticky_popup_script(output_path)
        return fig, output_path
        
    def plot_sunburst_over_time(self, df, interleaving_type, reference_mode, output_path=None, time_col="event_date"):
        """
        Create an animated sunburst that evolves over time.
        Each frame shows the tree state up to the current timestamp.
        """
        # --- Prepare the input dataframe ---
        df = self._prepare_sunburst_df(df, interleaving_type, reference_mode)
        if time_col not in df.columns:
            # print(f"⚠️ Column '{time_col}' not found — cannot animate.")
            return self.plot_sunburst(df, interleaving_type, reference_mode, output_path)

        # print("🧪 event_date sample:", df[time_col].head(10).tolist())
        # print("🧩 label sample:", df.get('formatted_id', df.get('id')).head(10).tolist())

        df = df.copy()
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        # Structural rows (Applications, Parents, etc.) may have NaT for event_date
        # because they are not events themselves. Fill them with the earliest known
        # event date so they always appear as parent anchors in every animation frame
        # instead of being dropped — which would orphan all their legal event children.
        earliest = df[time_col].dropna().min()
        if pd.isna(earliest):
            print(f"\u26a0\ufe0f No valid timestamps in '{time_col}'. Showing static chart.")
            return self.plot_sunburst(df, interleaving_type, reference_mode, output_path)
        df[time_col] = df[time_col].fillna(earliest)

        # One frame per calendar day — same-day events produce identical frames
        df["_date_only"] = df[time_col].dt.normalize()
        timestamps = sorted(df["_date_only"].unique())

        if not timestamps:
            print(f"\u26a0\ufe0f No valid timestamps in '{time_col}'. Showing static chart.")
            return self.plot_sunburst(df, interleaving_type, reference_mode, output_path)

        # print("Unique timestamps:", timestamps)
        # print("Total rows for first timestamp:", len(df[df[time_col] <= timestamps[0]]))
        # print("Sample event_date values:\n", df[time_col].head(10))
        
        hovertemplate = "%{customdata[1]}<extra></extra>"
        if interleaving_type == "Images":
            hovertemplate = "<extra></extra>"  # custom JS handles display
        # print("hovertemplate:", hovertemplate)
        
        # --- Build animation frames ---
        # customdata + hovertemplate must be in every frame: Plotly sunburst does a
        # full data replacement per frame and does NOT retain the initial trace hover data.
        frames = []
        branchvals = 'remainder' if interleaving_type == "Priorities" else 'total'
        for t in timestamps:
            subset = df[df["_date_only"] <= t]
            frames.append(
                go.Frame(
                    data=[go.Sunburst(
                        ids=subset["uid"],
                        labels=subset["formatted_id"],
                        parents=subset["parent_uid"],
                        values=subset["value"],
                        branchvalues=branchvals,
                        marker=dict(colors=subset["color"], line=dict(color='white', width=0.5)),
                        customdata=np.stack([subset["app_id"], subset["tooltip"], subset["hover_bg"]], axis=-1),
                        hovertemplate=hovertemplate,
                    )],
                    name=str(t.date() if hasattr(t, "date") else t)
                )
            )

        # --- Initial figure ---
        first_subset = df[df["_date_only"] <= timestamps[0]]
        fig = go.Figure(
            data=[go.Sunburst(
                ids=first_subset["uid"],
                labels=first_subset["formatted_id"],
                text=first_subset["formatted_id"],
                # text=first_subset['tooltip'], 
                parents=first_subset["parent_uid"],
                values=first_subset["value"],
                # hovertext=first_subset["tooltip"],
                customdata=np.stack([first_subset["app_id"], first_subset["tooltip"], first_subset["hover_bg"]], axis=-1),
                hovertemplate=hovertemplate, 
                branchvalues='remainder' if interleaving_type == "Priorities" else 'total', 
                marker=dict(colors=first_subset["color"], line=dict(color='white', width=0.5)),
                domain=dict(x=[0.05, 0.95], y=[0.18, 1.0]),                
            )],
            frames=frames
        )

        # --- Layout and controls ---
        fig.update_layout(
            #title=f"Patent Tree over Time: {interleaving_type}",
            title=dict(
                text=f"Patent Tree over Time: {interleaving_type}",
                x=0.5,           # horizontally centered
                xanchor='center',
            ),            
            margin=dict(t=60, l=20, r=20, b=100),  # more bottom margin for the slider
            autosize=True,       # fill whatever container width is given
            width=None,          # don't hardcode — patent_processor sets this
            # width=1200,
            height=900,
            uirevision="persist",
            transition={"duration": 400, "easing": "cubic-in-out"},
            updatemenus=[{
                "type": "buttons",
                "x": -0.02, "xanchor": "left",   # centered horizontally
                "y": -0.08, "yanchor": "top",      # sit just below the chart domain                
                "buttons": [
                    {"label": "▶ Play", "method": "animate",
                     "args": [None, {"frame": {"duration": 1000, "redraw": True},
                                     "fromcurrent": True, "transition": {"duration": 500}}]},
                    {"label": "⏸ Pause", "method": "animate",
                     "args": [[None], {"frame": {"duration": 0, "redraw": False},
                                       "mode": "immediate"}]}
                ]
            }],
            sliders=[{
                "x": 0.18,  "xanchor": "left",    # aligned to chart left edge
                "y": -0.06, "yanchor": "top",     # below the buttons
                "len": 0.80,                      # nearly full width                
                "currentvalue": {
                    "prefix": "Date: ",
                    "visible": True,
                    "xanchor": "center",
                    "font": {"size": 12},
                },                
                "steps": [
                    {"args": [[str(t.date() if hasattr(t, "date") else t)],
                              {"frame": {"duration": 0, "redraw": True},
                               "mode": "immediate"}],
                     "label": str(t.date() if hasattr(t, "date") else t),
                     "method": "animate"}
                    for t in timestamps
                ]
            }]
        )

        # --- Output ---
        if output_path is None:
            output_path = os.path.join(self.workdir, "animated_tree.html")

        fig.write_html(output_path, include_plotlyjs='cdn')
        self.append_sticky_popup_script(output_path)
        # print(f"✅ Animated sunburst saved to: {output_path}")
        return fig, output_path

    def plot_sunburst_for_widget(self, df, interleaving_type, reference_mode, output_path=None, time_col="event_date"):
        """
        Like plot_sunburst_over_time, but returns the figure WITHOUT built-in Plotly
        animation controls (no updatemenus, no sliders). The caller (patent_processor)
        adds real ipywidgets Play/Pause/Slider controls that live inside the HBox frame.

        Returns
        -------
        fig          : go.Figure  (with frames, no Plotly controls)
        timestamps   : list of pd.Timestamp
        df_prepared  : pd.DataFrame with uid/parent_uid/color/… columns ready for reuse
        output_path  : str
        hovertemplate: str
        """
        df = self._prepare_sunburst_df(df, interleaving_type, reference_mode)
        if time_col not in df.columns:
            fig, path = self.plot_sunburst(df, interleaving_type, reference_mode, output_path)
            return fig, [], df, path, ""

        df = df.copy()
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        # Structural rows (Applications, Parents, etc.) have NaT event_date.
        # Fill with earliest known event date so they always appear as parent anchors
        # in every animation frame, preventing their legal-event children from being orphaned.
        earliest = df[time_col].dropna().min()
        if pd.isna(earliest):
            fig, path = self.plot_sunburst(df, interleaving_type, reference_mode, output_path)
            return fig, [], df, path, ""
        df[time_col] = df[time_col].fillna(earliest)

        # Deduplicate to one frame per calendar day — multiple events on the
        # same day produce identical-looking frames and inflate serialisation cost.
        df["_date_only"] = df[time_col].dt.normalize()
        timestamps = sorted(df["_date_only"].unique())

        if not timestamps:
            fig, path = self.plot_sunburst(df, interleaving_type, reference_mode, output_path)
            return fig, [], df, path, ""

        hovertemplate = "%{customdata[1]}<extra></extra>"
        if interleaving_type == "Images":
            hovertemplate = "<extra></extra>"  # custom JS handles display

        # --- Build frames ---
        # customdata + hovertemplate must be in every frame: Plotly sunburst does a
        # full data replacement per frame and does NOT retain the initial trace hover data.
        frames = []
        branchvals = 'remainder' if interleaving_type == "Priorities" else 'total'
        for t in timestamps:
            subset = df[df["_date_only"] <= t]
            frames.append(go.Frame(
                data=[go.Sunburst(
                    ids=subset["uid"],
                    labels=subset["formatted_id"],
                    parents=subset["parent_uid"],
                    values=subset["value"],
                    branchvalues=branchvals,
                    marker=dict(colors=subset["color"], line=dict(color='white', width=0.5)),
                    customdata=np.stack([subset["app_id"], subset["tooltip"], subset["hover_bg"]], axis=-1),
                    hovertemplate=hovertemplate,
                )],
                name=str(t.date() if hasattr(t, "date") else t)
            ))

        # --- Initial figure (NO updatemenus / sliders — those go in the widget layer) ---
        first_subset = df[df["_date_only"] <= timestamps[0]]
        fig = go.Figure(
            data=[go.Sunburst(
                ids=first_subset["uid"],
                labels=first_subset["formatted_id"],
                # text=first_subset["tooltip"],
                text=first_subset["formatted_id"],
                parents=first_subset["parent_uid"],
                values=first_subset["value"],
                # hovertext=first_subset["tooltip"],
                customdata=np.stack([first_subset["app_id"], first_subset["tooltip"], first_subset["hover_bg"]], axis=-1),
                hovertemplate=hovertemplate,
                branchvalues='remainder' if interleaving_type == "Priorities" else 'total',
                marker=dict(colors=first_subset["color"], line=dict(color='white', width=0.5)),
            )],
            frames=frames
        )

        fig.update_layout(
            title=dict(
                text=f"Patent Tree over Time: {interleaving_type}",
                x=0.5, xanchor="center",
            ),
            margin=dict(t=50, l=10, r=10, b=80),  # b=80 just enough for one control row
            autosize=True,
            height=820,
            uirevision="persist",
            transition={"duration": 400, "easing": "cubic-in-out"},
            showlegend=False,
            updatemenus=[{
                "type": "buttons",
                "x": 0.02, "xanchor": "left",
                "y": -0.06, "yanchor": "top",
                "direction": "right",
                "pad": {"r": 8, "t": 0},
                "buttons": [
                    {"label": "▶ Play", "method": "animate",
                     "args": [None, {"frame": {"duration": 1000, "redraw": True},
                                     "fromcurrent": True, "transition": {"duration": 500}}]},
                    {"label": "⏸ Pause", "method": "animate",
                     "args": [[None], {"frame": {"duration": 0, "redraw": False},
                                       "mode": "immediate"}]},
                ],
            }],
            sliders=[{
                "x": 0.18, "xanchor": "left",   # immediately right of buttons
                "y": -0.04, "yanchor": "top",    # same row as buttons
                "len": 0.80,
                "pad": {"t": 0, "b": 0},
                "currentvalue": {
                    "prefix": "Date: ",
                    "visible": True,
                    "xanchor": "center",
                    "font": {"size": 12, "color": "#333"},
                    "offset": 20,
                },
                "ticklen": 4,
                "minorticklen": 2,
                "steps": [
                    {"args": [[str(t.date() if hasattr(t, "date") else t)],
                              {"frame": {"duration": 0, "redraw": True},
                               "mode": "immediate"}],
                     "label": str(t.date() if hasattr(t, "date") else t),
                     "method": "animate"}
                    for t in timestamps
                ],
            }],
        )

        # Save HTML with fixed size (looks good when opened externally)
        if output_path is None:
            output_path = os.path.join(self.workdir, "animated_tree.html")
        fig.update_layout(autosize=False, width=1200, height=900)
        fig.write_html(output_path, include_plotlyjs='cdn')
        self.append_sticky_popup_script(output_path)
        fig.update_layout(autosize=True, width=None, height=820)  # restore for widget

        return fig, timestamps, df, output_path, hovertemplate

    def plot_multiple_sunbursts(self, dfs, interleaving_types, reference_mode, output_path=None):
        """
        Plot multiple Sunburst trees in a single figure.
    
        Parameters
        ----------
        tree_dfs : list of pd.DataFrame
            List of DataFrames, one per tree.
        interleaving_types : list of str
            List of interleaving_type strings, one per tree.
        output_path : str, optional
            Path to save HTML output. If None, uses default.
        """

        n = len(dfs)
        if n == 0:
            print("No dataframes provided.")
            return None, None
            
        cols = min(n, 3)  # max 3 trees per row
        rows = (n + cols - 1) // cols
        
        # vertical_spacing is a fraction of the total figure height allocated between rows.
        # Domain-type subplots (sunbursts) don't auto-shrink, so we tighten it manually.
        # For a single row there is no gap to worry about.
        v_spacing = 0.02 if rows > 1 else 0.0

        fig = make_subplots(
            rows=rows, cols=cols,
            specs=[[{'type': 'domain'} for _ in range(cols)] for _ in range(rows)],
            subplot_titles=interleaving_types,
            vertical_spacing=v_spacing,
        )
        
        for i, (df, interleaving_type) in enumerate(zip(dfs, interleaving_types)):
            if df is None or not hasattr(df, "empty") or df.empty:
                continue

            df = self._prepare_sunburst_df(df, interleaving_type, reference_mode)

            # Hovertemplate switch
            hovertemplate = "%{customdata[1]}<extra></extra>"
            if interleaving_type == "Images":
                hovertemplate = "<extra></extra>"  # custom JS handles display
            
            row = i // cols + 1
            col = i % cols + 1

            # Ensure numeric values
            df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(1)

            fig.add_trace(
                go.Sunburst(
                    ids=df['uid'],
                    labels=df['formatted_id'],
                    text=df['formatted_id'],
                    parents=df['parent_uid'],
                    values=df['value'],
                    hoverinfo='none',
                    branchvalues='remainder' if interleaving_type == "Priorities" else 'total',
                    marker=dict(colors=df['color'], line=dict(color='white', width=0.5))
                ), 
                row=row, 
                col=col
            )
    
        fig.update_traces(
            textinfo='text',
            textfont_size=12,
            hoverinfo='none',
        )
    
        # 420 px per row is enough for sunburst charts; the tighter vertical_spacing
        # above removes the bulk of the blank band between rows.
        fig.update_layout(
            width=500 * cols,
            height=420 * rows,
            showlegend=False,
            title_text="Patent Trees",
            margin=dict(t=60, b=20, l=20, r=20),
        )
    
        if output_path is None:
            output_path = "filtered_multi_tree.html"
    
        fig.write_html(output_path, include_plotlyjs='cdn')
        self.append_sticky_popup_script(output_path)
        return fig, output_path
            
    def append_image_hover_script(self, html_path):
        custom_hover_script = """
        <style>
        #hover-img-preview {
            /* position:fixed keeps the popup in viewport coordinates so it
               follows the cursor correctly even inside the TIP iframe.       */
            position: fixed;
            display: none;
            border: 2px solid #888;
            border-radius: 6px;
            background: white;
            padding: 4px;
            z-index: 9999;
            box-shadow: 0 4px 16px rgba(0,0,0,0.25);
            pointer-events: none;   /* don't steal mouse events from the chart */
            max-width: 440px;
        }
        #hover-img-preview img {
            display: block;
            max-width: 420px;
            max-height: 560px;
            object-fit: contain;
        }
        #hover-img-caption {
            margin-top: 5px;
            padding: 3px 4px 2px 4px;
            font-size: 11px;
            font-family: sans-serif;
            color: #333;
            border-top: 1px solid #ddd;
            white-space: nowrap;
        }
        #hover-img-caption span {
            font-weight: bold;
        }
        </style>
        <div id="hover-img-preview">
            <img id="hover-img" src="">
            <div id="hover-img-caption"><span>First page image published in:</span> <span id="hover-pub-number"></span></div>
        </div>
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            var hoverBox    = document.getElementById('hover-img-preview');
            var hoverImg    = document.getElementById('hover-img');
            var hoverPubNum = document.getElementById('hover-pub-number');
            var myPlot      = document.querySelector('.js-plotly-plot');

            function extractSrc(tooltip) {
                var m = tooltip.match(/src=["']([^"']+)["']/);
                return m ? m[1] : null;
            }

            function extractPub(tooltip) {
                // pub number is embedded as data-pub="EP2180717B1" in the <img> tag
                var m = tooltip.match(/data-pub=["']([^"']+)["']/);
                return m ? m[1] : '';
            }

            function positionBox(clientX, clientY) {
                var vw = window.innerWidth  || document.documentElement.clientWidth;
                var vh = window.innerHeight || document.documentElement.clientHeight;
                var boxW = hoverBox.offsetWidth  || 440;
                var boxH = hoverBox.offsetHeight || 400;

                // Default placement: lower-right of cursor
                var left = clientX + 16;
                var top  = clientY + 16;

                // Avoid the sticky DiviTree action panel: when it is visible
                // (especially when docked to an edge), shift the hover preview
                // to the opposite side so it does not get covered.
                var sticky = document.getElementById('dt-sticky-popup');
                if (sticky && sticky.style.display !== 'none' && sticky.offsetWidth > 0) {
                    var sr = sticky.getBoundingClientRect();
                    // Test whether default placement would intersect the sticky
                    var defR = { left: left, top: top,
                                 right: left + boxW, bottom: top + boxH };
                    var intersects = !(defR.right  < sr.left  ||
                                       defR.left   > sr.right ||
                                       defR.bottom < sr.top   ||
                                       defR.top    > sr.bottom);
                    if (intersects) {
                        // Pick the side with more horizontal room outside the sticky
                        var roomRight = vw - sr.right;
                        var roomLeft  = sr.left;
                        if (roomRight >= boxW + 10 && roomRight >= roomLeft) {
                            left = sr.right + 10;
                        } else if (roomLeft >= boxW + 10) {
                            left = sr.left - boxW - 10;
                        }
                        // else fall through with default left
                    }
                }

                // Final clamp to viewport (with 10px margin)
                left = Math.min(left, vw - boxW - 10);
                top  = Math.min(top,  vh - boxH - 10);
                hoverBox.style.left = Math.max(10, left) + 'px';
                hoverBox.style.top  = Math.max(10, top)  + 'px';
            }

            if (myPlot) {
                myPlot.on('plotly_hover', function(data) {
                    var tooltip = (data.points[0].customdata && data.points[0].customdata[1]) || '';
                    var src = extractSrc(tooltip);
                    var pubNum = extractPub(tooltip);
                    if (src) {
                        if (hoverImg.getAttribute('data-src') !== src) {
                            hoverImg.setAttribute('data-src', src);
                            hoverImg.src = src;
                        }
                        hoverPubNum.textContent = pubNum;
                        hoverBox.style.display = 'block';
                        positionBox(data.event.clientX, data.event.clientY);
                    }
                });
                myPlot.on('plotly_unhover', function() {
                    hoverBox.style.display = 'none';
                });
                // Keep popup tracking the mouse after initial hover
                document.addEventListener('mousemove', function(e) {
                    if (hoverBox.style.display === 'block') {
                        positionBox(e.clientX, e.clientY);
                    }
                });
            }
        });
        </script>
        """
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        if '</body>' in html_content:
            html_content = html_content.replace('</body>', custom_hover_script + '</body>')
        else:
            html_content += custom_hover_script
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

    def _sticky_popup_script_content(self):
        """Return the sticky popup HTML+JS as a string.
        Callers that embed the chart as an HTML fragment (e.g. fig.to_html)
        can append this string directly to their fragment.
        """
        script = r"""
<style>
#dt-sticky-popup {
    position: fixed;
    display: none;          /* toggled to flex by JS */
    flex-direction: column; /* drag-handle → body → hint stacked vertically */
    min-width: 200px;       /* reduced — content sets natural width */
    max-width: 480px;
    /* max-height and overflow-y live on .dt-body, not here */
    background: #fffff8;
    border: 2px solid #888;
    border-radius: 8px;
    padding: 8px 10px 10px 10px;  /* tighter, symmetric */
    z-index: 10000;
    box-shadow: 0 6px 24px rgba(0,0,0,0.28);
    font-family: sans-serif;
    font-size: 12.5px;
    line-height: 1.5;
    cursor: default;
}
#dt-sticky-popup .dt-close {
    position: absolute;
    top: 5px; right: 9px;
    font-size: 15px;
    cursor: pointer;
    color: #666;
    user-select: none;
}
#dt-sticky-popup .dt-close:hover { color: #c00; }
/* Dock-to-edge buttons — snap the popup to the left or right edge of the
   viewport so it sits in the textual-tree pane and frees the chart pane.
   ⇤ docks to left edge, ⇥ docks to right edge. Current top is preserved. */
#dt-sticky-popup .dt-snap {
    position: absolute;
    top: 5px;
    font-size: 14px;
    cursor: pointer;
    color: #666;
    user-select: none;
    width: 16px;
    text-align: center;
    line-height: 1;
}
#dt-sticky-popup .dt-snap:hover { color: #06c; }
#dt-sticky-popup .dt-snap.active {
    color: #06c;
    background: #e3edff;
    border-radius: 3px;
}
#dt-sticky-popup .dt-snap-left  { right: 50px; }
#dt-sticky-popup .dt-snap-right { right: 28px; }
#dt-sticky-popup .dt-body {
    overflow-y: auto;
    max-height: 60vh;
    padding-bottom: 10px;
    scrollbar-width: thin;
    scrollbar-color: #aaa #f0f0f0;
}
#dt-sticky-popup .dt-body::-webkit-scrollbar { width: 6px; }
#dt-sticky-popup .dt-body::-webkit-scrollbar-track { background: #f0f0f0; }
#dt-sticky-popup .dt-body::-webkit-scrollbar-thumb { background: #aaa; border-radius: 3px; }
#dt-sticky-popup .dt-scroll-hint {
    display: none;
    text-align: center;
    font-size: 10px;
    color: #888;
    padding: 3px 0 6px;
    border-top: 1px solid #eee;
    background: #fffff8;
}
#dt-sticky-popup .dt-content { margin-top: 4px; text-align: left; }
#dt-sticky-popup .dt-actions {
    margin-top: 10px;
    border-top: 1px solid #ddd;
    padding-top: 8px;
    display: flex;
    flex-direction: column;  /* buttons stack vertically → narrower popup */
    gap: 5px;
}
#dt-sticky-popup .dt-btn {
    display: inline-block;
    align-self: flex-start;  /* don't stretch to full column width */
    padding: 3px 9px;
    border-radius: 4px;
    border: 1px solid #aaa;
    background: #f0f0f0;
    color: #1a1a6e;
    font-size: 11.5px;
    cursor: pointer;
    text-decoration: none;
    white-space: nowrap;
}
#dt-sticky-popup .dt-btn:hover { background: #d0d8ff; border-color: #668; }
/* Presearch helper buttons (XFR-Pivot, CLR, CTR, Inventor/Applicant search)
   share a single green palette so the user instantly recognises any of them
   as a step that builds an Espacenet presearch query from the family data. */
#dt-sticky-popup .dt-btn.concept,
#dt-sticky-popup .dt-btn.cpc,
#dt-sticky-popup .dt-btn.ct,
#dt-sticky-popup .dt-btn.presearch { background: #e8f5e9; border-color: #2e7d32; color: #1b5e20; }
#dt-sticky-popup .dt-btn.concept:hover,
#dt-sticky-popup .dt-btn.cpc:hover,
#dt-sticky-popup .dt-btn.ct:hover,
#dt-sticky-popup .dt-btn.presearch:hover { background: #c8e6c9; }
#dt-sticky-popup .dt-btn.xp { background: #fff3e0; border-color: #e65100; color: #bf360c; }
#dt-sticky-popup .dt-btn.xp:hover { background: #ffe0b2; }
#dt-sticky-popup .dt-group-label {
    width: 100%;
    font-size: 10.5px;
    font-weight: bold;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-top: 4px;
    margin-bottom: 1px;
    padding-top: 4px;
    border-top: 1px dashed #ccc;
}
#dt-sticky-popup .dt-group-label:first-child { border-top: none; margin-top: 0; padding-top: 0; }
#dt-sticky-popup .dt-drag-handle {
    display: block;         /* full-width row in column flex container */
    cursor: move;
    font-weight: bold;
    font-size: 11px;
    color: #555;
    letter-spacing: 0.04em;
    padding-bottom: 4px;
    padding-right: 70px;    /* room for ⇤ ⇥ ✕ buttons */
    border-bottom: 1px solid #ddd;
    margin-bottom: 6px;
    user-select: none;
}
/* Resize handle in the bottom-right corner — drag to enlarge the popup
   like a window. Diagonal stripe pattern signals the resize affordance. */
#dt-sticky-popup .dt-resize-handle {
    position: absolute;
    bottom: 0;
    right: 0;
    width: 16px;
    height: 16px;
    cursor: nwse-resize;
    background:
        linear-gradient(135deg,
            transparent 0%, transparent 40%,
            #888 40%, #888 50%,
            transparent 50%, transparent 65%,
            #888 65%, #888 75%,
            transparent 75%);
    opacity: 0.55;
    user-select: none;
    z-index: 1;
    border-bottom-right-radius: 6px;
}
#dt-sticky-popup .dt-resize-handle:hover { opacity: 1; }
/* When the popup has been resized, an inline 'data-resized' attribute is
   set; the body then fills available height via flex instead of using
   the default max-height: 60vh cap. */
#dt-sticky-popup[data-resized="1"] .dt-body {
    flex: 1 1 auto;
    max-height: none;
}
</style>

<div id="dt-sticky-popup">
  <span class="dt-drag-handle">⠿ DiviTree action panel</span>
  <span class="dt-snap dt-snap-left"  title="Dock to left edge (free chart pane)">⇤</span>
  <span class="dt-snap dt-snap-right" title="Dock to right edge">⇥</span>
  <span class="dt-close" title="Close">✕</span>
  <div class="dt-body" id="dt-body">
    <div class="dt-content" id="dt-sticky-content"></div>
    <div class="dt-actions" id="dt-sticky-actions"></div>
  </div>
  <div class="dt-scroll-hint" id="dt-scroll-hint">▼ scroll for more buttons</div>
  <div class="dt-resize-handle" title="Drag to resize"></div>
</div>

<script>
(function() {
  // ── Helpers ────────────────────────────────────────────────────────────────

  // Strip HTML tags, return plain text
  function stripHtml(html) {
    var d = document.createElement('div');
    d.innerHTML = html;
    return d.textContent || d.innerText || '';
  }

  // Classify a token as patent number, XP number, or neither.
  // Returns: { type: 'patent'|'xp'|null, value: cleaned string }
  function classifyNumber(token) {
    var t = token.replace(/\*$/, '').trim();
    if (/^XP\d{1,9}$/i.test(t)) return { type: 'xp',     value: t.toUpperCase() };
    if (/^[A-Z]{2,3}\d{4,}[A-Z0-9]*$/.test(t)) return { type: 'patent', value: t };
    return null;
  }

  // Extract publication/application numbers AND XP numbers from tooltip text.
  // Returns array of objects: { type: 'patent'|'xp', value: string }
  function extractNumbers(text) {
    var seen = {};
    var nums = [];
    // Match CC+digits (patents) and XP+digits (NPL citations)
    var re = /\b(XP\d{1,9}|[A-Z]{2,3}\d{4,}[A-Z0-9]*\*?)\b/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      var info = classifyNumber(m[1]);
      if (info && !seen[info.value]) {
        seen[info.value] = true;
        nums.push(info);
      }
    }
    return nums;
  }

  // Extract ALL concept phrases from tooltip bullet lines.
  // Captures both NEAR-joined multi-word phrases and single-word concepts.
  // Looks for lines starting with '•' or '·' under a 'Concepts:' header.
  function extractConcepts(html) {
    var concepts = [];
    var seen = {};
    // Match bullet lines including span-wrapped (green/bold) text.
    // The inner group captures plain text OR HTML tags so that
    // green-coloured concepts like <span>+manage* NEAR inform*...</span>
    // are not lost when the regex hits the opening '<'.
    var re = /[•·]\s*((?:[^<\n•·]|<[^>]+>)+)/g;
    var m;
    while ((m = re.exec(html)) !== null) {
      var phrase = m[1].replace(/<[^>]+>/g, '').trim();
      if (!phrase) continue;
      // Skip pub numbers, XP numbers, CPC/IPC symbols, and cpc=... display strings
      if (/^[A-Z]{2,3}\d{4,}/.test(phrase)) continue;
      if (/^XP\d{1,9}/i.test(phrase)) continue;
      if (/^[A-Z]\d{2}[A-Z]/.test(phrase)) continue;    // CPC/IPC: G11B, H04N…
      if (/^cpc=/i.test(phrase)) continue;               // cpc=symbol display
      if (!seen[phrase]) { seen[phrase] = true; concepts.push(phrase); }
    }
    return concepts;
  }

  // Convert one concept phrase to Espacenet field= syntax.
  // Handles 3+ token chains by splitting into overlapping pairs,
  // matching Python's split_concept_to_pairs for old cached HTML.
  function _conceptToField(phrase, field) {
    var p = phrase.replace(/^\+/, '').trim();
    var tokens = p.split(/\s+NEAR\s+/i).map(function(t) { return t.trim(); });
    if (tokens.length === 1) {
      return field + ' all "' + tokens[0] + '"';
    }
    if (tokens.length === 2) {
      return field + '=("' + tokens[0] + '" prox/distance<=3 "' + tokens[1] + '")';
    }
    // 3+ tokens: generate overlapping adjacent pairs joined with AND
    var pairs = [];
    for (var i = 0; i < tokens.length - 1; i++) {
      pairs.push(field + '=("' + tokens[i] + '" prox/distance<=3 "' + tokens[i+1] + '")');
    }
    return pairs.join(' AND ');
  }
  // claims= field (Claim 1 text)
  function conceptToClaims(phrase) { return _conceptToField(phrase, 'claims'); }
  // nftxt= field (full text)
  function conceptToNftxt(phrase)  { return _conceptToField(phrase, 'nftxt');  }

  // Build Espacenet search URL using claims= field.
  // Extract CPC subgroup symbols (e.g. "G11B20/00007", "H04N13/144") from a
  // tooltip for use in XFR-Pivot Ranking. Reads data-pivot-cpc first (the
  // dedicated attribute for non-Classifications nodes), falls back to
  // data-cpc. Returns a list of unique subgroups in document order.
  //
  // We preserve the full subgroup symbol (including the part after '/')
  // rather than collapsing to the main group, because Espacenet matches
  // cpc="..." against the indexed subgroup directly: full subgroups give
  // a more precise pivot anchor and let Espacenet do the look-up faster.
  function _extractPivotCpcs(tooltipHtml) {
    if (!tooltipHtml) return [];
    var txt = tooltipHtml
      .replace(/&quot;/g, '"').replace(/&#39;/g,  "'")
      .replace(/&lt;/g,   '<').replace(/&gt;/g,   '>')
      .replace(/&amp;/g,  '&');
    var m = txt.match(/data-pivot-cpc\s*=\s*["']([^"']+)["']/i);
    if (!m) m = txt.match(/data-cpc\s*=\s*["']([^"']+)["']/i);
    if (!m || !m[1]) return [];

    // Validate full CPC subgroup shape:
    //   Section letter + 2 digit class + subclass letter + group digits
    //   + '/' + subgroup digits (e.g. G11B20/10527, H04N13/144).
    // Accept just the main group (no slash) as a fallback for legacy data.
    var validSubgroup = /^[A-Z]\d{2}[A-Z]\d+\/\d+$/;
    var validMainGrp  = /^[A-Z]\d{2}[A-Z]\d+$/;

    var seen = {};
    var out  = [];
    m[1].split(',').forEach(function(sym) {
      sym = sym.trim().replace(/^\+/, '').replace(/\s+/g, '');
      if (!sym) return;
      if (!validSubgroup.test(sym) && !validMainGrp.test(sym)) return;
      if (seen[sym]) return;
      seen[sym] = true;
      out.push(sym);
    });
    return out;
  }

  // Build (cpc="X" OR cpc="Y" OR ...) AND (claims/nftxt OR-block) for XFR
  // Pivot Ranking. Concepts are OR-joined inside parentheses; CPC subgroups
  // (read via _extractPivotCpcs) are OR-joined and AND-prefixed to the block.
  // Using full subgroups (e.g. "G11B20/10527") rather than main groups gives
  // a more precise anchor and lets Espacenet match the indexed CPC field
  // directly without expanding the main group's siblings.
  // When no CPC anchor is available the URL contains only the OR-block.
  function _buildPivotUrl(concepts, field, tooltipHtml) {
    if (!concepts.length) return null;
    var parts = concepts.map(function(c) { return _conceptToField(c, field); });
    var orBlock = '(' + parts.join(' OR ') + ')';
    var cpcs = _extractPivotCpcs(tooltipHtml || '');
    var q;
    if (cpcs.length) {
      var cpcBlock = '(' + cpcs.map(function(g) { return 'cpc="' + g + '"'; }).join(' OR ') + ')';
      q = cpcBlock + ' AND ' + orBlock;
    } else {
      q = orBlock;
    }
    return 'https://worldwide.espacenet.com/patent/search?q=' + encodeURIComponent(q);
  }

  function buildEspacenetClaimsUrl(concepts, tooltipHtml) {
    return _buildPivotUrl(concepts, 'claims', tooltipHtml);
  }
  function buildEspacenetNftxtUrl(concepts, tooltipHtml) {
    return _buildPivotUrl(concepts, 'nftxt', tooltipHtml);
  }

  // Extract IPC/CPC classification symbols.
  // Prefers the hidden data-cpc attribute (all symbols, set by Python)
  // over parsing visible text (which is capped at 20 for hover tooltips).
  function extractClassifications(text, tooltipHtml) {
    // Try hidden span first
    if (tooltipHtml) {
      var dm = tooltipHtml.match(/data-cpc='([^']*)'/);
      if (dm && dm[1]) {
        return dm[1].split(',').map(function(s){ return s.trim().replace(/^\+/, ''); })
                    .filter(function(s){ return /^[A-Z]\d/.test(s); });
      }
    }
    // Fallback: regex on visible plain text
    var seen = {}, syms = [];
    var re = /\b([A-Z]\d{2}[A-Z]\d+(?:\/\d+)?)\b/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (!seen[m[1]]) { seen[m[1]] = true; syms.push(m[1]); }
    }
    return syms;
  }

  // Build Espacenet CPC OR-query URL.
  // Uses cpc=SYMBOL OR cpc=SYMBOL ... so Espacenet ranks by best CPC match.
  // Capped at 20 symbols to stay within Espacenet's 20-term limit.
  function buildCpcUrl(syms) {
    if (!syms.length) return null;
    var parts = syms.map(function(s) { return 'cpc=' + s; });
    return 'https://worldwide.espacenet.com/patent/search?q=' +
           encodeURIComponent(parts.join(' OR '));
  }

  // Build Espacenet citation-ranking OR-query URL.
  // Uses ct=NUM OR ct=NUM ... so Espacenet ranks by best citation overlap.
  // Accepts both PL (patent) and NPL (XP) numbers; ct= works for both.
  function buildCtrUrl(citations) {
    if (!citations.length) return null;
    var parts = citations.map(function(c) { return 'ct=' + c; });
    return 'https://worldwide.espacenet.com/patent/search?q=' +
           encodeURIComponent(parts.join(' OR '));
  }

  // Multilingual list of corporate / personal-title stopwords used to derive
  // a "core" applicant name for deduplication. PANASONIC CORP and PANASONIC
  // CORPORATION both reduce to PANASONIC, so they collapse to one pa= clause
  // in the Espacenet query. Tokens are upper-case and matched as whole words.
  var _PA_STOPWORDS = {
    'AB':1, 'ACTIEN':1, 'ACTIENGESELLSCHAFT':1, 'AG':1, 'AKTIEBOLAG':1,
    'AKTIEBOLAGET':1, 'AKTIEN':1, 'AKTIENGESELLSCHAFT':1, 'ANONIM':1,
    'ANONYME':1, 'ASSOCIATES':1, 'BESLOTEN':1, 'BV':1, 'CIE':1, 'CO':1,
    'COMPAGNIE':1, 'COMPANY':1, 'CORP':1, 'CORPORATION':1, 'DES':1,
    'DIPL':1, 'DITE':1, 'DR':1, 'EI':1, 'ENTERPRISE':1, 'ETABLISSEMENT':1,
    'FABRIK':1, 'FUR':1, 'GENERALE':1, 'GESELLSCHAFT':1, 'GLE':1, 'GMBH':1,
    'HOLDING':1, 'INC':1, 'INCORPORATED':1, 'IND':1, 'INDLE':1,
    'INDUSTRIAL':1, 'INDUSTRIE':1, 'INDUSTRIELLE':1, 'ING':1,
    'INTERNATIONAL':1, 'KABUSHIKI':1, 'KAISHA':1, 'KG':1, 'KK':1,
    'KOMMANDIT':1, 'KOMMANDITGESELLSCHAFT':1, 'LIMITED':1, 'LLC':1, 'LM':1,
    'LTD':1, 'LTDA':1, 'NAAMLOZE':1, 'NATIONALE':1, 'NLE':1, 'NV':1, 'OF':1,
    'OY':1, 'PLC':1, 'PROF':1, 'PTY':1, 'PUBL':1, 'RESEARCH':1, 'SA':1,
    'SARL':1, 'SAS':1, 'SC':1, 'SE':1, 'SIRKETI':1, 'SOCIETE':1, 'SPA':1,
    'SRL':1, 'STE':1, 'THE':1, 'UND':1, 'VENNOOTSCHAP':1
  };
  // Reduce a name to its core (stopwords removed) for dedup.
  // Splits on non-letters/digits so punctuation doesn't matter; single-letter
  // tokens are also dropped to handle dotted forms like "G.m.b.H." which
  // would otherwise survive as "G M B H".
  function _coreApplicantKey(name) {
    var toks = name.toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean);
    var core = toks.filter(function(t){
      return t.length > 1 && !_PA_STOPWORDS[t];
    });
    return core.length ? core.join(' ') : name.toUpperCase();
  }

  // Detect a corporate / institutional name: any token in the multilingual
  // stopwords set (CORP, CORPORATION, GMBH, KAISHA, LLC, SOCIETE, ...) marks
  // the name as corporate. Token-based detection catches both abbreviated
  // forms (PANASONIC CORP) and full forms (PANASONIC CORPORATION) and avoids
  // word-boundary regex traps like /\bINC\b/ failing to match "INCORPORATED".
  function _isCorporateName(name) {
    var toks = name.toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean);
    for (var i = 0; i < toks.length; i++) {
      if (_PA_STOPWORDS[toks[i]]) return true;
    }
    return false;
  }

  // Extract party names from a Parties tooltip and build an Espacenet
  // inventor/applicant OR-query URL.
  // Company names (any stopword token: CORP, CORPORATION, GMBH, KAISHA,
  // LLC, ...) → pa=  |  personal names → in=
  // Duplicate names (same name ±[CC], or same "core" after stopword
  // stripping — e.g. PANASONIC CORP vs PANASONIC CORPORATION) collapse
  // to a single entry. The shortest surviving form wins because Espacenet
  // matches pa= as a substring, so the shorter form catches more variants.
  function buildPartiesUrl(tooltipHtml) {
    var re = /[•·]\s*((?:[^<\n•·]|<[^>]+>)+)/g;
    var m, seenRaw = {}, byCorePa = {}, byCoreIn = {}, pas = [], ins = [];
    while ((m = re.exec(tooltipHtml)) !== null) {
      var raw  = m[1].replace(/<[^>]+>/g, '').trim();
      // Strip a leading '+' that marks "new in this branch" — it is a visual
      // marker only, not part of the name. Espacenet rejects '+' inside pa=
      // and in= field values with: "Invalid query: character is not allowed: +".
      raw = raw.replace(/^\+\s*/, '');
      // Strip trailing [CC] country code before dedup and field assignment
      var name = raw.replace(/\s*\[[A-Z]{2}\]\s*$/, '').trim();
      if (!name || seenRaw[name]) continue;
      seenRaw[name] = true;
      var isCorp = _isCorporateName(name);
      var bucket = isCorp ? byCorePa : byCoreIn;
      var key = isCorp ? _coreApplicantKey(name) : name.toUpperCase();
      var prev = bucket[key];
      // Keep the shortest variant — pa="PANASONIC" matches both
      // "PANASONIC CORP" and "PANASONIC CORPORATION" because Espacenet
      // treats pa= as a token-prefix match.
      if (!prev || name.length < prev.length) bucket[key] = name;
    }
    // Materialise the deduplicated lists in stable order. For corporate
    // applicants we emit the stopword-stripped CORE form (the bucket key)
    // rather than the original variant: pa="MATSUSHITA ELECTRIC" instead
    // of pa="MATSUSHITA ELECTRIC INDUSTRIAL CO LTD". Espacenet's pa= field
    // matches as a token-prefix, so the cleaned form catches every legal
    // variant (CORP, CORPORATION, INC, KAISHA, GMBH, ...) without needing
    // to enumerate them. This both broadens recall and shortens the URL.
    Object.keys(byCorePa).forEach(function(k){ pas.push(k); });
    Object.keys(byCoreIn).forEach(function(k){ ins.push(byCoreIn[k]); });
    if (!pas.length && !ins.length) return null;
    // Build the query in two AND-combined groups:
    //   • applicants  → pa="A"            (single applicant, no parens)
    //                   (pa="A" OR pa="B") (multiple applicants, OR-combined)
    //   • inventors   → in="X"            (single inventor)
    //                   (in="X" OR in="Y" OR in="Z") (multiple inventors)
    // Multiple applicants are OR-combined, NOT AND-combined: a divisional
    // family member that is filed under only one of the corporate entities
    // (e.g. only "PANASONIC CORP" without "MATSUSHITA ELECTRIC...") would
    // be excluded by AND-combination, which is far too restrictive for the
    // "everything related to this family's parties" presearch intent.
    var parts = [];
    if (pas.length === 1) {
      parts.push('pa="' + pas[0] + '"');
    } else if (pas.length > 1) {
      parts.push('(' + pas.map(function(n){ return 'pa="'+n+'"'; }).join(' OR ') + ')');
    }
    if (ins.length === 1) {
      parts.push('in="' + ins[0] + '"');
    } else if (ins.length > 1) {
      parts.push('(' + ins.map(function(n){ return 'in="'+n+'"'; }).join(' OR ') + ')');
    }
    var q = parts.join(' AND ');
    return 'https://worldwide.espacenet.com/patent/search?q=' + encodeURIComponent(q);
  }

  // Build Espacenet URL for a patent publication/application number
  function espacenetPubUrl(num) {
    return 'https://worldwide.espacenet.com/patent/search?q=' + encodeURIComponent(num);
  }

  // Build Espacenet URL using the ct= field — finds patents citing the given
  // document. Works for both patent literature (PL) and non-patent literature
  // (NPL, e.g. XP numbers). In the new Espacenet interface, NPL numbers cannot
  // be searched directly; they must be searched as cited documents via ct=.
  function espacenetCtUrl(num) {
    return 'https://worldwide.espacenet.com/patent/search?q=' +
           encodeURIComponent('ct=' + num);
  }

  // Backward-compat alias for XP NPL numbers (same URL builder).
  function espacenetXpUrl(xpNum) { return espacenetCtUrl(xpNum); }

  // Build EP Register URLs for a publication number (EP only).
  // Returns null for non-EP numbers.
  // Build EP Register URLs.
  // Reads data-appid embedded by Python in the tooltip <b> tag first —
  // that is the real EP application number (e.g. EP2019188854).
  // Falls back to parsing pub-header text for a number without kind code,
  // then to a publication number with kind code.
  function buildEpRegisterUrls(tooltipHtml) {
    // EPO Register uses the SHORT application number format:
    // EP20YYNNNNN (long/OPS) → EPYYNNNNN (short/Register), i.e. strip '20'.
    // All URLs include &lng=en for English language.
    function toRegNum(raw) {
      return raw.replace(/^EP20/i, 'EP');  // EP2019188854 → EP19188854
    }
    function makeUrls(num, endpoint) {
      var base = 'https://register.epo.org/' + endpoint
               + '?number=' + num + '&lng=en';
      return { about:     base + '&tab=main',
               legal:     base + '&tab=legal',
               federated: base + '&tab=federated',
               event:     base + '&tab=event',
               citations: base + '&tab=citations',
               family:    base + '&tab=family',
               alldocs:   base + '&tab=doclist',
               ueMain:    base + '&tab=ueMain',
               ueEvent:   base + '&tab=ueEvent',
               ueDoclist: base + '&tab=ueDoclist' };
    }
    // 1. Prefer data-appid embedded by Python
    var mAppid = tooltipHtml.match(/data-appid='([^']+)'/);
    if (mAppid) {
      var raw = mAppid[1].trim();
      var mParse = raw.match(/^(EP\d+)([A-Z]\d?)?$/i);
      if (mParse) {
        var endpoint = mParse[2] ? 'publication' : 'application';
        var num = mParse[2] ? mParse[1]           // pub: strip kind code only
                            : toRegNum(mParse[1]); // app: also strip century '20'
        return makeUrls(num, endpoint);
      }
    }
    // 2. Fallback: parse header text
    var headerText = tooltipHtml.replace(/<[^>]+>/g, ' ');
    var re = /\bEP(\d+)([A-Z]\d?)?\b/gi;
    var m, appNum = null, pubNum = null;
    while ((m = re.exec(headerText)) !== null) {
      if (!m[2]) { appNum = 'EP' + m[1]; }
      else       { pubNum = 'EP' + m[1]; }
    }
    var num2 = appNum || pubNum;
    if (!num2) return null;
    var ep2 = appNum ? 'application' : 'publication';
    var finalNum = appNum ? toRegNum(appNum) : pubNum;
    return makeUrls(finalNum, ep2);
  }

  // ── Popup DOM refs ─────────────────────────────────────────────────────────
  var popup   = document.getElementById('dt-sticky-popup');
  var content = document.getElementById('dt-sticky-content');
  var actions = document.getElementById('dt-sticky-actions');
  var closeBtn = popup.querySelector('.dt-close');
  var handle   = popup.querySelector('.dt-drag-handle');
  var resizeHandle = popup.querySelector('.dt-resize-handle');
  var snapLeft  = popup.querySelector('.dt-snap-left');
  var snapRight = popup.querySelector('.dt-snap-right');

  function hidePopup() { popup.style.display = 'none'; }

  closeBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    hidePopup();
  });

  // Dock-to-edge: snap the popup horizontally and PIN it there. Once docked,
  // the popup keeps its position when the user clicks a new arc — only its
  // content updates. Manual drag undocks it (returns to follow-cursor mode).
  // dockedSide: null = free / follow cursor, 'left' = pinned left,
  // 'right' = pinned right. Visual state is reflected via CSS class.
  var dockedSide = null;

  function setDocked(side) {
    dockedSide = side;
    // Visual highlighting of the active dock button
    snapLeft.classList.toggle('active',  side === 'left');
    snapRight.classList.toggle('active', side === 'right');
  }

  function dockLeft() {
    setDocked('left');
    popup.style.left = '10px';
  }
  function dockRight() {
    setDocked('right');
    var vw = window.innerWidth, pw = popup.offsetWidth;
    popup.style.left = Math.max(10, vw - pw - 10) + 'px';
  }

  snapLeft.addEventListener('click', function(e) {
    e.stopPropagation();
    // Toggle: clicking the active dock button undocks (returns to free mode)
    if (dockedSide === 'left') { setDocked(null); } else { dockLeft(); }
  });
  snapRight.addEventListener('click', function(e) {
    e.stopPropagation();
    if (dockedSide === 'right') { setDocked(null); } else { dockRight(); }
  });

  // Click outside → close
  document.addEventListener('click', function(e) {
    if (popup.style.display === 'block' && !popup.contains(e.target)) {
      hidePopup();
    }
  });

  // ── Dragging ───────────────────────────────────────────────────────────────
  var dragging = false, dragX = 0, dragY = 0;
  handle.addEventListener('mousedown', function(e) {
    dragging = true;
    dragX = e.clientX - popup.getBoundingClientRect().left;
    dragY = e.clientY - popup.getBoundingClientRect().top;
    // Manual drag undocks the popup so subsequent arc clicks return to
    // follow-cursor positioning.
    if (dockedSide !== null) setDocked(null);
    e.preventDefault();
  });
  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    var vw = window.innerWidth, vh = window.innerHeight;
    var pw = popup.offsetWidth, ph = popup.offsetHeight;
    popup.style.left = Math.max(0, Math.min(vw - pw, e.clientX - dragX)) + 'px';
    popup.style.top  = Math.max(0, Math.min(vh - ph, e.clientY - dragY)) + 'px';
  });
  document.addEventListener('mouseup', function() { dragging = false; });

  // ── Resizing ───────────────────────────────────────────────────────────────
  // Drag the bottom-right corner handle to enlarge the popup like a window.
  // We override CSS max-width/max-height with inline values, mark the popup
  // with data-resized="1" so the body fills available height via flex, and
  // clamp to viewport. A min size keeps the popup usable even when shrunk.
  var resizing = false, rsStartX = 0, rsStartY = 0, rsStartW = 0, rsStartH = 0;
  resizeHandle.addEventListener('mousedown', function(e) {
    resizing = true;
    var r = popup.getBoundingClientRect();
    rsStartX = e.clientX;
    rsStartY = e.clientY;
    rsStartW = r.width;
    rsStartH = r.height;
    // Lift CSS caps so the user can grow beyond max-width / max-height.
    popup.style.maxWidth  = 'none';
    popup.style.maxHeight = 'none';
    popup.setAttribute('data-resized', '1');
    e.stopPropagation();
    e.preventDefault();
  });
  document.addEventListener('mousemove', function(e) {
    if (!resizing) return;
    var vw = window.innerWidth, vh = window.innerHeight;
    var r  = popup.getBoundingClientRect();
    // Clamp so the popup stays inside the viewport
    var maxW = vw - r.left - 4;
    var maxH = vh - r.top  - 4;
    var newW = Math.max(200, Math.min(maxW, rsStartW + (e.clientX - rsStartX)));
    var newH = Math.max(120, Math.min(maxH, rsStartH + (e.clientY - rsStartY)));
    popup.style.width  = newW + 'px';
    popup.style.height = newH + 'px';
    e.preventDefault();
  });
  document.addEventListener('mouseup', function() { resizing = false; });

  // ── Position helper ────────────────────────────────────────────────────────
  function positionPopup(cx, cy) {
    // Briefly make visible off-screen to measure real rendered height
    popup.style.visibility = 'hidden';
    popup.style.display    = 'flex';
    var vw = window.innerWidth,  vh = window.innerHeight;
    var pw = popup.offsetWidth  || Math.min(540, vw - 20);
    var ph = popup.offsetHeight || 300;

    // ── Docked mode: keep current x position, only refresh measurements ──
    // The popup stays pinned at the user-chosen edge across clicks; this is
    // the "dock to pane" behaviour. We still re-clamp y so the popup doesn't
    // run off the bottom if its content has grown since the last show.
    if (dockedSide === 'left' || dockedSide === 'right') {
      // Re-snap x in case the popup width changed with new content
      if (dockedSide === 'left')  popup.style.left = '10px';
      if (dockedSide === 'right') popup.style.left = Math.max(10, vw - pw - 10) + 'px';
      // Keep current top, but clamp to viewport
      var curTop = parseInt(popup.style.top, 10) || 60;
      curTop = Math.max(10, Math.min(curTop, vh - ph - 10));
      popup.style.top = curTop + 'px';
      popup.style.visibility = 'visible';
      var hint0 = document.getElementById('dt-scroll-hint');
      var body0 = document.getElementById('dt-body');
      if (hint0 && body0) {
        hint0.style.display =
          (body0.scrollHeight > body0.clientHeight + 4) ? 'block' : 'none';
      }
      return;
    }

    var margin = 14;
    // Horizontal: right of cursor; flip left if no room
    var left = (cx + margin + pw <= vw - 10)
               ? cx + margin
               : Math.max(10, cx - pw - margin);
    // Vertical: below cursor; flip above if popup would overflow bottom
    var top;
    if (cy + margin + ph <= vh - 10) {
      top = cy + margin;                // fits below — preferred
    } else if (cy - ph - margin >= 10) {
      top = cy - ph - margin;           // flip above cursor
    } else {
      top = Math.max(10, vh - ph - 10); // clamp: best effort
    }
    popup.style.left       = left + 'px';
    popup.style.top        = top  + 'px';
    popup.style.visibility = 'visible';
    // Show scroll hint if the body div overflows
    var hint = document.getElementById('dt-scroll-hint');
    var body = document.getElementById('dt-body');
    if (hint && body) {
      hint.style.display =
        (body.scrollHeight > body.clientHeight + 4) ? 'block' : 'none';
    }
  }

  // ── Show popup on Plotly click ─────────────────────────────────────────────
  function showPopup(tooltipHtml, cx, cy) {
    if (!tooltipHtml || tooltipHtml.trim() === '') return;

    // In the sticky popup, suppress the raw Classifications bullet list —
    // the pills section below already shows all symbols as clickable buttons.
    // We keep only the bold header line (Publication / Application name).
    // The raw list is detected as an <i>Classifications:</i> section.
    var displayHtml = tooltipHtml
      // Strip raw Classifications bullet list (shown as CLR button instead)
      .replace(/<i>Classifications:<\/i><br>[\s\S]*?(?=<span id='dt-all-cpc'|$)/,
               '')
      .replace(/<span id='dt-all-cpc'[^>]*><\/span>/, '');      // Concepts bullet list is kept visible in the popup content area.

    content.innerHTML = displayHtml;
    actions.innerHTML = '';

    var plain = stripHtml(tooltipHtml);  // use original for number/concept extraction

    // ── Grouped number buttons ─────────────────────────────────────────
    // Parse the tooltip HTML to separate numbers appearing in the
    // 'Publication:' header from those in 'Citations:' bullet lines,
    // then render each group under its own label.
    function makeLabel(text) {
      var lbl = document.createElement('div');
      lbl.className = 'dt-group-label';
      lbl.textContent = text;
      return lbl;
    }
    function makeBtn(info) {
      var a = document.createElement('a');
      a.target = '_blank';
      a.rel = 'noopener';
      if (info.type === 'xp') {
        a.className = 'dt-btn xp';
        a.href = espacenetXpUrl(info.value);
        a.title = 'Find patents citing ' + info.value + ' (ct= in Espacenet)';
        a.textContent = '📄 ' + info.value;
      } else {
        a.className = 'dt-btn';
        a.href = espacenetPubUrl(info.value);
        a.title = 'Open ' + info.value + ' in Espacenet';
        a.textContent = '🔗 ' + info.value;
      }
      return a;
    }

    // Build a side-by-side pair of buttons for a PL citation:
    //   [📄 ct=NUM]  [🔗 NUM]
    // The first opens an Espacenet citation search (ct= field) — contextually
    // appropriate for citations. The second opens the publication directly.
    function makeBtnPair(info) {
      var pair = document.createElement('div');
      pair.className = 'dt-btn-pair';
      pair.style.display = 'flex';
      pair.style.gap = '4px';
      pair.style.alignItems = 'stretch';
      pair.style.marginBottom = '2px';

      var aCt = document.createElement('a');
      aCt.className = 'dt-btn ct';
      aCt.target = '_blank';
      aCt.rel = 'noopener';
      aCt.href = espacenetCtUrl(info.value);
      aCt.title = 'Find patents citing ' + info.value + ' (ct= in Espacenet)';
      aCt.textContent = '📄 ct=' + info.value;
      aCt.style.flex = '1';
      aCt.style.margin = '0';

      var aPub = document.createElement('a');
      aPub.className = 'dt-btn';
      aPub.target = '_blank';
      aPub.rel = 'noopener';
      aPub.href = espacenetPubUrl(info.value);
      aPub.title = 'Open ' + info.value + ' in Espacenet';
      aPub.textContent = '🔗 ' + info.value;
      aPub.style.flex = '1';
      aPub.style.margin = '0';

      pair.appendChild(aCt);
      pair.appendChild(aPub);
      return pair;
    }

    // Extract numbers specifically from the Publication header line
    // (bold text before any <br>, typically '<b>Publication: EP…</b>')
    var pubHeaderMatch = tooltipHtml.match(/<b[^>]*>[^<]*<\/b>/);
    var pubHeaderText  = pubHeaderMatch ? stripHtml(pubHeaderMatch[0]) : '';
    // Keep only publication numbers (have a kind code like B1, A3);
    // filter out bare application numbers (end with digits only).
    var pubNums  = extractNumbers(pubHeaderText).filter(function(n) {
      return n.type === 'patent' && /[A-Z]\d?\*?$/.test(n.value);
    });

    // Extract numbers from Citations bullet lines (everything after the header)
    // Strip the header so its numbers don't appear twice
    var bodyHtml = pubHeaderMatch
      ? tooltipHtml.slice(tooltipHtml.indexOf(pubHeaderMatch[0]) + pubHeaderMatch[0].length)
      : tooltipHtml;
    var bodyNums = extractNumbers(stripHtml(bodyHtml));

    // Deduplicate: remove from bodyNums anything already in pubNums
    var pubVals = {};
    pubNums.forEach(function(n) { pubVals[n.value] = true; });
    bodyNums = bodyNums.filter(function(n) { return !pubVals[n.value]; });

    // Pre-compute CPC data so CLR button can sit inline with the pub number.
    // Only use classifications when data-cpc is explicitly embedded by Python
    // (i.e. this is a real Classifications node). The regex fallback would
    // false-match CPC-like patterns in Concepts or other tooltips.
    var bodyPlain = stripHtml(bodyHtml || tooltipHtml);
    var hasCpcData = /data-cpc='[^']+'/. test(tooltipHtml);
    var cpcs = hasCpcData ? extractClassifications(bodyPlain, tooltipHtml) : [];
    var cpcUrl = cpcs.length > 0 ? buildCpcUrl(cpcs) : null;

    // Detect node types that get special action sections
    var isImagesNode  = /data-pub=/.test(tooltipHtml);
    var isPartiesNode2 = /<i>Parties:<\/i>/.test(tooltipHtml);
    var isCitationsNode = /<i>Citations:<\/i>/i.test(tooltipHtml);
    var _legalTypes2 = [
      'Legal Events', 'Procedural Codes', 'Bibliographic Register Codes',
      'Events Register Codes', 'UPP Codes', 'Procedural Steps'
    ];
    var isLegalProcNode = _legalTypes2.some(function(t) {
      return new RegExp('<i>' + t + ':<\/i>', 'i').test(tooltipHtml);
    });

    if (isPartiesNode2) {
      // PARTIES node: replace PUBLICATION button with inventor/applicant search
      var partiesUrl = buildPartiesUrl(tooltipHtml);
      if (partiesUrl) {
        var aParties = document.createElement('a');
        aParties.className = 'dt-btn presearch';
        aParties.href = partiesUrl;
        aParties.target = '_blank';
        aParties.rel = 'noopener';
        aParties.title = 'Search Espacenet by inventor (in=) and applicant (pa=)';
        aParties.textContent = '🔎 Inventor / Applicant search';
        actions.appendChild(aParties);
      }
    } else {
      if (pubNums.length > 0) {
        if (!isImagesNode) actions.appendChild(makeLabel('Publication'));
        pubNums.forEach(function(info) { actions.appendChild(makeBtn(info)); });
        // Classifications list: display CPC symbols between PUBLICATION and CLR
        if (cpcs.length > 0) {
          actions.appendChild(makeLabel('Classifications'));
          var clsList = document.createElement('div');
          clsList.style.cssText = 'font-size:11px;font-family:monospace;'
            + 'line-height:1.7;padding:2px 0 4px 2px;'
            + 'max-height:180px;overflow-y:auto;';
          cpcs.forEach(function(sym) {
            var row = document.createElement('div');
            row.textContent = '\u2022 ' + sym;
            clsList.appendChild(row);
          });
          actions.appendChild(clsList);
        }
        // CLR section: separate label + button below Classifications
        if (cpcUrl) {
          actions.appendChild(makeLabel('Classification Ranking'));
          var aClr = document.createElement('a');
          aClr.className = 'dt-btn cpc';
          aClr.href = cpcUrl;
          aClr.target = '_blank';
          aClr.rel = 'noopener';
          aClr.title = 'CLassification Ranking — Espacenet OR query on all '
                     + cpcs.length + ' CPC symbols, ranked by best match';
          aClr.textContent = '🏷 CLR (' + cpcs.length + ')';
          actions.appendChild(aClr);
        }
      }
      if (bodyNums.length > 0) {
        if (!isImagesNode) actions.appendChild(makeLabel('Publication'));
        bodyNums.forEach(function(info) {
          // In Citations nodes, render PL citations as a side-by-side pair
          // [ct=NUM] + [NUM] so the user can launch a citation search OR open
          // the publication itself with one click. NPL (XP) numbers keep their
          // single-button form because they cannot be opened directly anyway.
          if (isCitationsNode && info.type !== 'xp') {
            actions.appendChild(makeBtnPair(info));
          } else {
            actions.appendChild(makeBtn(info));
          }
        });
        // CTR section: separate label + button below the citation list,
        // analogous to the CLR button on Classifications nodes.
        if (isCitationsNode) {
          var ctrUrl = buildCtrUrl(bodyNums.map(function(n) { return n.value; }));
          if (ctrUrl) {
            actions.appendChild(makeLabel('Citation Ranking'));
            var aCtr = document.createElement('a');
            aCtr.className = 'dt-btn ct';
            aCtr.href = ctrUrl;
            aCtr.target = '_blank';
            aCtr.rel = 'noopener';
            aCtr.title = 'CiTation Ranking — Espacenet OR query on all '
                       + bodyNums.length + ' citations, ranked by best match';
            aCtr.textContent = '📚 CTR (' + bodyNums.length + ')';
            actions.appendChild(aCtr);
          }
        }
      }
    }

    // ── EP Register buttons for legal/procedural nodes ──────────────────────
    if (isLegalProcNode) {
      // Pass full tooltipHtml so buildEpRegisterUrls can read data-appid.
      var _regUrls = buildEpRegisterUrls(tooltipHtml);
      if (_regUrls) {
        actions.appendChild(makeLabel('EP Register'));
        var _btns = [
          { href: _regUrls.about,     text: '📄 EP About this file' },
          { href: _regUrls.legal,     text: '⚖︎ EP Legal status' },
          { href: _regUrls.federated, text: '🗺 EP Federated Register' },
          { href: _regUrls.event,     text: '📅 EP Event history' },
          { href: _regUrls.citations, text: '🔗 EP Citations' },
          { href: _regUrls.family,    text: '👪 EP Patent family' },
          { href: _regUrls.alldocs,   text: '📁 EP All documents' },
        ];
        // Unitary Patent buttons — shown when:
        //  (a) UPP Codes node, OR
        //  (b) publication number has kind code C0 (unitary effect)
        // UP section only when a C0 kind code is present in the publication number.
        var isUppNode = pubNums.some(function(n) { return /C0\*?$/i.test(n.value); });
        // Render EP buttons first, then UP label + UP buttons after
        _btns.forEach(function(b) {
          // Wrap button + URL in a row div
          var row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:baseline;gap:6px;';
          var a = document.createElement('a');
          a.className = 'dt-btn';
          a.href = b.href;
          a.target = '_blank';
          a.rel = 'noopener';
          a.title = b.href;  // native browser tooltip on hover
          a.textContent = b.text;
          // URL shown inline as small grey text for copy/read
          row.appendChild(a);
          actions.appendChild(row);
        });
        // Add UP section after EP buttons
        if (isUppNode) {
          actions.appendChild(makeLabel('Unitary Patent'));
          [{ href: _regUrls.ueMain,    text: '📄 UP About this file' },
           { href: _regUrls.ueEvent,   text: '📅 UP Event history' },
           { href: _regUrls.ueDoclist, text: '📁 UP All documents' }
          ].forEach(function(b) {
            var row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:baseline;gap:6px;';
            var a = document.createElement('a');
            a.className = 'dt-btn';
            a.href = b.href; a.target = '_blank'; a.rel = 'noopener';
            a.title = b.href; a.textContent = b.text;
            row.appendChild(a);
            actions.appendChild(row);
          });
        }
      }
    }

    // XFR section: concept-based search (claims + full-text).
    // Suppressed for node types where concept queries make no sense:
    // Parties, Legal Events, Procedural Codes, Register Codes, UPP Codes,
    // Procedural Steps, Citations, Classifications.
    // Note: Python's .capitalize() lowercases all but the first letter, so
    // 'Legal Events' becomes 'Legal events' in the tooltip HTML.
    // Match case-insensitively to cover both forms.
    var _noXfrTypes = [
      'Parties', 'Legal Events', 'Procedural Codes',
      'Bibliographic Register Codes', 'Events Register Codes',
      'UPP Codes', 'Procedural Steps', 'Citations', 'Classifications'
    ];
    var isPartiesNode = _noXfrTypes.some(function(t) {
      return new RegExp('<i>' + t + ':<\\/i>', 'i').test(tooltipHtml);
    });
    var concepts = isPartiesNode ? [] : extractConcepts(tooltipHtml);
    if (concepts.length > 0) {
      var claimsUrl = buildEspacenetClaimsUrl(concepts, tooltipHtml);
      var nftxtUrl  = buildEspacenetNftxtUrl(concepts, tooltipHtml);
      if (claimsUrl || nftxtUrl) {
        actions.appendChild(makeLabel('XFR — Pivot Ranking'));
        if (claimsUrl) {
          var aClaims = document.createElement('a');
          aClaims.className = 'dt-btn concept';
          aClaims.href = claimsUrl;
          aClaims.target = '_blank';
          aClaims.rel = 'noopener';
          aClaims.title = 'XFR concept strategy searching Claim 1 text';
          aClaims.textContent = '🔍 Search Espacenet (claims)';
          actions.appendChild(aClaims);
        }
        if (nftxtUrl) {
          var aNftxt = document.createElement('a');
          aNftxt.className = 'dt-btn concept';
          aNftxt.href = nftxtUrl;
          aNftxt.target = '_blank';
          aNftxt.rel = 'noopener';
          aNftxt.title = 'XFR concept strategy searching full text';
          aNftxt.textContent = '🔍 Search Espacenet (full-text)';
          actions.appendChild(aNftxt);
        }
      }
    }

    // CPC classification search is surfaced as the CLR button in the Publication row.

        positionPopup(cx, cy);
    popup.style.display = 'flex';
  }

  // ── Wire to Plotly ─────────────────────────────────────────────────────────
  // Use a retry loop: DOMContentLoaded may have already fired when a saved
  // HTML file is opened in a new browser tab, so the event listener would
  // never execute. Poll until the Plotly plot element is ready.
  function _wirePlotlyClick() {
    var myPlot = document.querySelector('.js-plotly-plot');
    if (!myPlot || !myPlot.on) {
      // Not ready yet — retry in 200 ms
      setTimeout(_wirePlotlyClick, 200);
      return;
    }

    myPlot.on('plotly_click', function(data) {
      var pt      = data.points[0];
      var tooltip = (pt.customdata && pt.customdata[1]) || '';
      var cx = data.event.clientX;
      var cy = data.event.clientY;
      // Fallback for nodes with empty customdata[1] (animated charts,
      // structural/grey sectors, legal-event leaf nodes):
      // build a minimal tooltip from the pub number (customdata[0])
      // and the sector label so the popup still appears.
      if (!tooltip || !tooltip.trim()) {
        var pubFallback = (pt.customdata && pt.customdata[0]) || '';
        var labelFallback = pt.label || pt.text || '';
        if (pubFallback || labelFallback) {
          var parts = [];
          if (pubFallback) parts.push('<b>Publication: ' + pubFallback + '</b>');
          if (labelFallback && labelFallback !== pubFallback)
            parts.push('<i>' + labelFallback + '</i>');
          tooltip = parts.join('<br>');
        }
      }
      showPopup(tooltip, cx, cy);
      // Prevent the outside-click handler from immediately closing
      data.event.stopPropagation && data.event.stopPropagation();
    });
  }

  // Start wiring — works whether DOM is ready or not
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wirePlotlyClick);
  } else {
    _wirePlotlyClick();  // DOM already ready
  }

})();
</script>
"""
        return script

    def append_sticky_popup_script(self, html_path):
        """Inject the sticky popup script into a saved Plotly HTML file."""
        script = self._sticky_popup_script_content()
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()
        if '</body>' in html:
            html = html.replace('</body>', script + '</body>')
        else:
            html += script
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)

    def run_divitree_plotter(self, tree_type="ORAP", process_both=False,
                             reference_mode="Application", interleaving_type=None, tree_path=None):
        """
        Plot the latest tree output that matches the requested tree_type.
        Args:
            tree_type (str|None): "ORAP" or "priority".
            tree_path (str|None): If given, plot this specific file directly.
        """
        if tree_path and os.path.exists(tree_path):
            all_paths = [tree_path]
        else:
            all_paths = sorted(
                [p for p in self.find_latest_output_files() if p],
                key=lambda p: os.path.getmtime(p),
                reverse=True
            )

        if not all_paths:
            print("❌ No tree output files found.")
            return None, None

        for tree_path in all_paths:
            if not tree_path or not isinstance(tree_path, str):
                continue
                
            filename_lower = os.path.basename(tree_path).lower()
            # print("filename_lower:", filename_lower)
                
            # print("tree_path:", tree_path)            
            tree_data, interleaving_type, reference_mode = self.read_tree_data(tree_path, interleaving_type, reference_mode)
            # print("run_divitree_plotter: interleaving_type, reference_mode:", interleaving_type, reference_mode)
            
            df = self.create_grouped_items_df(tree_data)
            if df.empty:
                print(f"⚠️ DataFrame is empty after parsing {tree_path}, skipping...")
                continue
                
            df['interleaving_type'] = str(interleaving_type).capitalize()
            df['reference_mode'] = str(reference_mode).capitalize()
            
            html_filename = f"sunburst_plot_{interleaving_type}.html"
            html_path = os.path.join(self.workdir, html_filename)

            try:
                fig, _ = self.plot_sunburst(df, interleaving_type, reference_mode, html_path)
            except Exception as e:
                print(f"❌ Error generating Plotly chart: {e}")
                continue

            html_header = f"""
                ✅ Chart saved to: <code>{html_path}</code><br>
                📂 To open it in a new browser tab, right-click the file in the left sidebar and choose:<br>
                <b><i>+ Open in New Browser Tab</i></b>
                """
                
            if interleaving_type == "Images":
                self.append_image_hover_script(html_path)
            self.append_sticky_popup_script(html_path)

            return fig, html_path, html_header  # Only process one file and return immediately

        print("❌ No tree files processed.")
        return None, None, None