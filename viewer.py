"""
viewer.py — YAML Problem Bank Viewer
Single-file tkinter viewer for ESTELA physics problem bank YAML files.
"""
import re
import tkinter as tk
from tkinter import filedialog, font as tkfont
from html.parser import HTMLParser
import zipfile
import tempfile
import os

try:
    import yaml
except ImportError:
    yaml = None

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


NONE_TAG = "<none>"


def sanitize(text):
    """Replace non-BMP characters (e.g. emoji) that crash macOS tkinter."""
    return "".join(c if ord(c) <= 0xFFFF else "\ufffd" for c in str(text))

# ---------------------------------------------------------------------------
# bank_info field-name variant maps
# ---------------------------------------------------------------------------

DATE_KEYS     = ["date_created", "date created"]
PROMPTS_KEYS  = ["generation prompts", "generation_prompts",
                 "generational prompts", "generational_prompts"]
PROMPTS2_KEYS = ["generation prompts 2"]
LO_KEYS       = ["learning objectives", "learning_objectives"]
ASSOC_KEYS    = ["associated data", "associated_data"]
DETAILS_KEYS  = ["generation details", "generation_details"]


def get_bi(bi, keys, default=None):
    """Return first matching value from a bank_info dict given a list of key variants."""
    for k in keys:
        if k in bi:
            return bi[k]
    return default


# ---------------------------------------------------------------------------
# Content parsing
# ---------------------------------------------------------------------------

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._current_row = None
        self._current_cell = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._current_cell = []
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            if self._current_row is not None and self._current_cell is not None:
                self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr":
            if self._current_row is not None:
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data):
        if self._in_cell and self._current_cell is not None:
            self._current_cell.append(data)


def parse_content(text):
    """Parse mixed text into ('text'|'latex'|'table', value) segments."""
    segments = []
    pattern = re.compile(
        r"<latex>(.*?)</latex>|<table>(.*?)</table>",
        re.DOTALL | re.IGNORECASE,
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            segments.append(("text", text[pos:m.start()]))
        if m.group(1) is not None:
            segments.append(("latex", m.group(1)))
        else:
            tp = TableParser()
            tp.feed(m.group(2))
            segments.append(("table", tp.rows))
        pos = m.end()
    if pos < len(text):
        segments.append(("text", text[pos:]))
    return segments


# ---------------------------------------------------------------------------
# Collapsible section widget
# ---------------------------------------------------------------------------

class CollapsibleSection(tk.Frame):
    """A toggle-button + content frame, embeddable in a Text widget."""

    def __init__(self, parent, label, state_dict, state_key,
                 bg="#f8f8f8", **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self._state_dict = state_dict
        self._state_key = state_key
        self._expanded = state_dict.get(state_key, False)

        self._btn = tk.Button(
            self,
            text=self._btn_text(label),
            anchor="w",
            relief="flat",
            bg="#e0e8f0",
            activebackground="#c8d8ec",
            font=("Helvetica", 10, "bold"),
            padx=6, pady=2,
            cursor="hand2",
            command=self._toggle,
        )
        self._btn.pack(fill="x")
        self._label = label

        self._content = tk.Frame(self, bg=bg)
        if self._expanded:
            self._content.pack(fill="x", padx=4, pady=(0, 4))

    def _btn_text(self, label):
        arrow = "▼" if self._state_dict.get(self._state_key, False) else "▶"
        return f"  {arrow}  {label}"

    def _toggle(self):
        self._expanded = not self._expanded
        self._state_dict[self._state_key] = self._expanded
        arrow = "▼" if self._expanded else "▶"
        self._btn.config(text=f"  {arrow}  {self._label}")
        if self._expanded:
            self._content.pack(fill="x", padx=4, pady=(0, 4))
        else:
            self._content.pack_forget()

    def content_frame(self):
        return self._content

    def add_text_row(self, text, fg="#333333", font=("Helvetica", 10),
                     wrap=True):
        w = tk.Label(
            self._content,
            text=sanitize(text),
            anchor="nw",
            justify="left",
            fg=fg,
            bg=self._content["bg"],
            font=font,
            wraplength=700 if wrap else 0,
        )
        w.pack(fill="x", padx=4, pady=1)
        return w


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class BankViewerApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Problem Bank Viewer")
        self.geometry("900x750")
        self.minsize(640, 420)

        self._questions = []
        self._bank_info = {}
        self._current_index = 0
        self._yaml_dir = None
        self._figure_cache = {}
        self._tmp_dirs = []
        self._section_state = {}     # persists collapsed/expanded across navigation
        self._bank_info_widgets = [] # embedded widgets created at load time (destroyed on reload)
        self._question_widgets = []  # embedded widgets created per question (destroyed on navigate)

        self._build_ui()
        self.bind("<Left>",      lambda e: self._prev_question())
        self.bind("<Right>",     lambda e: self._next_question())
        self.bind("<Control-o>", lambda e: self.open_file())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._header_var = tk.StringVar(value="No file loaded")
        header = tk.Label(
            self,
            textvariable=self._header_var,
            anchor="w", padx=8, pady=4,
            relief="groove",
            bg="#dce8f5",
            font=("Helvetica", 11, "bold"),
        )
        header.pack(side="top", fill="x")

        toolbar = tk.Frame(self, relief="raised", bd=1, bg="#f0f0f0")
        toolbar.pack(side="top", fill="x")

        tk.Button(toolbar, text="Open File", command=self.open_file,
                  padx=6).pack(side="left", padx=4, pady=3)
        tk.Label(toolbar, bg="#f0f0f0", width=2).pack(side="left")

        self._prev_btn = tk.Button(toolbar, text="◀",
                                   command=self._prev_question, width=3)
        self._prev_btn.pack(side="left", padx=2, pady=3)

        self._nav_var = tk.StringVar(value="")
        tk.Label(toolbar, textvariable=self._nav_var,
                 bg="#f0f0f0", width=16).pack(side="left")

        self._next_btn = tk.Button(toolbar, text="▶",
                                   command=self._next_question, width=3)
        self._next_btn.pack(side="left", padx=2, pady=3)

        frame = tk.Frame(self)
        frame.pack(side="top", fill="both", expand=True)

        self._text = tk.Text(
            frame, wrap="word", state="disabled",
            padx=12, pady=8, spacing1=2, spacing3=2, cursor="arrow",
        )
        sb = tk.Scrollbar(frame, command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        self._configure_tags()
        self._show_placeholder("Open a YAML file to begin.  (Ctrl+O)")

    def _configure_tags(self):
        tw = self._text
        S = 11
        tw.tag_configure("title",      font=("Helvetica", S+2, "bold"))
        tw.tag_configure("type_badge", font=("Helvetica", S-1), foreground="#555555")
        tw.tag_configure("meta",       font=("Helvetica", S-1), foreground="#777777")
        tw.tag_configure("label",      font=("Helvetica", S, "bold"))
        tw.tag_configure("body",       font=("Helvetica", S))
        tw.tag_configure("none_val",   font=("Helvetica", S), foreground="#aaaaaa")
        tw.tag_configure("latex",      font=("Courier", S),
                         background="#f5f5dc", relief="flat")
        tw.tag_configure("answer_val", font=("Helvetica", S, "bold"),
                         foreground="#1a6e1a")
        tw.tag_configure("answer_tol", font=("Helvetica", S), foreground="#555555")
        tw.tag_configure("correct",    font=("Helvetica", S, "bold"),
                         foreground="#1a6e1a")
        tw.tag_configure("incorrect",  font=("Helvetica", S), foreground="#444444")
        tw.tag_configure("section",    font=("Helvetica", S, "bold"),
                         foreground="#333399")
        tw.tag_configure("divider",    font=("Helvetica", 4))
        tw.tag_configure("placeholder",font=("Helvetica", 12), foreground="#aaaaaa")
        tw.tag_configure("bi_label",   font=("Helvetica", S-1, "bold"),
                         foreground="#444455")
        tw.tag_configure("bi_val",     font=("Helvetica", S-1), foreground="#222222")

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def open_file(self):
        if yaml is None:
            self._show_placeholder("PyYAML is not installed.\nRun:  pip install pyyaml")
            return
        path = filedialog.askopenfilename(
            title="Open Problem Bank",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if path:
            self.load_bank(path)

    def load_bank(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            self._show_placeholder(f"Failed to load file:\n{exc}")
            return

        # Destroy all previously embedded widgets before clearing the text widget
        for w in self._bank_info_widgets + self._question_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._bank_info_widgets = []
        self._question_widgets = []

        self._yaml_dir = os.path.dirname(path)
        self._bank_info = data.get("bank_info", {})

        raw_questions = data.get("questions", [])
        self._questions = []
        for item in raw_questions:
            if isinstance(item, dict):
                for qtype, qdata in item.items():
                    if isinstance(qdata, dict):
                        self._questions.append({"_type": qtype, **qdata})

        # Build header
        title   = self._bank_info.get("title", "")
        bank_id = self._bank_info.get("bank_id", "")
        raw_au  = self._bank_info.get("authors", "")
        if isinstance(raw_au, list):
            authors = ", ".join(str(a) for a in raw_au)
        else:
            authors = str(raw_au) if raw_au else ""
        parts = [p for p in [title, bank_id, authors] if p]
        self._header_var.set("  ·  ".join(parts) if parts else os.path.basename(path))

        # Render bank info once — it stays for all navigation in this file
        tw = self._text
        tw.config(state="normal")
        tw.delete("1.0", "end")
        self._render_bank_info(tw)
        tw.insert("end", "\n", "divider")
        tw.insert("end", "─" * 72 + "\n", "meta")
        tw.insert("end", "\n", "divider")
        tw.mark_set("question_start", "end")
        tw.mark_gravity("question_start", "left")
        tw.config(state="disabled")

        if self._questions:
            self._current_index = 0
            self.show_question(0)
        else:
            tw.config(state="normal")
            tw.insert("end", "No questions found in this file.\n", "placeholder")
            tw.config(state="disabled")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _prev_question(self):
        if self._questions and self._current_index > 0:
            self._current_index -= 1
            self.show_question(self._current_index)

    def _next_question(self):
        if self._questions and self._current_index < len(self._questions) - 1:
            self._current_index += 1
            self.show_question(self._current_index)

    def _update_nav(self):
        n = len(self._questions)
        self._nav_var.set(f"Question {self._current_index+1} of {n}" if n else "")
        self._prev_btn.config(state="normal" if self._current_index > 0 else "disabled")
        self._next_btn.config(
            state="normal" if self._current_index < n-1 else "disabled")

    # ------------------------------------------------------------------
    # Top-level rendering
    # ------------------------------------------------------------------

    def _show_placeholder(self, msg):
        for w in self._bank_info_widgets + self._question_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._bank_info_widgets = []
        self._question_widgets = []
        tw = self._text
        tw.config(state="normal")
        tw.delete("1.0", "end")
        tw.insert("end", "\n\n" + msg, "placeholder")
        tw.config(state="disabled")

    def show_question(self, index):
        self._update_nav()
        self._figure_cache.clear()

        # Destroy only question-area embedded widgets; bank info widgets are preserved
        for w in self._question_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._question_widgets = []

        tw = self._text
        tw.config(state="normal")
        tw.delete("question_start", "end")
        self._render_question(tw, self._questions[index])
        tw.config(state="disabled")
        tw.see("question_start")

    # ------------------------------------------------------------------
    # Bank info rendering
    # ------------------------------------------------------------------

    def _render_bank_info(self, tw):
        bi = self._bank_info

        def field(label, value):
            tw.insert("end", f"  {label+':':<22}", "bi_label")
            if value is None or value == "" or value == [] or value == {}:
                tw.insert("end", NONE_TAG + "\n", "none_val")
            elif isinstance(value, list):
                tw.insert("end", ", ".join(str(v) for v in value) + "\n", "bi_val")
            else:
                tw.insert("end", str(value) + "\n", "bi_val")

        tw.insert("end", "BANK INFO\n", "section")

        field("Title",               bi.get("title"))
        field("Bank ID",             bi.get("bank_id"))
        field("Description",         bi.get("description"))
        field("Date",                get_bi(bi, DATE_KEYS))
        field("LLM",                 bi.get("LLM"))
        field("Authors",             bi.get("authors"))
        field("Learning Objectives", get_bi(bi, LO_KEYS))

        tw.insert("end", "\n", "divider")

        # Collapsible sections
        self._embed_collapsible(
            tw, "Generation Prompts",
            self._build_prompts_content,
            get_bi(bi, PROMPTS_KEYS),
        )

        gp2 = get_bi(bi, PROMPTS2_KEYS)
        if gp2:
            self._embed_collapsible(
                tw, "Generation Prompts 2",
                self._build_prompts_content,
                gp2,
            )

        self._embed_collapsible(
            tw, "Updates",
            self._build_updates_content,
            bi.get("updates"),
        )
        self._embed_collapsible(
            tw, "Associated Data",
            self._build_assoc_content,
            get_bi(bi, ASSOC_KEYS),
        )
        self._embed_collapsible(
            tw, "Generation Details",
            self._build_details_content,
            get_bi(bi, DETAILS_KEYS),
        )

    def _embed_collapsible(self, tw, label, builder_fn, data):
        """Create a CollapsibleSection, populate it, embed in text widget."""
        count = ""
        if isinstance(data, list):
            count = f" ({len(data)})"
        elif data is None:
            count = " (none)"

        section = CollapsibleSection(
            tw, f"{label}{count}",
            self._section_state, label,
            bg="#f4f6fb",
        )
        builder_fn(section, data)
        tw.window_create("end", window=section)
        tw.insert("end", "\n", "body")
        self._bank_info_widgets.append(section)

    def _build_prompts_content(self, section, data):
        if not data:
            section.add_text_row(NONE_TAG, fg="#aaaaaa")
            return
        for item in data:
            if isinstance(item, dict):
                for k, v in item.items():
                    section.add_text_row(
                        f"[{k}]",
                        fg="#333399", font=("Helvetica", 10, "bold"), wrap=False,
                    )
                    section.add_text_row(str(v) if v is not None else NONE_TAG,
                                         fg="#222222" if v else "#aaaaaa")
            else:
                section.add_text_row(str(item))

    def _build_updates_content(self, section, data):
        if not data:
            section.add_text_row(NONE_TAG, fg="#aaaaaa")
            return
        for item in data:
            if isinstance(item, dict):
                for k, v in item.items():
                    if v is None:
                        section.add_text_row(f"{k}: {NONE_TAG}", fg="#aaaaaa")
                    elif isinstance(v, list):
                        section.add_text_row(f"{k}:", fg="#555555",
                                             font=("Helvetica", 10, "bold"))
                        for sub in v:
                            if isinstance(sub, dict):
                                for sk, sv in sub.items():
                                    section.add_text_row(
                                        f"  [{sk}]  {sv if sv is not None else NONE_TAG}",
                                        fg="#333333" if sv else "#aaaaaa",
                                    )
                            else:
                                section.add_text_row(f"  {sub}")
                    else:
                        section.add_text_row(f"{k}: {v}", fg="#333333")
            else:
                section.add_text_row(str(item) if item is not None else NONE_TAG,
                                     fg="#333333" if item else "#aaaaaa")

    def _build_assoc_content(self, section, data):
        if not data:
            section.add_text_row(NONE_TAG, fg="#aaaaaa")
            return
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    for k, v in item.items():
                        section.add_text_row(
                            f"{k}: {v if v is not None else NONE_TAG}",
                            fg="#333333" if v else "#aaaaaa",
                        )
                else:
                    section.add_text_row(str(item) if item else NONE_TAG,
                                         fg="#333333" if item else "#aaaaaa")
        else:
            section.add_text_row(str(data))

    def _build_details_content(self, section, data):
        if not data:
            section.add_text_row(NONE_TAG, fg="#aaaaaa")
            return
        if isinstance(data, dict):
            for k, v in data.items():
                section.add_text_row(
                    f"{k}: {v if v is not None else NONE_TAG}",
                    fg="#333333" if v else "#aaaaaa",
                )
        else:
            section.add_text_row(str(data))

    # ------------------------------------------------------------------
    # Question rendering
    # ------------------------------------------------------------------

    def _render_question(self, tw, q):
        qtype = q.get("_type", "unknown")
        title  = q.get("title", q.get("id", ""))
        points = q.get("points", "")
        badge  = f"  [{qtype.upper()}  ·  {points}pt]" if points else f"  [{qtype.upper()}]"

        tw.insert("end", title, "title")
        tw.insert("end", badge + "\n", "type_badge")
        tw.insert("end", "\n", "divider")

        # ID and figure on one line
        qid    = q.get("id", NONE_TAG)
        figure = q.get("figure")
        tw.insert("end", "ID: ", "label")
        tw.insert("end", str(qid), "body")
        tw.insert("end", "    Figure: ", "label")
        if figure:
            tw.insert("end", str(figure) + "\n", "body")
            self._insert_figure(tw, figure)
        else:
            tw.insert("end", NONE_TAG + "\n", "none_val")
        tw.insert("end", "\n", "divider")

        # Question text
        tw.insert("end", "Question\n", "section")
        text = q.get("text", "")
        if text:
            self.render_content(tw, text)
        else:
            tw.insert("end", NONE_TAG + "\n", "none_val")
        tw.insert("end", "\n", "divider")

        # Answer section — dispatch by type
        tw.insert("end", "Answer\n", "section")
        if qtype == "numerical":
            self._render_answer_numerical(tw, q)
        elif qtype in ("multiple_choice", "multiple_answers"):
            self._render_answer_choices(tw, q, qtype)
        elif qtype == "categorization":
            self._render_answer_categorization(tw, q)
        else:
            ans = q.get("answer")
            tw.insert("end",
                       str(ans) if ans is not None else NONE_TAG,
                       "body" if ans is not None else "none_val")
            tw.insert("end", "\n", "body")

        tw.insert("end", "\n", "divider")

        # Feedback
        tw.insert("end", "─" * 60 + "\n", "meta")
        tw.insert("end", "Feedback\n", "section")
        feedback = q.get("feedback") or {}
        self._render_feedback(tw, feedback)

    # -- Answer renderers --

    def _render_answer_numerical(self, tw, q):
        ans = q.get("answer") or {}
        value         = ans.get("value")
        margin_type   = ans.get("margin_type")
        tolerance     = ans.get("tolerance")
        precision_type = ans.get("precision_type")
        precision     = ans.get("precision")

        tw.insert("end", "Value: ", "label")
        if value is not None:
            tw.insert("end", str(value), "answer_val")
        else:
            tw.insert("end", NONE_TAG, "none_val")

        # margin_type / tolerance
        if margin_type is not None or tolerance is not None:
            mt = margin_type if margin_type is not None else NONE_TAG
            tl = tolerance   if tolerance   is not None else NONE_TAG
            unit = "%" if margin_type == "percent" else (str(margin_type) if margin_type else "")
            tw.insert("end", f"    (±{tl} {unit})", "answer_tol")
        elif precision_type is not None or precision is not None:
            pt = precision_type if precision_type is not None else NONE_TAG
            pv = precision      if precision      is not None else NONE_TAG
            tw.insert("end", f"    (precision: {pv} {pt})", "answer_tol")
        else:
            tw.insert("end", f"    margin_type: {NONE_TAG}  tolerance: {NONE_TAG}",
                      "none_val")
        tw.insert("end", "\n", "body")

    def _render_answer_choices(self, tw, q, qtype):
        if qtype == "multiple_answers":
            partial = q.get("partial")
            tw.insert("end", "Partial credit: ", "label")
            tw.insert("end",
                       str(partial) if partial is not None else NONE_TAG,
                       "body" if partial is not None else "none_val")
            tw.insert("end", "\n", "body")

        answers = q.get("answers") or []
        if not answers:
            tw.insert("end", NONE_TAG + "\n", "none_val")
            return

        for item in answers:
            if not isinstance(item, dict):
                continue
            ans = item.get("answer", item)
            text    = ans.get("text", NONE_TAG)
            correct = ans.get("correct", None)
            lock    = ans.get("lock")

            marker = "✓ " if correct else "  • "
            tag    = "correct" if correct else "incorrect"
            tw.insert("end", f"  {marker}", tag)
            self.render_content(tw, str(text))

            extras = []
            if qtype == "multiple_answers":
                if lock is not None:
                    extras.append(f"lock={lock}")
            if extras:
                tw.insert("end", f"  [{', '.join(extras)}]", "meta")
            tw.insert("end", "\n", "body")

    def _render_answer_categorization(self, tw, q):
        categories = q.get("categories") or []
        if not categories:
            tw.insert("end", NONE_TAG + "\n", "none_val")
            return
        for item in categories:
            if not isinstance(item, dict):
                continue
            cat = item.get("category", item)
            desc    = cat.get("description", NONE_TAG)
            answers = cat.get("answers") or []
            tw.insert("end", f"  Category: ", "label")
            tw.insert("end", str(desc) + "\n", "body")
            for a in answers:
                tw.insert("end", f"    • {a}\n", "body")

    def _render_feedback(self, tw, feedback):
        general     = feedback.get("general")
        on_correct  = feedback.get("on_correct")
        on_incorrect = feedback.get("on_incorrect")

        if general:
            self.render_content(tw, general)
        else:
            tw.insert("end", NONE_TAG + "\n", "none_val")

        tw.insert("end", "\n", "body")
        tw.insert("end", "✓ Correct: ", "label")
        if on_correct:
            tw.insert("end", on_correct + "\n", "body")
        else:
            tw.insert("end", NONE_TAG + "\n", "none_val")

        tw.insert("end", "✗ Incorrect: ", "label")
        if on_incorrect:
            tw.insert("end", on_incorrect + "\n", "body")
        else:
            tw.insert("end", NONE_TAG + "\n", "none_val")

    # ------------------------------------------------------------------
    # Content rendering (mixed text / latex / table)
    # ------------------------------------------------------------------

    def render_content(self, tw, text):
        for seg_type, seg_val in parse_content(text):
            if seg_type == "text":
                tw.insert("end", sanitize(seg_val), "body")
            elif seg_type == "latex":
                tw.insert("end", sanitize(seg_val), "latex")
            elif seg_type == "table":
                self.insert_table(tw, seg_val)

    def insert_table(self, tw, rows):
        if not rows:
            return
        container = tk.Frame(tw, bd=1, relief="solid", bg="white")
        for r_idx, row in enumerate(rows):
            for c_idx, cell in enumerate(row):
                bg = "#dce8f5" if r_idx == 0 else ("white" if r_idx % 2 == 0 else "#f7f7f7")
                wt = "bold" if r_idx == 0 else "normal"
                tk.Label(
                    container, text=sanitize(cell),
                    font=("Helvetica", 10, wt),
                    bg=bg, fg="#222222",
                    relief="flat", bd=0,
                    padx=6, pady=3, anchor="w",
                ).grid(row=r_idx, column=c_idx, sticky="nsew", padx=1, pady=1)
                container.columnconfigure(c_idx, weight=1)
        tw.insert("end", "\n", "body")
        tw.window_create("end", window=container)
        tw.insert("end", "\n", "body")
        self._question_widgets.append(container)

    # ------------------------------------------------------------------
    # Figure handling
    # ------------------------------------------------------------------

    def _insert_figure(self, tw, figure_filename):
        img_path = self._resolve_figure(figure_filename)
        if img_path and PIL_AVAILABLE:
            try:
                img = Image.open(img_path)
                img.thumbnail((100, 100), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._figure_cache[figure_filename] = photo
                tw.image_create("end", image=photo)
                tw.insert("end", "\n", "body")
                return
            except Exception:
                pass
        tw.insert("end", f"[Figure: {figure_filename}]\n", "meta")

    def _resolve_figure(self, filename):
        if not self._yaml_dir:
            return None
        direct = os.path.join(self._yaml_dir, filename)
        if os.path.isfile(direct):
            return direct
        for entry in os.listdir(self._yaml_dir):
            if entry.lower().endswith(".zip"):
                zip_path = os.path.join(self._yaml_dir, entry)
                try:
                    with zipfile.ZipFile(zip_path) as zf:
                        for name in zf.namelist():
                            if os.path.basename(name) == filename:
                                tmp = tempfile.mkdtemp()
                                self._tmp_dirs.append(tmp)
                                zf.extract(name, tmp)
                                return os.path.join(tmp, name)
                except Exception:
                    continue
        return None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_close(self):
        import shutil
        for d in self._tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if yaml is None:
        import sys
        print("ERROR: PyYAML is not installed. Run:  pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    app = BankViewerApp()
    app.mainloop()
