# patent_analysis/patent_processor.py

import sys
from contextlib import contextmanager

@contextmanager
def suppress_stdout():
    """Temporarily suppress stdout (print output)."""
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

import os, re
from pathlib import Path

import pandas as pd
import xml.etree.ElementTree as ET
from functools import partial

from IPython.display import IFrame, display, HTML, clear_output
import plotly.graph_objects as go
from plotly.graph_objs import FigureWidget
import ipywidgets as widgets
from ipywidgets import widgets, Button, VBox, HBox, Output, GridBox, Layout

from collections import Counter
from epo.tipdata.ops import OPSClient, models, exceptions

from patent_analysis.install_dependencies import _ensure_deps
_ensure_deps()
from patent_analysis.ops_client_wrapper import OPSClientWrapper
from patent_analysis.family_record import FamilyRecord
from patent_analysis.tree_creation import TreeCreation, TreeNode
from patent_analysis.tree_processor import TreeProcessor
from patent_analysis.related_files_searcher import RelatedFilesSearcher
from patent_analysis.divitree_plotter import DiviTreePlotter, COLOR_MAP


def display_chart_inline(html_path, height="900px"):
    """Read a saved HTML file and display it inline in the notebook without any network call."""
    if not html_path or not os.path.exists(html_path):
        print(f"⚠️ Chart file not found: {html_path}")
        return
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    display(HTML(f"""
        <iframe srcdoc="{html_content.replace(chr(34), "&quot;")}"
            width="100%" height="{height}"
            style="border:none; overflow:hidden;">
        </iframe>
    """))

# Main class
class PatentProcessor:
            
    # ✅ Step 1: Group UI Initialization
    def init_input_widgets(self):
        style_description = {'description_width': '100px'}
        dropdown_layout = widgets.Layout(width='400px')
        
        self.tree_type_selector = widgets.Dropdown(
            options=['with ORAP numbers (recommended)', 'with priority numbers'],
            value='with ORAP numbers (recommended)',
            description='DiviTree Type:',
            layout=dropdown_layout,
            style=style_description
        )
        
        self.reference_type_widget = widgets.Dropdown(
            options=['application', 'publication'],
            value='publication',
            description='Reference Type:',
            layout=dropdown_layout,
            style=style_description
        )
        
        self.doc_number_widget = widgets.Text(description='Doc Number:', layout=dropdown_layout, style=style_description)
        self.country_widget = widgets.Text(description='Country:', layout=dropdown_layout, style=style_description)
        self.kind_widget = widgets.Text(description='Kind:', layout=dropdown_layout, style=style_description)
        
        self.constituents_widget = widgets.Dropdown(
            options=[None, 'biblio', 'legal'],
            value=None,
            description='Constituents:',
            layout=dropdown_layout,
            style=style_description
        )
        
        self.input_widget = widgets.Dropdown(
            options=[models.Docdb, models.Epodoc],
            value=models.Docdb,
            description='Input Model:',
            layout=dropdown_layout,
            style=style_description
        )
        
        self.output_type_widget = widgets.Dropdown(
            options=['raw', 'dataframe'],
            value='raw',
            description='Output Type:',
            layout=dropdown_layout,
            style=style_description
        )
        
        self.reference_type_widget.observe(self.update_doc_number, names='value')

        # ✅ Strip spaces and non-digit characters from Doc Number as the user types.
        def _clean_doc_number_input(change):
            raw = change['new'].strip()
            # Remove trailing check digit with dot (e.g. ".5", ".8")
            cleaned = re.sub(r'\.[0-9]$', '', raw)
            # Remove trailing kind code (e.g. "B1", "A2", "B2", "A1") before extracting digits
            cleaned = re.sub(r'[A-Z][0-9]?$', '', cleaned)
            clean = re.sub(r'[^0-9]', '', cleaned)
            if clean != raw:
                self.doc_number_widget.value = clean
        self.doc_number_widget.observe(_clean_doc_number_input, names='value')

    # ✅ Step 2: Build Layout Separately
    def build_input_ui(self):
        input_widgets_title = widgets.HTML(value="<h3 style='text-align:center; margin-bottom:15px;'>DiviTree</h3>")
        
        self.submit_button = widgets.Button(
            description="Submit",
            layout=widgets.Layout(width='400px', height='30px')
        )
    
        input_widgets_box = widgets.VBox([
            input_widgets_title,
            self.tree_type_selector,
            self.reference_type_widget,
            self.doc_number_widget,
            self.country_widget,
            self.kind_widget,
            self.constituents_widget,
            self.input_widget,
            self.output_type_widget,
            self.submit_button,  # ✅ only submit here
            self.output
        ], layout=widgets.Layout(padding='10px'))
        
        # Logos
        MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(MODULE_DIR, "white_logo.png"), "rb") as f:
            white_logo_data = f.read()
        with open(os.path.join(MODULE_DIR, "black_logo.png"), "rb") as f:
            black_logo_data = f.read()

        white_logo_image = widgets.Image(value=white_logo_data, format='png', width=200, height=120)
        black_logo_image = widgets.Image(value=black_logo_data, format='png', width=200, height=120)
        
        spacer = widgets.Box(layout=widgets.Layout(width='10px'))
        logos_title = widgets.HTML("<h3 style='text-align:center; margin-bottom:10px;'>Le jour et la nuit</h3>")
        logos_box = widgets.HBox([white_logo_image, spacer, black_logo_image], layout=widgets.Layout(justify_content='center'))
        logos_with_title = widgets.VBox([logos_title, logos_box], layout=widgets.Layout(align_items='center'))
        self.input_ui = widgets.HBox([input_widgets_box, logos_with_title], layout=widgets.Layout(align_items='flex-start', gap='20px', padding='10px'))

    # ✅ Step 3: Encapsulate Output Initialization
    def init_output_widgets(self):
        self.output = widgets.Output()
        self.text_output = widgets.Output(
            layout=widgets.Layout(
                border='1px solid gray', 
                flex='1 1 35%',
                height='1000px', 
                overflow='auto',
                padding='10px', 
                min_width='600px'  # ensures text doesn't collapse completely
            )
        )
        
        self.chart_output = widgets.Output(
            layout=widgets.Layout(
                border='1px solid gray',
                flex='1 1 65%',
                height='auto',
                overflow='visible',
                padding='10px',
                min_width='600px',
            )
        )

        self.output_container = widgets.HBox(        
            [self.text_output, self.chart_output], 
            layout=widgets.Layout(
                align_items='flex-start', 
                gap='20px', 
                padding='10px',
                width='100%',        # full width of notebook
                flex_flow='row nowrap'
            )
        )
        
        # enforce vertical sync
        self.text_output.layout.align_self = 'stretch'
        self.chart_output.layout.align_self = 'stretch'

        # optional scroll behavior for long logs
        self.text_output.layout.overflow_y = 'scroll'
        self.text_output.layout.height = '990px'

        self.output_container.layout.display = 'none'

    _PROGRESS_HTML = (
        "<style>"
        "@keyframes _diprogress{{"
        "0%{{background-position:0 0}}"
        "100%{{background-position:40px 0}}"
        "}}"
        "</style>"
        "<div style='margin:8px 0 6px 0;font-family:sans-serif;width:400px'>"
        "<div style='font-size:13px;margin-bottom:4px'>{msg}</div>"
        "<div style='width:400px;height:18px;background:#e0e0e0;border-radius:4px;overflow:hidden'>"
        "<div style='width:60%;height:100%;border-radius:4px;"
        "background:repeating-linear-gradient("
        "  45deg,"
        "  #2196F3 0px,#2196F3 20px,"
        "  #1976D2 20px,#1976D2 40px"
        ");"
        "background-size:40px 40px;"
        "animation:_diprogress 0.8s linear infinite'>"
        "</div></div></div>"
    )

    def _show_progress(self, message: str, target=None):
        """Append an animated HTML progress bar to target Output widget."""
        import time
        t = target if target is not None else self.output
        with t:
            display(HTML(self._PROGRESS_HTML.format(msg=message)))
        time.sleep(0.15)  # brief yield so IOPub flushes before blocking work

    def _render_tree_html(self, content: str) -> str:
        """
        Convert plain-text tree content to HTML for display in text_output.
        Uses inline styles on each span — CSS classes don't work reliably
        inside Jupyter Output widgets due to DOM isolation.
        """
        import re, html as hm

        # Strip all ANSI escape sequences
        ansi_re = re.compile(r'\x1b\[[0-9;]*[mGKHFABCDJ]?')
        content = ansi_re.sub('', content)

        lines = content.split('\n')
        html_lines = []

        S_MONO   = "font-family:monospace;font-size:13px;display:block;white-space:pre-wrap"
        S_CC     = "color:#00afff;font-weight:bold"   # [EP] [WO] country codes — matches ANSI 38;5;39 (sky blue)
        S_DATE   = "color:#e65c00"                    # dates
        S_NEW    = "color:#2e7d32;font-weight:bold"   # new items (+)
        S_LINK   = "color:#1565c0"                    # image URLs

        combined = re.compile(
            r"(?P<cc>\[[A-Z]{2}\])"
            r"|(?P<db>\[(?:\d{4}-\d{2}-\d{2}|\d{8})\])"
            r"|(?P<dp>(?<!\[)\d{4}-\d{2}-\d{2}(?!\]))"
        )

        def colorise_text(text: str) -> str:
            result = []
            pos = 0
            for m in combined.finditer(text):
                result.append(hm.escape(text[pos:m.start()]))
                if m.group('cc'):
                    inner = hm.escape(m.group()[1:-1])   # the two-letter code without brackets
                    result.append(f'[<span style="{S_CC}">{inner}</span>]')
                elif m.group('db'):
                    inner = hm.escape(m.group()[1:-1])
                    result.append(f'[<span style="{S_DATE}">{inner}</span>]')
                elif m.group('dp'):
                    result.append(f'<span style="{S_DATE}">{hm.escape(m.group())}</span>')
                pos = m.end()
            result.append(hm.escape(text[pos:]))
            return ''.join(result)

        def mono(text: str) -> str:
            return f'<span style="{S_MONO}">{text}</span>'

        list_line    = re.compile(r'^(\.\s)+\[(?!Concept\]|Claim)')
        legal_line   = re.compile(r'^(\.\s)+\[[A-Z]{2,3}\] ')   # legal event: [EP] RFEE: … [date]
        concept_line = re.compile(r'^(\.\s)+\[Concept\]')
        img_line     = re.compile(r'^(\.\s)+<img ')
        dot_line     = re.compile(r'^(\.\s)+')

        for line in lines:
            if not line.strip():
                html_lines.append(mono('&nbsp;'))
                continue

            indent_m = re.match(r'^((?:\. )+)', line)
            indent   = hm.escape(indent_m.group(1)) if indent_m else ''
            rest     = line[len(indent_m.group(1)):] if indent_m else line

            if concept_line.match(line):
                prefix = '[Concept] '
                phrase = rest[len(prefix):] if rest.startswith(prefix) else rest
                if phrase.startswith('+'):
                    html_lines.append(mono(
                        f'{indent}<span style="{S_NEW}">{hm.escape(phrase[1:])}</span>'
                    ))
                else:
                    html_lines.append(mono(f'{indent}{hm.escape(phrase)}'))

            elif img_line.match(line):
                src_m = re.search(r'src=["\']([^"\']+)["\']', rest)
                if src_m:
                    url = hm.escape(src_m.group(1))
                    html_lines.append(mono(
                        f'{indent}<a href="{url}" target="_blank" style="{S_LINK}">{url}</a>'
                    ))
                else:
                    html_lines.append(mono(f'{indent}{hm.escape(rest)}'))

            elif legal_line.match(line):
                # Legal event lines: ". . [EP] RFEE: Renewal fee paid [2023-01-15]"
                # After ANSI stripping, rest = "[EP] RFEE: …  [date]".
                # colorise_text() handles [CC] → sky blue and [date] → orange directly.
                html_lines.append(mono(f'{indent}{colorise_text(rest)}'))

            elif list_line.match(line):
                def colorise_quoted(m):
                    token = m.group(0)
                    inner = token.strip("'\"")
                    if inner.startswith('+'):
                        return f'\'<span style="{S_NEW}">{hm.escape(inner[1:])}</span>\''
                    return f"'{colorise_text(inner)}'"
                colored = re.sub(r"'[^']*'", colorise_quoted, rest)
                html_lines.append(mono(f'{indent}{colored}'))

            elif dot_line.match(line):
                if ', ' in rest and any(p.startswith('+') for p in rest.split(', ')):
                    parts = rest.split(', ')
                    rendered = []
                    for p in parts:
                        if p.startswith('+'):
                            rendered.append(f'<span style="{S_NEW}">{hm.escape(p[1:])}</span>')
                        else:
                            rendered.append(colorise_text(p))
                    html_lines.append(mono(f'{indent}{", ".join(rendered)}'))
                else:
                    html_lines.append(mono(f'{indent}{colorise_text(rest)}'))

            else:
                html_lines.append(mono(hm.escape(line)))

        return '\n'.join(html_lines)


    def setup_dropdowns_and_buttons(self):
        """Initializes dropdowns and action buttons with observers."""
        large_dropdown_layout = widgets.Layout(width='400px', height="300px")

        # ORAP dropdown
        self.ORAP_tree_event_code_dropdown = widgets.SelectMultiple(
            options=[], description='', disabled=False, layout=large_dropdown_layout
        )
        try:
            self.ORAP_tree_event_code_dropdown.unobserve(self._orap_observer, names='value')
        except AttributeError:
            pass
        self._orap_observer = partial(self.on_event_code_selected, source='ORAP')
        self.ORAP_tree_event_code_dropdown.observe(self._orap_observer, names='value')

        # Priority dropdown
        self.priority_tree_event_code_dropdown = widgets.SelectMultiple(
            options=[], description='', disabled=False, layout=large_dropdown_layout
        )
        try:
            self.priority_tree_event_code_dropdown.unobserve(self._priority_observer, names='value')
        except AttributeError:
            pass
        self._priority_observer = partial(self.on_event_code_selected, source='priority')
        self.priority_tree_event_code_dropdown.observe(self._priority_observer, names='value')

        # Hide priority dropdown initially
        self.priority_tree_event_code_dropdown.layout.display = 'none'

        # Initialize dropdown selection state
        self.selected_orap_codes = list(self.ORAP_tree_event_code_dropdown.value) if self.ORAP_tree_event_code_dropdown.value else []
        self.selected_prio_codes = list(self.priority_tree_event_code_dropdown.value) if self.priority_tree_event_code_dropdown.value else []

        # --- Column 1 (Actions) ---
        action_labels = [
            "Show Priorities", "Show Applications", "Show Parents", "Show Publications", "Show Classifications",
            "Show Citations", "Show Parties", "Show Images", "Show Claims", "Show Concepts"
        ]        
        action_flags = [
            'Show_priorities', 'Show_applications', 'Show_parents', 'Show_publications', 'Show_classifications',
            'Show_citations', 'Show_parties', 'Show_images', 'Show_claims', 'Show_concepts'
        ]
        
        # --- Column 2 (Legal and Procedural Actions) ---
        sub_action_labels = [
            "Show Legal Events", "Show Procedural Codes", "Show Bibliographic Register Codes", "Show Events Register Codes", "Show Procedural Steps", "Show UPP Codes"
        ]
        sub_action_flags = [
            'Show_legal_events', 'Show_procedural_codes', 'Show_biblio', 'Show_events', 'Show_procedural_steps', 'Show_upp'
        ]

        # Merge both into initFlags
        self.initFlags = action_flags + sub_action_flags

        self.action_buttons = [widgets.Button(description=label) for label in action_labels]
        for btn, flag in zip(self.action_buttons, action_flags):
            btn.on_click(lambda b, flag=flag: self.process_with_initFlag(flag))            

        self.sub_action_buttons = [widgets.Button(description=label) for label in sub_action_labels]
        for btn, flag in zip(self.sub_action_buttons, sub_action_flags):
            btn.on_click(lambda b, flag=flag: self.process_with_initFlag(flag))
        
        # Define process button before calling display_checkboxes_and_buttons
        self.process_button = widgets.Button(description="Process Selected Countries")
        self.process_button.on_click(self.on_process_button_clicked)

        self.process_all_button = widgets.Button(description="Process All Countries")
        self.process_all_button.on_click(self.on_process_all_button_clicked)

        self.submit_button.on_click(self.on_submit_button_clicked)
        
        self.flag_to_interleaving = {
            'Show_priorities': 'Priorities',
            'Show_applications': 'Applications',
            'Show_parents': 'Parents',
            'Show_publications': 'Publications',
            'Show_classifications': 'Classifications',            
            'Show_citations': 'Citations',
            'Show_parties': 'Parties',
            'Show_images': 'Images',
            'Show_legal_events': 'Legal Events',
            'Show_procedural_codes': 'Procedural Codes',
            'Show_biblio': 'Bibliographic Register Codes',
            'Show_events': 'Events Register Codes',
            'Show_procedural_steps': 'Procedural Steps',
            'Show_upp': 'UPP Codes',
            'Show_claims': 'Claims',
            'Show_concepts': 'Concepts',
        }
        
    # ✅ Step 5a: Extra bottom-bar buttons (DiviTree History + Related Files Search)
    def init_extra_buttons(self):
        """
        Initialises the two extra buttons displayed in a horizontal bar below result_hbox:
          - Bibliographic Actions Retrieval : DiviTree history for bibliographic types
                                              (Priorities, Applications, Parents, Publications,
                                               Citations, Classifications, Parties)
          - Related Files Search            : triggers a bibliographic-data-based related-files search
          - Event / Procedural Actions Retrieval : re-extract codes + show chart from any prior tree file,
                                                 AND show DiviTree history for event/procedural types
                                                 (Legal Events, Procedural Codes,
                                                  Bibliographic Register Codes, Events Register Codes,
                                                  Procedural Steps, UPP Codes)
        Button widths are set dynamically in display_checkboxes_and_buttons() once
        button_width is known. No fixed width is assigned here.
        The entire extra_section starts hidden and is revealed together with result_hbox.
        """
        button_layout = widgets.Layout(height='36px', margin='4px')  # width set dynamically in display_checkboxes_and_buttons

        # Col1 bottom — below the dropdown box: file selector to extract codes & show chart
        self.extract_codes_button = widgets.Button(
            description="Extract Codes & Show Chart",
            tooltip="Select a tree file, extract event/procedural codes into the dropdown, and display its chart",
            layout=button_layout
        )
        # Col2 bottom — aligned with Legal and Procedural Actions
        self.event_codes_retrieval_button = widgets.Button(
            description="Event / Procedural Actions Retrieval",
            tooltip="Show DiviTree history for event/procedural types (Legal Events, Procedural Codes, Bibliographic Register Codes, Events Register Codes, Procedural Steps, UPP Codes)",
            layout=button_layout
        )
        # Col3 — aligned with Select Countries
        self.related_files_button = widgets.Button(
            description="Related Files Search",
            tooltip="Find related patent files based on the bibliographic data of the current input",
            layout=button_layout
        )
        # Col4 — aligned with Bibliographic Actions
        self.divitree_history_button = widgets.Button(
            description="Bibliographic Actions Retrieval",
            tooltip="Show DiviTree history charts for bibliographic types (Priorities, Applications, Parents, Publications, Citations, Classifications, Parties)",
            layout=button_layout
        )

        self.extract_codes_button.on_click(self.on_extract_codes_clicked)
        self.event_codes_retrieval_button.on_click(self.on_event_codes_retrieval_clicked)
        self.related_files_button.on_click(self.on_related_files_clicked)
        self.divitree_history_button.on_click(self.on_divitree_history_clicked)

        # Order matches result_hbox column order: left, middle, right
        # Widths are applied in display_checkboxes_and_buttons()
        self.extra_buttons_bar = widgets.HBox(
            [self.event_codes_retrieval_button,
             self.related_files_button,
             self.divitree_history_button],
            layout=widgets.Layout(gap='20px', padding='10px', align_items='center')
        )

        # Individual output boxes — each hosts its own visualisation/results
        self.extract_codes_output = widgets.Output(
            layout=widgets.Layout(
                border='1px solid #b0d0b0',
                padding='10px',
                min_height='100px',
                width='100%',
                display='none'
            )
        )
        self.event_codes_retrieval_output = widgets.Output(
            layout=widgets.Layout(
                border='1px solid #a0d0a0',
                padding='10px',
                min_height='100px',
                width='100%',
                display='none'
            )
        )
        self.related_files_output = widgets.Output(
            layout=widgets.Layout(
                border='1px solid #f0c070',
                padding='10px',
                min_height='100px',
                width='100%',
                display='none'
            )
        )
        self.divitree_history_output = widgets.Output(
            layout=widgets.Layout(
                border='1px solid #90c0e0',
                padding='10px',
                min_height='100px',
                width='100%',
                display='none'
            )
        )

        # Container for output boxes — HIDDEN until result_hbox is shown
        # Note: extra_buttons_bar is no longer used here — each button is
        # embedded directly inside its column VBox in display_checkboxes_and_buttons()
        self.extra_section = widgets.VBox(
            [
                self.extract_codes_output,
                self.event_codes_retrieval_output,
                self.related_files_output,
                self.divitree_history_output,
            ],
            layout=widgets.Layout(width='100%', padding='0px', margin='0px', display='none')
        )

        # Persistent sub-outputs for event_codes_retrieval so re-pressing the button
        # always updates the same widgets rather than appending new ones
        self.event_codes_result_output = widgets.Output()
        self.event_codes_chart_display = widgets.Output(
            layout=widgets.Layout(
                width='100%', min_height='500px',
                border='1px solid #c0c0c0', padding='10px'
            )
        )
        # Separate persistent output for the Event/Procedural history section
        # (rendered below the file-selector within event_codes_retrieval_output)
        self.event_history_output = widgets.Output(
            layout=widgets.Layout(width='100%')
        )
        
    # ------------------------------------------------------------------
    # Handlers for the extra buttons (algorithms to be detailed later)
    # ------------------------------------------------------------------

    # ── Types handled by each bottom button ──────────────────────────────────
    BIBLIO_TYPES = frozenset([
        'Priorities', 'Applications', 'Parents', 'Publications',
        'Citations', 'Classifications', 'Parties', 'Images',
        'Claims', 'Concepts',
    ])
    EVENT_TYPES = frozenset([
        'Legal Events', 'Procedural Codes',
        'Bibliographic Register Codes', 'Events Register Codes',
        'Procedural Steps', 'UPP Codes',
    ])

    def _show_divitree_history(self, output_widget, type_filter: set, label: str):
        """
        Shared implementation for both history buttons.

        Parameters
        ----------
        output_widget : widgets.Output
            The Output widget to render into.
        type_filter : set
            Only interleaving types in this set are plotted.
        label : str
            Short label used in log messages (e.g. 'Bibliographic', 'Event/Procedural').
        """
        output_widget.layout.display = ''
        with output_widget:
            clear_output(wait=True)
            print(f"⏳ Loading {label} DiviTree history …")
            try:
                reference_mode = self.reference_type_widget.value

                # ── 1. Collect candidate search directories ──
                search_dirs = []
                for candidate in [
                    "/home/jovyan/output",
                    "/home/jovyan/filtered_output",
                    self.output_dir,
                    getattr(self.tree_plotter, 'workdir', None),
                ]:
                    if candidate and os.path.isdir(candidate) and candidate not in search_dirs:
                        search_dirs.append(candidate)

                # ── 2. Build deduplicated map: interleaving_type → (mtime, html_path, dir) ──
                best: dict = {}
                for d in search_dirs:
                    for fname in os.listdir(d):
                        if not (fname.startswith('sunburst_plot_') and fname.endswith('.html')):
                            continue
                        fpath = os.path.join(d, fname)
                        it = fname[len('sunburst_plot_'):-len('.html')].replace('_', ' ')
                        if it not in type_filter:
                            continue   # skip types handled by the other button
                        mtime = os.path.getmtime(fpath)
                        if it not in best or mtime > best[it][0]:
                            best[it] = (mtime, fpath, d)

                if not best:
                    print(f"ℹ️ No {label} DiviTree history found "
                          "(no matching sunburst_plot_*.html in /home/jovyan/output/ or /home/jovyan/filtered_output/).")
                    return

                # Sort by COLOR_MAP order, then alphabetically
                type_order = list(COLOR_MAP.keys())
                sorted_types = sorted(
                    best.keys(),
                    key=lambda t: (type_order.index(t) if t in type_order else len(type_order), t)
                )

                print(f"📂 Directories with history: {list(search_dirs)}")
                print(f"   output: {sorted_types}")

                # ── 3. Helper: find the best .txt for a given (dir, interleaving_type) ──
                def find_txt_for_type(d, it):
                    rec_clean = re.sub(r"[^A-Za-z0-9]", "", self.rec_input)
                    safe_type = it.replace(' ', '_')
                    prefix = f"{rec_clean}_"
                    suffix = f"_{safe_type}_first_output.txt"
                    typed = sorted(
                        [os.path.join(d, f) for f in os.listdir(d)
                         if f.startswith(prefix) and f.endswith(suffix)],
                        key=os.path.getmtime, reverse=True
                    )
                    return (typed[0], 'typed') if typed else (None, None)

                # ── 4. Build one DataFrame per type from its matched .txt ──
                dfs = []
                interleaving_types = []

                available = [(it, best[it]) for it in sorted_types
                             if find_txt_for_type(best[it][2], it)[0] is not None]
                missing   = [it for it in sorted_types
                             if find_txt_for_type(best[it][2], it)[0] is None]

                print(f"\n🌳 Plotting {len(available)} sunburst(s) with typed snapshots: "
                      f"{[it for it, _ in available]}")
                if missing:
                    print(f"   ⏭️  Skipped {len(missing)} (no snapshot yet — run each type once to generate): "
                          f"{missing}")

                for it in sorted_types:
                    _mtime, _html, d = best[it]
                    txt_path, txt_kind = find_txt_for_type(d, it)
                    if not txt_path:
                        continue
                    print(f"  ✅ '{it}'  ←  {os.path.basename(txt_path)}")
                    try:
                        tree_data, _, detected_rm = self.tree_plotter.read_tree_data(
                            txt_path, it, reference_mode
                        )
                        if tree_data is None:
                            print(f"    ⚠️ read_tree_data returned None, skipping.")
                            continue
                        df = self.tree_plotter.create_grouped_items_df(tree_data)
                        if df is None or df.empty:
                            print(f"    ⚠️ Empty DataFrame, skipping.")
                            continue
                        df['interleaving_type'] = str(it).capitalize()
                        df['reference_mode'] = str(detected_rm or reference_mode).capitalize()
                        dfs.append(df)
                        interleaving_types.append(it)
                    except Exception as e:
                        print(f"    ⚠️ Error for '{it}': {e}")
                        continue

                if not dfs:
                    print(f"ℹ️ No valid tree data could be loaded from {label} history files.")
                    return

                # ── 5. Plot all types as a multi-sunburst ──
                safe_label = label.replace('/', '_').replace(' ', '_')
                output_path = os.path.join(self.output_dir, f"divitree_history_{safe_label}.html")
                fig, saved_path = self.tree_plotter.plot_multiple_sunbursts(
                    dfs, interleaving_types, reference_mode, output_path=output_path
                )
                if fig is not None:
                    display(fig)
                    print(f"✅ History chart saved to: {saved_path}")
                else:
                    print("⚠️ plot_multiple_sunbursts returned no figure.")

            except Exception as e:
                print(f"❌ Error displaying {label} DiviTree history: {e}")
                import traceback; traceback.print_exc()

    def _update_dropdown_bottom(self):
        """col1 row2 placeholder — Extract Codes & Show Chart button removed; kept empty so other row2 columns stay aligned."""
        self.dropdown_bottom.children = []
        self.dropdown_bottom.layout = widgets.Layout(width='100%', overflow='visible')

    def on_divitree_history_clicked(self, b):
        """
        'Bibliographic Actions Retrieval' button — plots history for
        bibliographic types only: Priorities, Applications, Parents,
        Publications, Citations, Classifications, Parties.
        """
        self._show_divitree_history(
            self.divitree_history_output,
            self.BIBLIO_TYPES,
            'Bibliographic'
        )

    def on_event_codes_retrieval_clicked(self, b):
        """
        'Event / Procedural Actions Retrieval' button (col2 bottom).
        Shows DiviTree history for event/procedural types only:
        Legal Events, Procedural Codes, Bibliographic Register Codes,
        Events Register Codes, Procedural Steps, UPP Codes.
        """
        self._show_divitree_history(
            self.event_codes_retrieval_output,
            self.EVENT_TYPES,
            'Event/Procedural'
        )

    def on_extract_codes_clicked(self, b):
        """
        'Extract Codes & Show Chart' button (col1 bottom, below the dropdown).
        Lists *_tree.txt files, lets the user pick one, extracts event/procedural
        codes into the dropdown, and displays the matching chart.
        """
        self.extract_codes_output.layout.display = ''
        with self.extract_codes_output:
            clear_output(wait=True)
            try:
                if not hasattr(self, 'output_dir') or not self.output_dir:
                    print("⚠️ No output directory found. Please process a patent first.")
                    return

                import glob
                tree_files = sorted(glob.glob(os.path.join(self.output_dir, "*_tree.txt")))

                if not tree_files:
                    print("⚠️ No tree files found. Please process a patent first.")
                    return

                file_options = {os.path.basename(f): f for f in tree_files}

                file_selector = widgets.Dropdown(
                    options=list(file_options.keys()),
                    description='Tree file:',
                    style={'description_width': 'auto'},
                    layout=widgets.Layout(width='500px')
                )
                extract_button = widgets.Button(
                    description="Extract Codes & Show Chart",
                    layout=widgets.Layout(width='240px', height='32px', margin='4px')
                )

                # Clear persistent outputs so previous results don't linger
                self.event_codes_result_output.clear_output()
                self.event_codes_chart_display.clear_output()

                def on_extract(btn):
                    selected_path = file_options[file_selector.value]

                    # --- Codes ---
                    with self.event_codes_result_output:
                        clear_output(wait=True)
                        legal_codes = self.extract_legal_event_codes(selected_path)
                        proc_codes  = self.extract_procedural_codes(selected_path)
                        all_codes   = sorted(set(legal_codes) | set(proc_codes))
                        if all_codes:
                            print(f"📋 {len(all_codes)} code(s) found in {file_selector.value}:")
                            for code in all_codes:
                                print(f"   {code}")
                            self.ORAP_tree_event_code_dropdown.options = all_codes
                            self.ORAP_tree_event_code_dropdown.value   = ()
                            print("\n✅ Codes loaded into 'Select Event / Procedural Codes' dropdown.")
                        else:
                            print(f"⚠️ No codes found in {file_selector.value}.")

                    # --- Chart ---
                    with self.event_codes_chart_display:
                        clear_output(wait=True)
                        html_basename = os.path.splitext(file_selector.value)[0] + '.html'
                        html_path     = os.path.join(self.output_dir, html_basename)
                        if os.path.exists(html_path):
                            with open(html_path, 'r', encoding='utf-8') as f:
                                html_content = f.read()
                            import base64
                            encoded = base64.b64encode(html_content.encode()).decode()
                            iframe = (
                                f'<iframe src="data:text/html;base64,{encoded}" '
                                f'width="100%" height="900px" frameborder="0" '
                                f'style="border:none;"></iframe>'
                            )
                            display(HTML(f'<b>Chart:</b> <code>{html_basename}</code><br>{iframe}'))
                        else:
                            print(f"⚠️ No matching chart found: {html_basename}")
                            print(f"   (Chart is generated when an action button is clicked)")

                extract_button.on_click(on_extract)

                display(widgets.HBox([file_selector, extract_button]))
                display(self.event_codes_result_output)
                display(self.event_codes_chart_display)

            except Exception as e:
                print(f"❌ Error during Extract Codes & Show Chart: {e}")
                import traceback; traceback.print_exc()


    def on_related_files_clicked(self, b):
        """
        Called when "Related Files Search" is pressed.
        Uses RelatedFilesSearcher to run OPS published_data_search queries
        (strategies §7.5.3.1 – §7.5.3.6 from OPS Lesson 6) and displays:
          1. A summary table of related publications (including the input file).
          2. A multi-sunburst chart for each found publication that has a
             typed snapshot ({pub_number}_{type}_first_output.txt) on disk.
        """
        if not hasattr(self, 'record') or self.record is None:
            with self.related_files_output:
                clear_output(wait=True)
                print("⚠️  Please process a patent first before running Related Files Search.")
            self.related_files_output.layout.display = ''
            return

        self.related_files_output.layout.display = ''
        with self.related_files_output:
            clear_output(wait=True)
            try:
                from patent_analysis.related_files_searcher import RelatedFilesSearcher

                searcher = RelatedFilesSearcher(
                    ops_client   = self.record.client,
                    rec_input    = self.rec_input,
                    filtered_df  = self.filtered_app_numbers,
                    family_root  = self.familyRoot,
                    models       = self.models,
                    exceptions   = self.exceptions,
                    reference_type = self.reference_type_widget.value,
                    country_code   = self.country_widget.value,
                )
                results_df = searcher.run()

                if results_df.empty:
                    print("ℹ️ No related applications found.")
                else:
                    print(f"\n📋 Summary — {len(results_df)} related file(s) found:")
                    print(results_df.to_string(index=False))

                    # Save results CSV alongside other DiviTree outputs
                    output_path = os.path.join(self.output_dir, "related_files_results.csv")
                    results_df.to_csv(output_path, index=False)
                    print(f"\n💾 Results saved to: {output_path}")

                    # ── Plot multi-sunburst for found publications ────────────────
                    print(f"\n⏳ Loading sunburst charts for related files …")
                    reference_mode = self.reference_type_widget.value

                    search_dirs = []
                    for candidate in [
                        "/home/jovyan/output",
                        "/home/jovyan/filtered_output",
                        self.output_dir,
                        getattr(self.tree_plotter, 'workdir', None),
                    ]:
                        if candidate and os.path.isdir(candidate) and candidate not in search_dirs:
                            search_dirs.append(candidate)

                    # Collect available (pub_number, interleaving_type) → txt_path
                    pub_numbers = results_df["pub_number"].tolist()
                    dfs_charts = []
                    interleaving_types_charts = []

                    # Only load snapshots for the two informative chart types:
                    # "initial" (bare tree overview) and "Parties" (inventors/applicants).
                    DISPLAY_TYPES = {"initial", "parties"}

                    # Identify interleaving types from sunburst html files on disk,
                    # restricted upfront to DISPLAY_TYPES to avoid loading everything.
                    type_order = list(COLOR_MAP.keys())
                    known_types: list = []
                    for d in search_dirs:
                        if not os.path.isdir(d):
                            continue
                        for fname in os.listdir(d):
                            if fname.startswith('sunburst_plot_') and fname.endswith('.html'):
                                it = fname[len('sunburst_plot_'):-len('.html')].replace('_', ' ')
                                if it not in known_types and it.lower() in DISPLAY_TYPES:
                                    known_types.append(it)
                    known_types.sort(
                        key=lambda t: (type_order.index(t) if t in type_order else len(type_order), t)
                    )

                    # Track which pubs were matched by a typed snapshot
                    matched_pubs: set = set()

                    for pub in pub_numbers:
                        pub_clean = re.sub(r"[^A-Za-z0-9]", "", pub)
                        for it in known_types:
                            safe_type = it.replace(' ', '_')
                            # Typed snapshot: {pub_clean}_{safe_type}_first_output.txt
                            # e.g. EP4408276_Applications_first_output.txt
                            prefix = f"{pub_clean}_"
                            suffix = f"_{safe_type}_first_output.txt"
                            for d in search_dirs:
                                matches = sorted(
                                    [os.path.join(d, f) for f in os.listdir(d)
                                     if f.startswith(prefix) and f.endswith(suffix)],
                                    key=os.path.getmtime, reverse=True
                                )
                                if matches:
                                    txt_path = matches[0]
                                    print(f"  ✅ '{pub}' / '{it}'  ←  {os.path.basename(txt_path)}")
                                    try:
                                        tree_data, _, detected_rm = self.tree_plotter.read_tree_data(
                                            txt_path, it, reference_mode
                                        )
                                        if tree_data is None:
                                            print(f"    ⚠️ read_tree_data returned None, skipping.")
                                            continue
                                        df_c = self.tree_plotter.create_grouped_items_df(tree_data)
                                        if df_c is None or df_c.empty:
                                            print(f"    ⚠️ Empty DataFrame, skipping.")
                                            continue
                                        df_c['interleaving_type'] = str(it).capitalize()
                                        df_c['reference_mode'] = str(detected_rm or reference_mode).capitalize()
                                        df_c['pub_number'] = pub
                                        dfs_charts.append(df_c)
                                        interleaving_types_charts.append(it)
                                        matched_pubs.add(pub)
                                    except Exception as e:
                                        print(f"    ⚠️ Error for '{pub}'/'{it}': {e}")
                                    break  # use first matching directory

                    # ── Fallback: bare {pub_clean}_first_output.txt (initial Submit, no action pressed) ──
                    for pub in pub_numbers:
                        if pub in matched_pubs:
                            continue   # already have a typed snapshot for this pub
                        pub_clean = re.sub(r"[^A-Za-z0-9]", "", pub)
                        bare_name = f"{pub_clean}_first_output.txt"
                        for d in search_dirs:
                            bare_path = os.path.join(d, bare_name)
                            if os.path.isfile(bare_path):
                                it_fallback = "initial"
                                print(f"  ✅ '{pub}' / '{it_fallback}' (bare)  ←  {bare_name}")
                                try:
                                    tree_data, _, detected_rm = self.tree_plotter.read_tree_data(
                                        bare_path, it_fallback, reference_mode
                                    )
                                    if tree_data is None:
                                        print(f"    ⚠️ read_tree_data returned None, skipping.")
                                        break
                                    df_c = self.tree_plotter.create_grouped_items_df(tree_data)
                                    if df_c is None or df_c.empty:
                                        print(f"    ⚠️ Empty DataFrame, skipping.")
                                        break
                                    df_c['interleaving_type'] = it_fallback.capitalize()
                                    df_c['reference_mode'] = str(detected_rm or reference_mode).capitalize()
                                    df_c['pub_number'] = pub
                                    dfs_charts.append(df_c)
                                    interleaving_types_charts.append(it_fallback)
                                except Exception as e:
                                    print(f"    ⚠️ Error for '{pub}' bare snapshot: {e}")
                                break  # use first matching directory

                    if dfs_charts:
                        output_path_chart = os.path.join(self.output_dir, "related_files_multi.html")
                        fig, saved_path = self.tree_plotter.plot_multiple_sunbursts(
                            dfs_charts, interleaving_types_charts, reference_mode,
                            output_path=output_path_chart
                        )
                        if fig is not None:
                            display(fig)
                            print(f"✅ Related files chart saved to: {saved_path}")
                        else:
                            print("⚠️ plot_multiple_sunbursts returned no figure.")
                    else:
                        print("ℹ️ No typed snapshots found for related files — "
                              "run each publication through DiviTree first to generate charts.")

            except Exception as e:
                print(f"❌ Error during Related Files Search: {e}")
                import traceback; traceback.print_exc()

    # ✅ Step 5: Finalize Layout
    def build_main_layout(self):
        """
        VBox (main_layout)
        │
        ├── VBox (input_ui)
        ├── HBox (outputs_container) <-- text_output + chart_output            
        ├── HBox (result_hbox) [hidden until results available]    
              ├── VBox (tree_event_code_dropdown_box) <-- dropdown area, filled by flag/data
              ├── VBox (checkboxes_buttons_container) <-- checkboxes + process/action buttons
        """        

        # Ensure the three sections exist (even empty at start)
        self.dropdown_section = widgets.VBox([], layout=widgets.Layout(padding='0px', margin='0px', gap='20px'))
        self.subaction_section = widgets.VBox([], layout=widgets.Layout(padding='0px', margin='0px', gap='20px'))
        self.first_vbox = widgets.VBox([], layout=widgets.Layout(padding='0px', margin='0px', gap='20px'))
        self.actions_box = widgets.VBox([], layout=widgets.Layout(padding='0px', margin='0px', gap='20px'))
        
        # DROPDOWN BOX (will be populated dynamically)
        self.tree_event_code_dropdown_box = widgets.VBox([])
        
        # # CHECKBOXES/BUTTONS BOX (will be populated dynamically)
        self.checkboxes_buttons_container = widgets.VBox([])

        # Row 2 of the GridBox — bottom buttons + placeholder for col1
        # Populated with actual buttons in display_checkboxes_and_buttons()
        self.dropdown_bottom  = widgets.VBox([], layout=widgets.Layout(height='36px'))  # col1 — Extract Codes & Show Chart (added when dropdown visible)
        self.subaction_bottom = widgets.VBox([], layout=widgets.Layout(height='36px'))  # col2
        self.firstcol_bottom  = widgets.VBox([], layout=widgets.Layout(height='36px'))  # col3
        self.actions_bottom   = widgets.VBox([], layout=widgets.Layout(height='36px'))  # col4

        self.result_hbox = widgets.GridBox(
            [
                self.dropdown_section,        # row1 col1
                self.subaction_section,       # row1 col2
                self.first_vbox,              # row1 col3
                self.actions_box,             # row1 col4
                self.dropdown_bottom,         # row2 col1 — placeholder (empty)
                self.subaction_bottom,        # row2 col2 — Event/Procedural Codes Retrieval
                self.firstcol_bottom,         # row2 col3 — Related Files Search
                self.actions_bottom,          # row2 col4 — DiviTree History
            ],
            layout=widgets.Layout(
                width='100%',
                grid_template_columns='repeat(4, minmax(0, 1fr))',
                grid_template_rows='1fr auto',
                column_gap='120px',
                row_gap='30px',
                padding='10px 30px',
                margin='0 0 20px 0',
                align_items='stretch',
                overflow='visible',
            )
        )

        # Keep the rest of the existing layout the same:
        self.main_layout = widgets.VBox([
            self.input_ui,
            self.output_container,
            self.result_hbox,
            self.extra_section        # ← DiviTree History + Related Files Search (hidden until result_hbox shown)
        ])

        display(self.main_layout)
        
        # ✅ Now safe to initialize doc number display
        self.update_doc_number({'new': self.reference_type_widget.value})
        
    # ✅ Step 6: Call These in __init__            
    def __init__(self, OPSClient, models, exceptions):
        self.workdir = "/home/jovyan/output"
        # self.client: OPSClientWrapper = OPSClientWrapper(key=os.getenv("OPS_KEY"), secret=os.getenv("OPS_SECRET"))
        self.client: OPSClient = OPSClient(key=os.getenv("OPS_KEY"), secret=os.getenv("OPS_SECRET"))
        self.models     = models      # stored for RelatedFilesSearcher
        self.exceptions = exceptions  # stored for RelatedFilesSearcher
        self.tree = None
        self.root = None
        self.listAPs = None
        self.listORAPs = None
        self.filtered_app_numbers = None
        self.familyRoot = None
        self.record = None
        self.initFlag = None
        self.is_button_clicked = False
        self.country_checkboxes = []
        self.rec_input = []
        self.last_flag = None
        self.last_interleaving_type = None
        self.tree_processor = None
        # self.process_all_button_displayed = False
        self.output_dir = "/home/jovyan/filtered_output"
        os.makedirs(self.output_dir, exist_ok=True)
        self.tree_plotter = DiviTreePlotter(workdir=self.output_dir)
        
        # Initialize widgets
        self.init_input_widgets()
        self.init_output_widgets()
        self.build_input_ui()
        self.setup_dropdowns_and_buttons()
        self.init_extra_buttons()     # ← new: DiviTree History + Related Files Search
        self.build_main_layout()
        self.reset_ui()

    @property
    def selected_tree_type(self):
        """Returns the tree type ('priority' or 'ORAP') based on the selected dropdown value."""
        return "priority" if "priority" in self.tree_type_selector.value.lower() else "ORAP"
    
    def update_doc_number(self, change):
        """
        Updates the doc_number widget based on the selected reference type.
        """
        # Clear the output before displaying checkboxes
        with self.output:
            clear_output(wait=True)
        
        # Reset checkboxes when reference type changes
        self.country_checkboxes = []
        # self.process_all_button_displayed = False
        
        if change['new'] == 'application':
            self.doc_number_widget.value = '09164213' # '9015198', '09164213', '13168514', '13855087', '21958007', '91913039', '24209872'
            self.country_widget.value = 'EP' # 'GB'
            self.kind_widget.value = 'A'
            self.constituents_widget.value = None
            self.output_type_widget.value = 'raw'
        elif change['new'] == 'publication':
            self.doc_number_widget.value = '2101496' # 2101496 B1 , 4477148 A2, 3751504 B1, 3242420 A1, 0589877 B2, 1331351 B1
            self.country_widget.value = 'EP'
            self.kind_widget.value = 'B1' # 'B1', 'A2', 'A1', 'B2'
            self.constituents_widget.value = 'legal'
            self.output_type_widget.value = 'dataframe'
        else:
            self.doc_number_widget.value = ''
            self.country_widget.value = ''
            self.kind_widget.value = ''
            self.constituents_widget.value = None
            self.output_type_widget.value = 'raw'
            
        # Ensure the two left-column sections exist
        if not hasattr(self, "dropdown_section"):
            self.dropdown_section = widgets.VBox(
                [],
                layout=widgets.Layout(padding='0px', margin='0px', gap='20px', width='430px', align_items='flex-start')
            )
        if not hasattr(self, "subaction_section"):
            self.subaction_section = widgets.VBox(
                [],
                layout=widgets.Layout(padding='0px', margin='0px', gap='20px', width='430px', align_items='flex-start')
            )
        if not hasattr(self, "tree_event_code_dropdown_box"):
            self.tree_event_code_dropdown_box = widgets.VBox([])
            
        # Mount once (idempotent)
        self.tree_event_code_dropdown_box.children = [self.dropdown_section, self.subaction_section]

        # Toggle only the sub-action section; leave the dropdown section untouched        
        if self.reference_type_widget.value == "publication":
            sub_action_title = widgets.HTML("<h4 style='margin-bottom:20px;'>Legal and Procedural Actions</h4>")
            self.subaction_section.children = [sub_action_title] + self.sub_action_buttons
            self.subaction_section.layout.display = ''
            # ✅ Show the Event / Procedural Actions Retrieval button for publications
            if hasattr(self, 'event_codes_retrieval_button'):
                self.event_codes_retrieval_button.layout.display = ''            
        else:
            self.subaction_section.children = []
            self.subaction_section.layout.display = 'none'
            # ✅ Hide the Event / Procedural Actions Retrieval button for applications
            if hasattr(self, 'event_codes_retrieval_button'):
                self.event_codes_retrieval_button.layout.display = 'none'            

    def on_event_code_selected(self, change, source='ORAP'):
        """Event handler for dropdown changes. Show plot and text side-by-side."""
        
        # ── DEBUG helper: writes to text_output immediately ──
        def _dbg(msg):
            with self.text_output:
                print(f"🔍 DBG: {msg}", flush=True)

        _dbg(f"ENTRY — source={source!r}, new={change.get('new')!r}")

        # --- 1️⃣ Normalize selection ---
        selected_codes_raw = change.get('new') or []
        if selected_codes_raw is None:
            selected_codes_raw = []
        elif isinstance(selected_codes_raw, str):
            selected_codes_raw = [selected_codes_raw]
        selected_codes = [c.split(' - ')[0].strip() for c in selected_codes_raw if c]
        _dbg(f"STEP1 — selected_codes={selected_codes!r}")

        # --- 2️⃣ Determine tree type via source (reliable) ---
        if source == 'ORAP':
            self.selected_orap_codes = selected_codes
            tree_type = "ORAP"
            self.tree_type_selector.value = 'with ORAP numbers (recommended)'
        elif source == 'priority':
            self.selected_prio_codes = selected_codes
            tree_type = "priority"
            self.tree_type_selector.value = 'with priority numbers'
        else:
            _dbg(f"STEP2 ABORT — unknown source={source!r}")
            return
        _dbg(f"STEP2 — tree_type={tree_type!r}")

        # --- 3️⃣ Load tree file ---
        _dbg(f"STEP3 — tree_processor={getattr(self,'tree_processor',None)!r}")
        if not getattr(self, 'tree_processor', None):
            _dbg("STEP3 ABORT — no tree loaded yet")
            return
        tree_path = (
            getattr(self.tree_processor, "priority_tree_file_path", None)
            if tree_type == 'priority'
            else getattr(self.tree_processor, "ORAP_tree_file_path", None)
        )
        _dbg(f"STEP3 — tree_path={tree_path!r}, exists={bool(tree_path and os.path.exists(tree_path))}")
        if not tree_path or not os.path.exists(tree_path):
            with self.text_output:
                self.text_output.clear_output(wait=True)
                print(f"❌ No valid {tree_type} tree file found.")
            return

        # --- prepopulate persistent maps from full tree if empty (only once) ---
        try:
            # build full_tooltip_map on tree_plotter (not only when persistent_color_map empty)
            if not getattr(self.tree_plotter, "full_tooltip_map", None):
                full_tree_data, interleaving_type, reference_mode = self.tree_plotter.read_tree_data(tree_path, self.last_interleaving_type, self.reference_type_widget.value)
                # print("on_event_code_selected 1.: self.last_interleaving_type, interleaving_type:", self.last_interleaving_type, interleaving_type)
                if full_tree_data is not None:
                    full_df = self.tree_plotter.create_grouped_items_df(full_tree_data)

                    # clean ANSI from tooltip-related cols
                    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
                    for col in ["id", "label", "hovertext", "tooltip", "type"]:
                        if col in full_df.columns:
                            full_df[col] = full_df[col].apply(lambda x: ansi_escape.sub('', str(x)))
                    full_df['type'] = full_df['type'].str.strip()

                    # populate persistent color/type maps
                    _ = self.tree_plotter.assign_branch_colors(full_df, fromWhere='prepopulate')

                    # build mapping keyed by the SAME formatted_id used later
                    full_df['formatted_id'] = full_df['id'].apply(
                        lambda x: str(x).split(":")[0] if ": [" in str(x) else str(x)
                    )
                    
                    if 'tooltip' not in full_df.columns:
                        full_df['tooltip'] = ''

                    # build cleaned id -> tooltip map
                    # key on cleaned original 'id' (not formatted_id) to ensure same key for filtered rows
                    full_df['id_clean'] = full_df['id'].apply(lambda x: ansi_escape.sub('', str(x)).strip())  # CHANGED
                    full_df['tooltip_clean'] = full_df['tooltip'].apply(lambda x: ansi_escape.sub('', str(x)))
                    # store on the tree_plotter so _prepare_sunburst_df can use it
                    self.tree_plotter.full_tooltip_map = dict(zip(full_df['id_clean'], full_df['tooltip_clean']))  # CHANGED
                    # print("Prepopulated persistent_color_map with", len(self.tree_plotter.persistent_color_map), "entries")
                    # print("Stored full_tooltip_map with", len(self.tree_plotter.full_tooltip_map), "entries")
        except Exception as e:
            print("Warning: prepopulate persistent maps failed:", e)
            
        # --- 4️⃣ Filter tree if codes selected ---
        _dbg(f"STEP4 — filtering for codes={selected_codes!r}")
        display_path = tree_path if not selected_codes else self.filter_tree_by_event_code(tree_path, selected_codes)
        _dbg(f"STEP4 — display_path={display_path!r}, exists={bool(display_path and os.path.exists(display_path))}")
        if not display_path or not os.path.exists(display_path):
            with self.text_output:
                self.text_output.clear_output(wait=True)
                print("⚠️ Filtered file not found or contains no matching events.")
            return

        # --- 5️⃣ Display preview in text_output ---
        with open(display_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            
        # Print tree preview in self.text_output widget
        with self.text_output:
            self.text_output.clear_output(wait=True)
            print(f"\n=== Processing {tree_type.upper()} tree in the reference mode {self.reference_type_widget.value} ===")
            print("Handler fired for event:", source, "(triggered dropdown)")                
            if not selected_codes:
                print(f"ℹ️ No legal event codes selected. Displaying full {tree_type} tree with all legal events.")
            else:
                print(f"🎯 Filtering for event code(s): {', '.join(selected_codes)} in {tree_type} tree.")                
            print(f"Tree has {len(lines)} lines after filtering.\nPreview:\n{''.join(lines)}")
            print(f"The tree is interleaving data of the type {self.last_interleaving_type}")                        
            # ── Auto-copy tree text to clipboard (text embedded directly in JS)
            self._auto_copy_to_clipboard(''.join(lines))
            
        # --- 6️⃣ Prepare figure data ---
        tree_data, interleaving_type, reference_mode = self.tree_plotter.read_tree_data(display_path, self.last_interleaving_type, self.reference_type_widget.value)
        
        # print("on_event_code_selected 2.: reference_mode:", reference_mode)
        if tree_data is None:
            with self.text_output:
                print("⚠️ Failed to parse tree data.")
            return
            
        df = self.tree_plotter.create_grouped_items_df(tree_data)


        # ✅ Ensure tooltip exists before cleaning
        if "tooltip" not in df.columns:
            df["tooltip"] = ""
            
        df['interleaving_type'] = str(interleaving_type).capitalize()
        df['reference_mode'] = str(reference_mode).capitalize()
        
        # ✅ Strip ANSI color codes from tooltip-related fields
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        for col in ["id", "label", "hovertext", "tooltip"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: ansi_escape.sub('', str(x)))

        # print(f"📝 DataFrame built for {tree_type}, rows={len(df)}")
        # print("Columns present:", df.columns.tolist())
        # print("Sample tooltips:", df["tooltip"].head().tolist())

        # --- 8️⃣ Generate HTML output ---
        output_file = os.path.join(self.output_dir, f"{'filtered' if selected_codes else 'full'}_{tree_type}_tree.html")

        _dbg("STEP8 — calling plot_sunburst_for_widget")
        try:
            # --- 8️⃣b Build figure with proper centered controls ---
            fig, timestamps, df_prepared, output_file, hovertemplate = \
                self.tree_plotter.plot_sunburst_for_widget(
                    df, interleaving_type, reference_mode, output_file
                )

            # FigureWidget not available in this environment — embed as self-contained HTML.
            # The full Plotly HTML (chart + Play/Pause + slider) is injected inline so
            # everything renders inside chart_output and stays within the HBox frame.
            fig_html = fig.to_html(
                full_html=False,
                include_plotlyjs='cdn',
                config={'responsive': True},
            )

            # Wrap in a div that fills the panel width and constrains height
            chart_div = f"""
                        <div style="width:100%; height:900px; overflow:hidden;">
                           {fig_html}
                        </div>
                    """

            # --- 9️⃣ Display inside chart_output ---
            with self.chart_output:
                self.chart_output.clear_output(wait=False)
                self.chart_output.layout.overflow   = 'visible'
                self.chart_output.layout.height     = 'auto'
                self.chart_output.layout.min_height = '990px'

                html_header = (
                    f'✅ Chart saved to: <code>{output_file}</code><br>'
                    f'📂 To open it in a new browser tab, right-click the file in the left sidebar and choose:<br>'
                    f'<b><i>+ Open in New Browser Tab</i></b>'
                )
                display(HTML(html_header))
                # Use srcdoc iframe so injected <script> tags execute.
                # display(HTML(...)) in a Jupyter Output widget sandboxes scripts.
                display_chart_inline(output_file, height='900px')
                self.chart_output.layout.height = self.text_output.layout.height

        except Exception as e:
            import traceback as _tb
            _dbg(f"STEP8 EXCEPTION: {e}")
            with self.chart_output:
                self.chart_output.clear_output(wait=False)
                print(f"Chart could not be updated: {e}")
                print(_tb.format_exc())                
                
        # --- 1️⃣0️⃣ Ensure container visible ---
        self.output_container.layout.display = ''  # ✅ also show the combined container if hidden
        return None
        
    def update_figure(self, df, interleaving_type, output_file):
        """
        Update or create a FigureWidget in-place with new data from df.
        Displays the figure inside self.chart_output output widget.
        """
        # Generate new figure usingNNn tree_plotter
        fig_widget = self._fig_widget
        # fig, _ = self.tree_plotter.plot_sunburst(df, interleaving_type, output_file)
        # fig, _ = self.tree_plotter.plot_multiple_sunbursts(df, interleaving_type, output_file)
        fig, _ = self.tree_plotter.plot_sunburst_over_time(df, interleaving_type, output_file)
        # Update traces individually to avoid assignment errors
        for i, trace in enumerate(fig.data):
            if i < len(fig_widget.data):
                fig_widget.data[i].update(trace)
            else:
                fig_widget.add_trace(trace)
        # Remove extra traces if any
        while len(fig_widget.data) > len(fig.data):
            fig_widget.data = fig_widget.data[:-1]
        
        # Update layout fully
        fig_widget.layout = fig.layout

        # Display or update output in the chart_output container widget
        with self.chart_output:
            self.chart_output.clear_output(wait=False)
            display(fig_widget)
            self.chart_output.layout.height = self.text_output.layout.height

        return fig_widget

    def filter_tree_by_event_code(self, input_path, selected_codes):
        """Filter tree file lines by selected legal event codes, writing to filtered output file."""

        if not selected_codes:
            print("⚠️ No event codes selected.")
            return None

        # selected_codes_set = set(selected_codes)
        selected_codes_set = set(
            code.strip().strip(',').replace(" ", "_")
            for code in selected_codes if code.strip()
        )

        filename = os.path.basename(input_path).lower()
        if "_first_output.txt" in filename:
            output_file = 'filtered_ORAP_tree.txt'
        elif "_second_output.txt" in filename:
            output_file = 'filtered_priority_tree.txt'
        else:
            print(f"❌ Could not determine tree type from input path: {input_path}")
            return None

        output_path = os.path.join(self.output_dir, output_file)

        # Regex pattern to catch all ANSI escape sequences like \x1b[38;5;214m or \x1b[0m
        ansi_escape = re.compile(r'\x1b\[[0-9;,]*m')

        keep_block = False  # keep child lines if parent matched
        
        try:    
            with open(input_path, 'r', encoding='utf-8') as fin, \
                 open(output_path, 'w', encoding='utf-8') as fout:
                for line in fin:
                    stripped = line.strip()
                    if not stripped:
                        continue

                    # Remove ANSI codes before matching
                    clean_line = ansi_escape.sub('', line)
                    if stripped.startswith('.'):
                        match = re.match(r'^[.\s]*(\[[A-Z]{2}\]\s+)?([A-Z0-9]+[A-Z0-9\s_-]*):', clean_line)
                        # print("match", match)
                        if match:
                            # normalized_code = match.group(1).replace(" ", "_")  # Replace spaces with underscores
                            country_code_part = match.group(1) or ""  # "[EP] "
                            event_code_part = match.group(2)          # "AC"
                            full_code = f"{country_code_part}{event_code_part}".strip()  # "[EP] AC"
                            
                            normalized_for_matching = full_code.replace(" ", "_")  # "[EP]_AC"
                            
                            # ✅ Only keep block if this parent matches the selection
                            if normalized_for_matching in selected_codes_set:                           
                                # print("normalized_for_matching:", normalized_for_matching)
                                # print("selected_codes_set:", selected_codes_set)
                                keep_block = True
                                fout.write(line)  # ✅ Write original, unchanged                                
                        else:
                            keep_block = False
                    elif stripped.startswith(','):
                        # Always keep application/publication lines
                        fout.write(line)  # ✅ With ANSI colors
                    else:
                        # Keep child/description lines if parent code matched
                        if keep_block:
                            fout.write(line) # ✅ With ANSI colors
                        
            # print(f"✅ Filtered tree saved to {output_path}")
            return output_path
        except Exception as e:
            # Safe: don't reference clean_line outside the loop
            print(f"❌ Error filtering codes {selected_codes}: {e}")
            return None
                        
    def process_with_initFlag(self, flag):
        """
        Process the patent tree data for the selected tree type and initFlag.
        Output: textual output goes to self.text_output, Plotly chart to self.chart_output.
        """
        self.last_flag = flag
        self.last_interleaving_type = self.flag_to_interleaving.get(flag)  # ✅ save it
        self.is_button_clicked = True

        # Clear bar 1 immediately (wait=False = instant, not deferred)
        self.output.clear_output(wait=False)
        
        # --- Helper to clean ANSI escape sequences ---
        def clean_ansi(text):
            return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)

        # Define flags
        show_codes_flag = flag in [
            'Show_legal_events', 'Show_procedural_codes', 'Show_biblio',
            'Show_events', 'Show_procedural_steps', 'Show_upp'
        ]
        
        # reference_mode = "publication" if self.reference_type_widget.value == "publication" else "application"

        # All textual output goes into self.text_output
        with self.text_output:
            clear_output(wait=True)
            print(f"Processing tree type labelled: {self.selected_tree_type} with flag: {flag}")
            if show_codes_flag and self.reference_type_widget.value != "publication":
                print("⚠️ Legal Events / Procedural Codes can only be retrieved if the Reference Type is set to 'publication'.")
                self.output_container = widgets.HBox(
                    [self.text_output, self.chart_output],
                    layout=widgets.Layout(align_items='flex-start', gap='20px', padding='10px')
                )
                return

        # Bar appears below "Processing tree type…" while tree is being built
        self._show_progress(
            f"⏳ Retrieving <b>{self.flag_to_interleaving.get(flag, flag)}</b> data…",
            target=self.text_output
        )

        try:
            # --- 1️⃣ Initialize TreeProcessor
            tree_processor = TreeProcessor(
                tree=self.tree, root=self.root, initFlag=flag,
                listAPs=self.listAPs, listORAPs=self.listORAPs,
                df=self.filtered_app_numbers, familyRoot=self.familyRoot,
                interleaving_type=self.flag_to_interleaving.get(flag)
            )
            self.tree_processor = tree_processor
            
            # Remove old tree files
            for path in [tree_processor.ORAP_tree_file_path, tree_processor.priority_tree_file_path]:
                if os.path.exists(path):
                    os.remove(path)
                    
            root = tree_processor.process_tree(self.selected_tree_type, self.reference_type_widget.value)
            tree_path = (
                tree_processor.priority_tree_file_path
                if self.selected_tree_type == "priority"
                else tree_processor.ORAP_tree_file_path
            )
            
            with self.text_output:
                clear_output(wait=True)   # removes "Processing…" + progress bar
                if os.path.exists(tree_path):
                    with open(tree_path, 'r') as f:
                        content = f.read()
                    display(HTML(
                        f'<span style="font-family:monospace;font-size:13px;display:block">'
                        f'Raw content of {self.selected_tree_type.upper()} tree file:'
                        f'</span>'
                        + self._render_tree_html(content) +
                        f'<span style="font-family:monospace;font-size:13px;display:block">'
                        f'{self.selected_tree_type.upper()} tree contains {len(content.splitlines())} lines.'
                        f'</span>'
                    ))
                    self._auto_copy_to_clipboard(content)
            
                    # Hide both dropdowns then enable/show only one
                    self.ORAP_tree_event_code_dropdown.layout.display = 'none'
                    self.priority_tree_event_code_dropdown.layout.display = 'none'
                    
                    # --- Always clear dropdowns box first ---
                    self.dropdown_section.children = []
                    self.dropdown_section.layout.display = 'none'
                    
                    # --- Always clear dropdowns box and repopulate as needed
                    dropdown_vbox_children = []
                    
                    # Show ONLY the correct dropdown for this tree type
                    if show_codes_flag and self.reference_type_widget.value == "publication":
                        if flag == "Show_legal_events":
                            codes = self.extract_legal_event_codes(tree_path)
                        else:
                            codes = self.extract_procedural_codes(tree_path)
                        
                        if codes:
                            # ✅ Remove ANSI escape sequences and eliminate duplicates
                            clean_codes = list({clean_ansi(c) for c in codes if c})

                            code_defs = {}  # <-- initialize dictionary
                            # Extract definition from `clean_line` if present
                            # Assuming 'clean_line' is something like: "RFEE: Renewal Fee paid", or similar
                            # You might need to adjust the regex depending on actual format
                            for code in clean_codes:
                                definition = ""
                                for line in content.splitlines():
                                    # Remove ANSI escapes for safety
                                    clean_line = clean_ansi(line)
                                    match = re.search(rf"{re.escape(code)}\s*:\s*(.+)", clean_line)
                                    if match:
                                        definition = match.group(1).strip()
                                        break  # take first occurrence
            
                                if code not in code_defs:
                                    code_defs[code] = definition

                            # Create dropdown options as "CODE - Definition" or just CODE if no definition
                            clean_codes = [
                                f"{code} - {definition}" if definition else code
                                for code, definition in code_defs.items()
                            ]
    
                            # Optional: sort alphabetically or numerically
                            clean_codes.sort()

                            # print("clean_codes for dropdown (no duplicates):", clean_codes)
                            dropdown = (
                                self.priority_tree_event_code_dropdown 
                                if self.selected_tree_type == "priority"
                                else self.ORAP_tree_event_code_dropdown
                            )
                            dropdown.options = clean_codes
                            dropdown.value = ()
                            dropdown.layout.display = ""  # show dropdown
                            # ✅ Add title only once here
                            title = widgets.HTML("<h4 style='margin-bottom:20px;'>Select Event / Procedural Codes</h4>")
                            dropdown_vbox_children.append(widgets.VBox([title, dropdown], layout=widgets.Layout(margin='0 0 20px 0')))
                            self.dropdown_section.children = [dropdown_vbox_children[0]]
                            self.dropdown_section.layout.display = ''
                            # ⚠️ DO NOT call _update_extra_buttons_width() here —
                            # layout changes inside `with Output()` are buffered
                            # and don't propagate. Call is made OUTSIDE below.
                        else:
                            print(f"⚠️ No codes found for {self.selected_tree_type} tree.")                                
                            self.dropdown_section.children = []
                            self.dropdown_section.layout.display = 'none'
    
                    # --- Show results HBox (the full row layout) ---
                    self.result_hbox.layout.display = ''
                    self.extra_section.layout.display = ''
                else:                    
                    print(f"Tree file not found: {tree_path}")

            with self.chart_output:
                clear_output(wait=True)
                if self.tree:
                    fig, html_path, html_header = self.run_divitree_chart(tree_path=tree_path)
                    if html_path and os.path.exists(html_path):
                        display(HTML(html_header))
                        display_chart_inline(html_path, height="860px")
                    elif fig is not None:
                        display(HTML(html_header))
                        display(fig)   # graceful fallback only
                    else:
                        print("No chart available (chart generation failed)")
                else:
                    print("No chart available (tree not created)")
        
                self.output_container.layout.display = ''  # show output area    

            # ✅ MUST be outside all `with Output()` contexts.
            # self._update_extra_buttons_width()  # no longer needed — buttons live inside their columns
            self._update_dropdown_bottom()  # sync Extract Codes button visibility with dropdown state

            # --- 2️⃣ Prepare animated chart trigger button for sub_action_flags ---
            sub_action_flags = [
                'Show_legal_events', 'Show_procedural_codes', 'Show_biblio',
                'Show_events', 'Show_procedural_steps', 'Show_upp'
            ]

            if flag in sub_action_flags:
                try:
                    tree_data, interleaving_type, reference_mode = self.tree_plotter.read_tree_data(
                        tree_path, self.last_interleaving_type, self.reference_type_widget.value
                    )
                    if tree_data is not None:
                        df = self.tree_plotter.create_grouped_items_df(tree_data)


                        df['interleaving_type'] = str(interleaving_type).capitalize()
                        df['reference_mode'] = str(reference_mode).capitalize()
                        
                        self.chart_output.layout.overflow = 'visible'
                        self.chart_output.layout.height = 'auto'
                        self.chart_output.layout.min_height = '900px'
                        self.output_container.layout.display = ''

                        # with self.chart_output:
                        #     output_box = widgets.Output()

                        #     def on_animate_clicked(_):
                        #         with output_box:
                        #             output_box.clear_output(wait=True)
                        #             print("🎞️ Generating animated Sunburst ... please wait ...")
                        #             try:
                        #                 fig_anim, _ = self.tree_plotter.plot_sunburst_over_time(
                        #                     df, interleaving_type, reference_mode
                        #                 )
                        #                 display(fig_anim)
                                        
                        #                 # Optional: auto-scroll to animation
                        #                 from IPython.display import Javascript, display as js_display
                        #                 js_display(Javascript("window.scrollTo(0, document.body.scrollHeight);"))
                                        
                        #             except Exception as e:
                        #                 print(f"⚠️ Could not generate animation: {e}")
                                        
                        #     animate_button = widgets.Button(
                        #         description="▶ Start Animated Sunburst",
                        #         tooltip="Show tree growth over time",
                        #         button_style='success',
                        #         layout=widgets.Layout(width='auto', margin='10px 0px 10px 0px')
                        #     )
                            
                        #     animate_button.on_click(on_animate_clicked)
                        #     display(widgets.VBox([animate_button, output_box]))

                        #     # Adjust layout
                        #     self.chart_output.layout.overflow = 'visible'
                        #     self.chart_output.layout.height = 'auto'
                        #     self.chart_output.layout.min_height = '900px'
                        #     self.output_container.layout.display = ''

                except Exception as e:
                    with self.chart_output:
                        self.chart_output.clear_output(wait=False)
                        print(f"⚠️ Could not prepare animation button: {e}")

        except Exception as e:
            with self.text_output:
                clear_output(wait=True)
                print(f"❌ Error processing tree with initFlag {flag}: {e}")
            with self.chart_output:
                clear_output(wait=True)
                print("No chart created due to error.")
            import traceback
            traceback.print_exc()
        
    def extract_legal_event_codes(self, file_path):
        """
        Parses a tree file to extract unique legal event codes, defined as:
        Lines that match the pattern '. . CODE:' where CODE is typically 2–5 characters.
        """
        if not os.path.exists(file_path):
            return []

        _ansi_e = re.compile(r'\x1b\[[0-9;,]*m')
        event_codes = set()
        with open(file_path, 'r') as f:
            for line in f:
                line = _ansi_e.sub('', line).strip()
                if line.startswith('. .') and ':' in line:
                    content = line.lstrip('. ').strip()
                    code_part = content.split(':', 1)[0].strip()
                    code_part = ' '.join(code_part.split())
                    event_codes.add(code_part)
        return sorted(event_codes)
        
    def extract_procedural_codes(self, file_path):
        """
        Parses a tree file to extract unique procedural codes, e.g. from lines like '. . PROC123: ...'.
        """
        if not os.path.exists(file_path):
            return []

        _ansi_e = re.compile(r'\x1b\[[0-9;,]*m')
        proc_codes = set()
        with open(file_path, 'r') as f:
            for line in f:
                line = _ansi_e.sub('', line).strip()
                if line.startswith('. .') and ':' in line:
                    content = line.lstrip('. ').strip()
                    code_part = content.split(':', 1)[0].strip()
                    code_part = ' '.join(code_part.split())
                    proc_codes.add(code_part)
        return sorted(proc_codes)

    def _auto_copy_to_clipboard(self, text: str) -> None:
        """Copy `text` to the clipboard via an injected self-executing JS snippet.

        The text is embedded as a JSON-encoded string literal — all newlines,
        quotes and special characters are safely escaped by json.dumps().
        Must be called from inside a ``with self.text_output:`` block so the
        injected <script> tag lands in the same output cell.
        Shows a 2-second blue toast confirming the copy.
        """
        import json as _json
        js_str = _json.dumps(text)   # e.g.  "\"line1\\nline2\\n\""
        snippet = (
            "<script>(function(){"
            f"var text={js_str};"
            "if(!text||!text.trim())return;"
            "var toast=function(m){"
              "var d=document.createElement('div');"
              "d.textContent=m;"
              "d.style.cssText='position:fixed;top:14px;right:20px;z-index:99999;"
                "background:#1976d2;color:#fff;padding:6px 18px;border-radius:4px;"
                "font:13px sans-serif;transition:opacity 1.2s;opacity:1;';"
              "document.body.appendChild(d);"
              "setTimeout(function(){d.style.opacity='0';"
                "setTimeout(function(){d.remove();},1300);},2000);};"
            "if(navigator.clipboard&&navigator.clipboard.writeText){"
              "navigator.clipboard.writeText(text)"
                ".then(function(){toast('\U0001f4cb Tree copied to clipboard');},"
                "      function(){toast('\u26a0\ufe0f Clipboard blocked — Ctrl+C to copy');});}"
            "else{"
              "var ta=document.createElement('textarea');"
              "ta.value=text;ta.style.cssText='position:fixed;opacity:0;';"
              "document.body.appendChild(ta);ta.focus();ta.select();"
              "try{document.execCommand('copy');toast('\U0001f4cb Tree copied (fallback)');}"
              "catch(e){toast('\u26a0\ufe0f Copy failed');}"
              "document.body.removeChild(ta);}"
            "})()</script>"
        )
        display(HTML(snippet))

    def _compute_button_width(self, labels, base_padding=200, char_px=8, min_width=400):
        max_label_len = max(len(label) for label in labels)
        final_width  = min(max_label_len * char_px + base_padding, min_width)
        # print("final_width:", final_width)
        return f"{final_width}px"
        
    def _update_extra_buttons_width(self) -> None:
        """
        Align the three bottom extra buttons with the result_hbox columns above.

        KEY FACTS:
        1. ipywidgets does NOT support CSS calc() in Layout widths — plain px only.
        2. ipywidgets HBox flex items with display='none' still occupy their full
           width in the layout (they are invisible but not removed from flow).
           So when dropdown_section (col1) is hidden, it still takes up
           bw + 40px (its width + the gap), and the bar must be offset rightward
           by that same amount so col2 buttons stay aligned.

        When col1 VISIBLE : bar left-padding = 10px (matches result_hbox padding)
                            first button width = col1 + gap + col2 = 2*bw + 40
        When col1 HIDDEN  : bar left-padding = 10 + bw + 40  (skip ghost col1+gap)
                            first button width = col2 only = bw
        """
        button_width_str = getattr(self, '_button_width', '400px')
        col_gap = 40       # must match result_hbox gap
        base_pad = 10      # must match result_hbox left padding

        try:
            bw = int(button_width_str.replace('px', '').strip())
        except ValueError:
            bw = 400

        dropdown_visible = (
            hasattr(self, 'dropdown_section') and
            self.dropdown_section.layout.display != 'none' and
            len(self.dropdown_section.children) > 0
        )

        if dropdown_visible:
            left_padding    = base_pad
            first_btn_width = 2 * bw + col_gap   # spans col1 + gap + col2
        else:
            # col1 hidden but still occupies bw + col_gap in flexbox
            left_padding    = base_pad + bw + col_gap   # e.g. 10+400+40 = 450px
            first_btn_width = bw                         # spans col2 only

        self.event_codes_retrieval_button.layout.width = f'{first_btn_width}px'
        self.related_files_button.layout.width         = button_width_str
        self.divitree_history_button.layout.width      = button_width_str

        self.extra_buttons_bar.children = [
            self.event_codes_retrieval_button,
            self.related_files_button,
            self.divitree_history_button,
        ]
        self.extra_buttons_bar.layout = widgets.Layout(
            gap=f'{col_gap}px',
            padding=f'10px 10px 10px {left_padding}px',
            align_items='center'
        )
    
    def display_checkboxes_and_buttons(self):
        """
        Displays a combined UI section with:
        - A VBox of country selection (title + checkboxes) and process buttons
        - A VBox of action buttons
        - A VBox holding the event/procedural dropdown and sub-action buttons (conditionally)
        All three arranged side-by-side in an HBox.
        Column headers carry a left border accent matching the button border colour.
        Bottom extra buttons are kept aligned with the columns above via
        _update_extra_buttons_width(), which accounts for dropdown_section visibility.
        """

        # ✅ Create Process All Countries button *only here*
        self.process_all_button = widgets.Button(description="Process All Countries")
        self.process_all_button.on_click(self.on_process_all_button_clicked)
        
        # --- Compute uniform button width based on labels ---
        all_labels = [btn.description for btn in self.action_buttons + self.sub_action_buttons] + \
                     [self.process_button.description, self.process_all_button.description, self.submit_button.description]
        button_width = self._compute_button_width(all_labels)
    
        # Apply width to all buttons
        for btn in self.action_buttons + self.sub_action_buttons + [self.submit_button, self.process_button, self.process_all_button]:
            btn.layout.width = button_width

        # Store for use by _update_extra_buttons_width when called from process_with_initFlag
        self._button_width = button_width

        # --- Shared title style: left border accent mirrors button border colour ---
        title_style = "margin-bottom:20px; padding-left:8px; border-left:3px solid #c8c8c8;"

        # --- Create / reuse FamilyRecord safely ---
        try:
            if self.record is None:
                _clean_dn = re.sub(r'[^0-9]', '', self.doc_number_widget.value.strip())
                self.record = FamilyRecord(
                    self.reference_type_widget.value,
                    _clean_dn,
                    self.country_widget.value,
                    self.kind_widget.value,
                    [str(self.constituents_widget.value)] if self.constituents_widget.value else []
                )
        except Exception as e:
            print(f"Error initializing FamilyRecord: {e}")
            return

        if not hasattr(self.record, 'dropdown_cc'):
            print("Error: dropdown_cc attribute is missing in FamilyRecord.")
            return

        # --- Country checkboxes (skip EP/WO) ---
        self.country_checkboxes = [
            widgets.Checkbox(description=c, value=False, style={'description_width': 'auto'})
            for c in self.record.dropdown_cc if c not in ["EP", "WO"]
        ]

        self.country_checkbox_grid = widgets.GridBox(
            self.country_checkboxes,
            layout=widgets.Layout(
                grid_template_columns='repeat(3, minmax(50px, 1fr))',
                grid_gap='2px 2px',
                width='100%',
            )
            # layout=widgets.Layout(grid_template_columns="repeat(2, 1fr)", gap='5px', width='100%')            
        )        

        # --- Col3: Countries + process buttons (content only, no bottom button) ---
        country_title = widgets.HTML(f"<h4 style='{title_style}'>Select Countries</h4>")
        country_selection_box = widgets.VBox(
            [country_title, self.country_checkbox_grid],
            layout=widgets.Layout(gap='12px', width='100%')
        )
        process_buttons_box = widgets.VBox(
            [self.process_button, self.process_all_button],
            layout=widgets.Layout(gap='8px', width='100%')
        )
        for btn in [self.process_button, self.process_all_button]:
            btn.layout.width = '100%'
        self.first_vbox.children = [country_selection_box, process_buttons_box]
        self.first_vbox.layout = widgets.Layout(width='100%', overflow='visible', gap='20px')

        # --- Col4: Bibliographic actions (content only, no bottom button) ---
        action_title = widgets.HTML(f"<h4 style='{title_style}'>Bibliographic Actions</h4>")
        for btn in self.action_buttons:
            btn.layout.width = '100%'
        self.actions_box.children = [action_title] + self.action_buttons
        self.actions_box.layout = widgets.Layout(width='100%', overflow='visible', gap='5px')

        # --- HBox combining col3 + col4 (kept for checkboxes_buttons_container compatibility) ---
        self.checkboxes_buttons_container.children = [
            widgets.HBox(
                [self.first_vbox, self.actions_box],
                layout=widgets.Layout(gap='40px', align_items='flex-start')
            )
        ]

        # --- Col1: Dropdown section (content only) ---
        dropdown_widget = self.priority_tree_event_code_dropdown if self.last_flag == "Show_procedural_codes" else self.ORAP_tree_event_code_dropdown
        if dropdown_widget.options:
            dropdown_title = widgets.HTML(f"<h4 style='{title_style}'>Select Event / Procedural Codes</h4>")

            # Compute height to match the tallest sibling column (actions_box = col4)
            # Each button is 36px height + 4px margin = ~40px; subtract title (~30px) and padding
            _btn_count   = len(self.actions_box.children)
            _row_height  = 40   # px per button (height=36px + margin=4px)
            _title_h     = 34   # px reserved for the "Select Event..." title
            _computed_h  = max(150, _btn_count * _row_height - _title_h)
            dropdown_widget.layout.height = f'{_computed_h}px'
            dropdown_widget.layout.width  = '100%'
            
            self.dropdown_section.children = [dropdown_title, dropdown_widget]
            self.dropdown_section.layout.display = ''
        else:
            self.dropdown_section.children = []
            self.dropdown_section.layout.display = 'none'

        self.dropdown_section.layout.width = '100%'
        self.dropdown_section.layout.overflow = 'visible'

        # --- Col2: Sub-actions (content only, no bottom button) ---
        if self.reference_type_widget.value == "publication":
            sub_action_title = widgets.HTML(f"<h4 style='{title_style}'>Legal and Procedural Actions</h4>")
            for btn in self.sub_action_buttons:
                btn.layout.width = '100%'
            self.subaction_section.children = [sub_action_title] + self.sub_action_buttons
            self.subaction_section.layout.display = ''
        else:
            self.subaction_section.children = []
            self.subaction_section.layout.display = 'none'

        self.subaction_section.layout.width = '100%'
        self.subaction_section.layout.overflow = 'visible'

        # --- Row 2: Bottom buttons — one per grid cell, always on same baseline ---
        sep_html = "<div style='border-top:2px solid #c8c8c8; margin:16px 0 10px 0;'></div>"

        # col1 bottom: Extract Codes & Show Chart — synced via helper
        self._update_dropdown_bottom()

        # col2 bottom: Event / Procedural Codes Retrieval
        self.event_codes_retrieval_button.layout.width = '100%'
        self.subaction_bottom.children = [widgets.HTML(sep_html), self.event_codes_retrieval_button]
        self.subaction_bottom.layout = widgets.Layout(width='100%', overflow='visible')

        # col3 bottom: Related Files Search
        self.related_files_button.layout.width = '100%'
        self.firstcol_bottom.children = [widgets.HTML(sep_html), self.related_files_button]
        self.firstcol_bottom.layout = widgets.Layout(width='100%', overflow='visible')

        # col4 bottom: DiviTree History
        self.divitree_history_button.layout.width = '100%'
        self.actions_bottom.children = [widgets.HTML(sep_html), self.divitree_history_button]
        self.actions_bottom.layout = widgets.Layout(width='100%', overflow='visible')

        # Grid uses minmax(0,1fr) columns — no px update needed

    def run_divitree_chart(self, tree_path=None):
        """Generate chart from tree_path and return (fig, html_path, html_header)."""
        try:
            plotter = DiviTreePlotter(workdir="/home/jovyan/output")
            plotter.pub_to_app_map = getattr(self.tree_plotter, 'pub_to_app_map', {})
            fig, html_path, html_header = plotter.run_divitree_plotter(
                reference_mode=self.reference_type_widget.value,
                interleaving_type=self.last_interleaving_type,
                tree_path=tree_path
            )
            return fig, html_path, html_header
        except Exception as e:
            print(f"❌ Error running divitree_plotter: {e}")
            import traceback; traceback.print_exc()
            return None, None, None
            
    def run_divitree_chart2(self):
        with self.output:
            try:
                plotter = DiviTreePlotter(workdir="/home/jovyan/output")
                plotter.pub_to_app_map = getattr(self.tree_plotter, 'pub_to_app_map', {})
                # No arg — let plotter read the type from self.selected_tree_type inside our class
                
                fig, result, html_header = plotter.run_divitree_plotter(reference_mode=self.reference_type_widget.value)
                
                html_output_path1 = html_output_path2 = None  # initialize early
                
                # Uncomment below lines to activate when needed
                # if isinstance(result, tuple) and len(result) == 2:
                #     html_output_path1, html_output_path2 = result
                # elif isinstance(result, tuple) and len(result) == 1:
                if isinstance(result, tuple) and len(result) >= 1:
                    html_output_path1 = result[0]  # ORAP chart path
                return fig, result, html_header
            except Exception as e:
                print("❌ Error running divitree_plotter:", e)
                return None, None, None
            
    def create_and_process_tree(self, selected_countries=None):
        """
        This method creates a patent tree using the TreeCreation class and processes it with the TreeProcessor.
        """
        try:
            # Creates a TreeCreation object using the patent data.
            self.tree = TreeCreation(db="EPODOC", df=self.filtered_app_numbers)
            
            # Calls the method to create a nested dictionary structure representing the patent tree, and extracts relevant lists of applications and filtered application numbers.
            self.root, self.listAPs, self.listORAPs, self.filtered_app_numbers = self.tree.create_nested_dict(self.filtered_app_numbers, selected_countries)

            # Converts the nested dictionary to a tree structure using TreeNode objects.
            tree_object = TreeNode.dict_to_object(self.tree)

            # Initializes a TreeProcessor object to handle tree processing.
            tree_processor = TreeProcessor(
                tree=tree_object, root=self.root, initFlag=self.initFlag,
                listAPs=self.listAPs, listORAPs=self.listORAPs, df=self.filtered_app_numbers,
                familyRoot=self.familyRoot, interleaving_type=self.last_interleaving_type
            )

            # Processes the tree and creates a tree file that represents the patent family structure
            tree_processor.process_tree(self.selected_tree_type, self.reference_type_widget.value)
                       
            tree_file_attr = f"{self.selected_tree_type}_tree_file_path"
            
            if not hasattr(tree_processor, tree_file_attr):
                raise AttributeError(f"TreeProcessor has no attribute '{tree_file_attr}'")

            path = getattr(tree_processor, tree_file_attr)

            with self.text_output:
                clear_output(wait=True)
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        content = f.read()
                    display(HTML(
                        f'<span style="font-family:monospace;font-size:13px;display:block">'
                        f'Raw content of {self.selected_tree_type.upper()} tree file:'
                        f'</span>'
                        + self._render_tree_html(content) +
                        f'<span style="font-family:monospace;font-size:13px;display:block">'
                        f'{self.selected_tree_type.upper()} tree contains {len(content.splitlines())} lines.'
                        f'</span>'
                    ))
                else:
                    print(f"Tree file not found: {path}")
                    
            with self.chart_output:
                clear_output(wait=True)                                        
                if self.tree:
                    # ✅ Always generate chart if tree exists                
                    fig, _ , html_header = self.run_divitree_chart()
                    display(HTML(html_header))
                    display(fig)
                else:
                    print("No chart available (tree not created)")
                    
                self.output_container.layout.display = ''
                self.result_hbox.layout.display = ''        # if not already there
                self.extra_section.layout.display = ''      # ← add this line
                self.output.clear_output(wait=False)        # clear bar 1 now that results are visible
                self.chart_output.layout.height = self.text_output.layout.height

                return self.tree, self.root, self.listAPs, self.listORAPs, self.filtered_app_numbers            
        except Exception as e:
            with self.text_output:
                clear_output(wait=True)
                print(f"Error creating and processing tree: {e}")
            with self.chart_output:
                clear_output(wait=True)
                print("No chart available due to error.")
            return None, None, None, None, None

    def process_patent_record(self, selected_countries=None):
        
        import time
        with self.output:
            try:
                # Reset record to ensure it's recreated with the latest values
                self.record = None
                
                clean_doc_number = re.sub(r'[^0-9]', '', self.doc_number_widget.value.strip())
                raw_doc_number = f"{self.country_widget.value}{clean_doc_number}{self.kind_widget.value or ''}"
                self.rec_input = TreeCreation().clean_doc_number(raw_doc_number)
                input_country_code = self.rec_input[:2].upper()

                self.record = FamilyRecord(
                    self.reference_type_widget.value, 
                    clean_doc_number, 
                    self.country_widget.value,
                    self.kind_widget.value, 
                    [str(self.constituents_widget.value)] if self.reference_type_widget.value == 'publication' and self.constituents_widget.value else [], 
                    selected_countries
                )
                
                self.record.input_appln_number = self.rec_input
                self.record.input_country_code = input_country_code

                result = self.record.process_fami_record(selected_countries)
                _result_ok = isinstance(result, tuple)
                if _result_ok:
                    self.filtered_app_numbers, _ = result
                    self.familyRoot = self.record.get_family_root()
                    # Build pub_serial→app_number map directly from record.df
                    # (no need for a special method in family_record.py).
                    try:
                        import re as _re_pam
                        _kind_pam = _re_pam.compile(r'^([A-Z]{2})(\d+)([A-Z]\d?)?$')
                        _pam = {}
                        _df_rec = getattr(self.record, 'df', None)
                        if _df_rec is not None and not _df_rec.empty:
                            for _, _r in _df_rec.iterrows():
                                _an = str(_r.get('app_number') or '').strip()
                                _pn = str(_r.get('pub_number') or '').strip()
                                if not _an or not _pn: continue
                                if not _an.upper().startswith('EP'): continue
                                _m = _kind_pam.match(_pn)
                                if _m:
                                    _pam[_m.group(1) + _m.group(2)] = _an
                        self.tree_plotter.pub_to_app_map = _pam
                        # if _pam:
                        #     print(f'🗺 pub_to_app_map: {len(_pam)} EP entries, '
                        #           f'e.g. {dict(list(_pam.items())[:2])}')
                    except Exception as _e_pam:
                        print(f'⚠️ pub_to_app_map build failed: {_e_pam}')

            except Exception as e:
                print(f"Error processing patent record: {e}")
                import traceback
                traceback.print_exc()
                _result_ok = False

        # Block exited — "Extracted…" message flushed to browser.
        if _result_ok:
            time.sleep(0.15)
            try:
                self.create_and_process_tree(selected_countries)
            except Exception as e:
                with self.output:
                    print(f"Error creating tree: {e}")
                import traceback; traceback.print_exc()
                
        # Update checkboxes after processing the record
        self.display_checkboxes_and_buttons()
                
    def reset_ui(self):
        self.dropdown_vbox_children = []
        # Clear all output areas
        self.output.clear_output(wait=True)
        self.chart_output.clear_output(wait=False)
        self.text_output.clear_output(wait=True)

        # Clear dynamic UI containers
        self.tree_event_code_dropdown_box.children = []
        self.checkboxes_buttons_container.children = []
        self.result_hbox.layout.display = 'none'
        self.output_container.layout.display = 'none'
        if hasattr(self, 'extra_section'):
            self.extra_section.layout.display = 'none'
            self.divitree_history_output.layout.display = 'none'
            self.related_files_output.layout.display = 'none'
            self.event_codes_retrieval_output.layout.display = 'none'
            self.event_codes_result_output.clear_output()
            self.event_codes_chart_display.clear_output()            
        
        # Reset dropdown
        self.ORAP_tree_event_code_dropdown.options = []
        self.ORAP_tree_event_code_dropdown.value = ()
        self.priority_tree_event_code_dropdown.options = []
        self.priority_tree_event_code_dropdown.value = ()
        
        # Reset internal state
        self.selected_orap_codes = []
        self.selected_prio_codes = []
        self.country_checkboxes = []
        self.is_button_clicked = False
        self.last_flag = None
        self._country_action_displayed = False
        self.process_all_button_displayed = False
         
        # Clear checkbox container if it exists
        if hasattr(self, "checkbox_container"):
            self.checkbox_container.children = []
            
    def on_submit_button_clicked(self, b):
        import time
        # Reset everything
        self.reset_ui()

        # Show bar immediately — reset_ui just cleared self.output so this
        # appears at once, before any API call begins.
        with self.output:
            display(HTML(self._PROGRESS_HTML.format(msg="⏳ Fetching patent family…")))
        time.sleep(0.15)   # yield so browser renders bar before blocking starts

        # Run main process ---
        selected_countries = ''
        self.process_patent_record(selected_countries)

        # # Re-display process_all_button cleanly --
        # with self.output:
        #     clear_output(wait=True)
        #     # print("📥 Submit pressed, preparing interface...")

        #     # Always rebuild the checkboxes + buttons interface
        #     self.display_checkboxes_and_buttons()

        #     # Reset flag to indicate Process All is managed inside display_checkboxes_and_buttons
        #     self.process_all_button_displayed = True  
        
        #     # Make sure container is visible (if you use it to group everything) --
        #     self.output_container.layout.display = ''  # This will show it!      
        #     self.result_hbox.layout.display = ''  # Show result box again
        #     self.extra_section.layout.display = ''  # Show extra buttons bar

        with self.output:
            # Make sure container is visible (if you use it to group everything) --
            self.output_container.layout.display = ''  # This will show it!      
            self.result_hbox.layout.display = ''  # Show result box again
            self.extra_section.layout.display = ''  # Show extra buttons bar            
        self.process_all_button_displayed = True
    
    def on_process_all_button_clicked(self, b):
        # with suppress_stdout():
            self._reset_dropdowns()   # ✅ always start with a clean dropdown
            for cb in self.country_checkboxes:
                cb.value = True
            self.on_process_button_clicked(b)
        
    def _reset_dropdowns(self):
        """Clear both event-code dropdowns and hide the dropdown section.
        Called whenever a new country-filtered tree is about to be generated,
        so the user always starts from a clean selection state.
        """
        # Temporarily disconnect observers to avoid triggering on_event_code_selected
        # while we reset options/value programmatically.
        try:
            self.ORAP_tree_event_code_dropdown.unobserve(self._orap_observer, names='value')
        except Exception:
            pass
        try:
            self.priority_tree_event_code_dropdown.unobserve(self._priority_observer, names='value')
        except Exception:
            pass

        self.ORAP_tree_event_code_dropdown.options = []
        self.ORAP_tree_event_code_dropdown.value   = ()
        self.ORAP_tree_event_code_dropdown.layout.display = 'none'

        self.priority_tree_event_code_dropdown.options = []
        self.priority_tree_event_code_dropdown.value   = ()
        self.priority_tree_event_code_dropdown.layout.display = 'none'

        self.selected_orap_codes = []
        self.selected_prio_codes = []

        if hasattr(self, 'dropdown_section'):
            self.dropdown_section.children = []
            self.dropdown_section.layout.display = 'none'

        # Re-attach observers
        self.ORAP_tree_event_code_dropdown.observe(self._orap_observer, names='value')
        self.priority_tree_event_code_dropdown.observe(self._priority_observer, names='value')

    def on_process_button_clicked(self, b):
        """
        This method processes the data for the selected countries when the "Process Selected Countries" button is clicked.
        """
        import time
        self._reset_dropdowns()   # ✅ always start with a clean dropdown
        selected_countries = [cb.description for cb in self.country_checkboxes if cb.value]
        if selected_countries:
            # Show bar immediately — same pattern as on_submit_button_clicked
            self.output.clear_output(wait=False)
            with self.output:
                display(HTML(self._PROGRESS_HTML.format(msg="⏳ Fetching patent family…")))
            time.sleep(0.15)
            self.process_patent_record(selected_countries)
        else:
            print("No countries selected.")
        