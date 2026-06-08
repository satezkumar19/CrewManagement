"""
Generate the two L2 deliverable PDFs:

  * L2_understanding.pdf             — what the L2 layer is, per the plan (conceptual)
  * L2ImplementationSimpleWords.pdf  — how it was built, in plain language

Pure-Python (fpdf2), no system PDF engine needed.
    python -m L2Knowledge_graph.scripts.gen_l2_pdfs [output_dir]
"""
import sys

from fpdf import FPDF

# ── palette ─────────────────────────────────────────────────────────────────────
NAVY = (24, 41, 71)
BLUE = (37, 99, 156)
LIGHT = (236, 240, 245)
GREY = (90, 96, 105)
CODE_BG = (244, 245, 247)
GREEN = (28, 122, 74)


def _ascii(text: str) -> str:
    """fpdf2 core fonts are latin-1; map the few unicode glyphs we use."""
    repl = {
        "→": "->", "←": "<-", "↔": "<->", "—": "-", "–": "-",
        "•": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
        "…": "...", "≪": "<<", "≥": ">=", "≤": "<=", "×": "x",
        "✓": "[OK]", "·": "-",
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


class L2PDF(FPDF):
    def __init__(self, doc_title: str, subtitle: str):
        super().__init__(format="A4")
        self.doc_title = doc_title
        self.subtitle = subtitle
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 18, 18)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GREY)
        self.cell(0, 6, _ascii(self.doc_title), align="L")
        self.cell(0, 6, "L2 Knowledge Graph", align="R")
        self.ln(8)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GREY)
        self.cell(0, 6, f"Maritime Crew Orchestrator  ·  Page {self.page_no()}", align="C")

    # ── building blocks ──────────────────────────────────────────────────────────
    def cover(self):
        self.add_page()
        self.set_fill_color(*NAVY)
        self.rect(0, 0, 210, 62, "F")
        self.set_xy(18, 18)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 24)
        self.multi_cell(174, 11, _ascii(self.doc_title))
        self.set_x(18)
        self.set_font("Helvetica", "", 12)
        self.set_text_color(200, 212, 228)
        self.multi_cell(174, 7, _ascii(self.subtitle))
        self.set_y(72)  # clear the navy band (62mm) before the metadata block
        self.set_x(18)
        self.set_text_color(*GREY)
        self.set_font("Helvetica", "", 10)
        self.multi_cell(
            174, 6,
            _ascii("Layer L2 - Knowledge Graph   |   PostgreSQL 16 + Apache AGE 1.6.0\n"
                   "Prototype Jun 10 - Prod Jun 15 - Doc due Jun 12 (async review)"),
        )
        self.ln(4)

    def h2(self, text: str):
        if self.get_y() > 250:
            self.add_page()
        self.ln(3)
        self.set_fill_color(*LIGHT)
        self.set_text_color(*NAVY)
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 9, _ascii("  " + text), fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def h3(self, text: str):
        self.ln(1)
        self.set_text_color(*BLUE)
        self.set_font("Helvetica", "B", 11)
        self.multi_cell(0, 6, _ascii(text))
        self.ln(0.5)

    def body(self, text: str):
        self.set_text_color(40, 44, 52)
        self.set_font("Helvetica", "", 10.5)
        self.multi_cell(0, 5.6, _ascii(text))
        self.ln(1.5)

    def bullets(self, items, accent=BLUE):
        self.set_font("Helvetica", "", 10.5)
        for it in items:
            bold, rest = (it if isinstance(it, tuple) else (None, it))
            y = self.get_y()
            self.set_text_color(*accent)
            self.set_font("Helvetica", "B", 10.5)
            self.cell(6, 5.4, _ascii(">"))
            self.set_text_color(40, 44, 52)
            x = self.get_x()
            if bold:
                self.set_font("Helvetica", "B", 10.5)
                self.write(5.4, _ascii(bold + " "))
                self.set_font("Helvetica", "", 10.5)
                self.write(5.4, _ascii(rest))
                self.ln(5.4)
            else:
                self.set_font("Helvetica", "", 10.5)
                self.multi_cell(0, 5.4, _ascii(rest))
            self.set_xy(self.l_margin, max(self.get_y(), y))
        self.ln(1.5)

    def code(self, text: str):
        self.ln(1)
        self.set_fill_color(*CODE_BG)
        self.set_text_color(20, 30, 40)
        self.set_font("Courier", "", 9)
        lines = _ascii(text).split("\n")
        pad = 2
        self.set_x(self.l_margin)
        # background box
        h = 5 * len(lines) + 2 * pad
        x0, y0 = self.get_x(), self.get_y()
        self.rect(x0, y0, 174, h, "F")
        self.set_xy(x0 + pad, y0 + pad)
        for ln_ in lines:
            self.set_x(x0 + pad)
            self.cell(0, 5, ln_, new_x="LMARGIN", new_y="NEXT")
        self.set_y(y0 + h)
        self.ln(2)

    def table(self, headers, rows, widths):
        self.ln(1)
        self.set_font("Helvetica", "B", 9.5)
        self.set_fill_color(*BLUE)
        self.set_text_color(255, 255, 255)
        for h, w in zip(headers, widths):
            self.cell(w, 7, _ascii(h), border=0, fill=True, align="L")
        self.ln(7)
        self.set_text_color(40, 44, 52)
        fill = False
        for row in rows:
            # compute row height from the tallest wrapped cell
            self.set_font("Helvetica", "", 9.5)
            line_counts = [
                max(1, len(self.multi_cell(w - 2, 5, _ascii(str(txt)),
                    dry_run=True, output="LINES")))
                for txt, w in zip(row, widths)
            ]
            rh = 5 * max(line_counts) + 2
            if self.get_y() + rh > 275:
                self.add_page()
            x0, y0 = self.l_margin, self.get_y()
            x = x0
            for txt, w in zip(row, widths):
                self.set_fill_color(*(LIGHT if fill else (255, 255, 255)))
                self.rect(x, y0, w, rh, "F")
                self.set_xy(x + 1, y0 + 1)
                self.multi_cell(w - 2, 5, _ascii(str(txt)), border=0, align="L",
                                new_x="LMARGIN", new_y="TOP")
                x += w
            self.set_xy(x0, y0 + rh)
            fill = not fill
        self.ln(2)

    def callout(self, text: str, color=GREEN):
        self.ln(1)
        self.set_fill_color(*LIGHT)
        x0, y0 = self.l_margin, self.get_y()
        self.set_font("Helvetica", "B", 10)
        lines = self.multi_cell(168, 5.4, _ascii(text), dry_run=True, output="LINES")
        h = 5.4 * len(lines) + 4
        self.rect(x0, y0, 174, h, "F")
        self.set_fill_color(*color)
        self.rect(x0, y0, 2.2, h, "F")
        self.set_xy(x0 + 5, y0 + 2)
        self.set_text_color(*NAVY)
        self.multi_cell(165, 5.4, _ascii(text))
        self.set_y(y0 + h)
        self.ln(2)


# ── document 1: understanding ─────────────────────────────────────────────────────
def build_understanding(path: str):
    p = L2PDF("L2 Knowledge Graph - Understanding",
              "What the L2 layer is, and how it fits the plan")
    p.cover()

    p.h2("1. What L2 is, in one paragraph")
    p.body(
        "L2 is the Knowledge Graph layer. L1 (SignalFabric) streams in raw records - crew, "
        "contracts, vessel/port data, certificates. On their own these are disconnected rows. "
        "L2's job is to weave them into ONE connected graph of nodes and relationships, so that "
        "the smarter layers above (L3 Intelligence, L4 Decision) can answer questions by walking "
        "relationships instead of writing big multi-table joins. The graph lives inside the same "
        "PostgreSQL database using the Apache AGE extension - no separate graph database is added.")

    p.h2("2. Where L2 sits")
    p.code(
        "L1 SignalFabric   ->  streams raw events (crew, contracts, vessels, certs)\n"
        "L2 Knowledge Graph->  connects them into a graph  <-- THIS LAYER\n"
        "L3 Intelligence   ->  agents traverse the graph to match & rank crew\n"
        "L4 Decision Graph ->  records every decision as a trace for learning")
    p.body("L2 is the memory of the system: the shared, connected picture every layer above reads from.")

    p.h2("3. The three dimensions of the graph")
    p.body("The plan describes L2 as ONE graph seen through three lenses (\"dimensions\"). They share "
           "the same nodes; each lens adds its own relationships.")
    p.table(
        ["Dimension", "Answers the question", "Example path", "Status"],
        [["EntityMap", "What exists and how is it factually related?",
          "Crew -HOLDS-> Certificate", "BUILT"],
         ["OpsMap", "What is the operational state right now?",
          "sign-off -> search -> match -> onboard", "Planned"],
         ["OrgMap", "How is the organisation structured?",
          "Company -> Fleet -> Vessel -> Rank", "Planned"]],
        [30, 60, 56, 28])
    p.callout("This deliverable builds Dimension 1 (EntityMap) in full. OpsMap and OrgMap are "
              "specified as a baseline plan for the Jun 12 review; they reuse EntityMap's nodes.")

    p.h2("4. EntityMap - the five things the domain is made of")
    p.bullets([
        ("Crew", "- a seafarer, either a sign-on candidate or someone onboard."),
        ("Vessel", "- a ship."),
        ("Port", "- a port where crew are, or a ship calls."),
        ("Certificate", "- a qualification a seafarer holds (e.g. STCW, GMDSS)."),
        ("Contract", "- one engagement of a crew member on a vessel."),
    ])
    p.body("These connect through plain-English relationships: a Crew HOLDS a Certificate, is "
           "ASSIGNED_TO a Vessel, is CURRENTLY_AT a Port, and SIGNED a Contract; a Vessel CALLS_AT "
           "a Port; a Contract is FOR_VESSEL and AT_PORT.")

    p.h2("5. What \"done\" means (the plan's exit criteria)")
    p.table(
        ["Exit criterion (from the plan)", "How L2 meets it"],
        [["All 3 dimensions populated", "EntityMap loaded (90 nodes, 189 edges); Ops/Org specced"],
         ["Crew search by rank + cert + port works", "Graph search endpoint returns correct crew"],
         ["Full relationship traversal works", "Traversal endpoint walks multi-hop paths"],
         ["Test UI query < 3 seconds", "Measured 5-37 milliseconds"],
         ["No data duplication across dimensions", "Each real-world thing is exactly one node"]],
        [80, 94])

    p.h2("6. Why a graph (and why Postgres + AGE)")
    p.bullets([
        ("A graph fits the questions.", "\"Which available Chief Officer at Rotterdam holds GMDSS "
         "and can replace the one signing off?\" is a path through relationships, not a row filter."),
        ("One database, not two.", "AGE runs the graph INSIDE the existing PostgreSQL, so graph "
         "context and relational records share one connection - simpler ops, no data sync."),
        ("Standard query language.", "AGE speaks openCypher, the widely-used graph query language, "
         "so the intelligence layer reads familiar queries."),
        ("Swappable backend.", "A 'fallback' mode builds the same shape in plain Python when AGE "
         "isn't present, so the feature always demos; 'age' mode runs the real graph."),
    ])

    p.h2("7. What the team gets from L2")
    p.bullets([
        ("For L3 (Intelligence):", "a ready-made web of facts to traverse instead of re-joining tables."),
        ("For reviewers:", "a clear schema, a working EntityMap, and a concrete plan for the other two maps."),
        ("For operations:", "fast (<3s) answers to crew-matching questions backed by real relationships."),
    ])
    p.output(path)
    print("wrote", path)


# ── document 2: implementation in simple words ────────────────────────────────────
def build_implementation(path: str):
    p = L2PDF("L2 Implementation - In Simple Words",
              "How the EntityMap was actually built, explained plainly")
    p.cover()

    p.h2("1. The big picture")
    p.body("We added a knowledge graph next to the normal crew table, in the SAME database. "
           "We used PostgreSQL with an extension called Apache AGE, which lets a normal SQL "
           "database also store and query a graph using a language called Cypher. Then we wrote "
           "code that reads the 40 crew records and turns them into graph nodes and connections, "
           "plus a small web API to ask the graph questions.")

    p.h2("2. The pieces we built")
    p.table(
        ["File", "What it does (plain words)"],
        [["database/entity_map.py", "The brain. Defines the 5 node types and 7 connection types, "
          "builds the graph from crew data, and runs the search & traversal queries."],
         ["scripts/seed_entity_map.py", "A one-command script that fills the graph with the crew data."],
         ["api/routes/graph.py", "The web endpoints other people/UI call to query the graph."],
         ["database/graph_db.py", "The only file that talks Cypher to the database (we fixed 3 bugs here)."]],
        [55, 119])

    p.h2("3. How we turn a crew record into graph pieces")
    p.body("For each crew member we create one Crew node, then connect it to the things it relates to. "
           "We always use MERGE (\"create only if it isn't already there\") so we never get duplicates - "
           "MV Pacific Star is one Vessel node even though four crew point at it.")
    p.code(
        "for each crew member:\n"
        "    MERGE a (:Crew) node with their details\n"
        "    for each certificate -> MERGE (:Certificate) + connect Crew -HOLDS-> it\n"
        "    connect Crew -CURRENTLY_AT-> (:Port)\n"
        "    if onboard a ship -> connect Crew -ASSIGNED_TO-> (:Vessel) -CALLS_AT-> (:Port)\n"
        "    if onboard       -> make a (:Contract) and link Crew -SIGNED-> it")

    p.h2("4. The questions the graph can answer")
    p.h3("a) Search crew by rank + certificate + port")
    p.body("You give any mix of rank, certificate and port; the graph returns the matching crew. "
           "Rank is a property on the Crew node; certificate and port are checked by following the "
           "HOLDS and CURRENTLY_AT connections.")
    p.code("GET /api/v1/graph/crew/search?rank=Chief Officer&certificate=GMDSS&port=Rotterdam\n"
           "-> 1 result: Piotr Kowalski   (took ~5 ms)")
    p.h3("b) Traverse one crew member's connections")
    p.body("You give a crew id; the graph returns everything connected to them, including two-hop "
           "paths like Crew -> Vessel -> Port.")
    p.code("GET /api/v1/graph/crew/SOF-2000/traverse\n"
           "-> CURRENTLY_AT Singapore, ASSIGNED_TO MV Pacific Star, SIGNED CT-SOF-2000  (~30 ms)")

    p.h2("5. Three real bugs we hit and fixed")
    p.body("These were not obvious, so they are worth recording for whoever touches the graph next:")
    p.bullets([
        ("Colons clash.", "Cypher writes connections like [:HOLDS]. The database library thought "
         ":HOLDS was a fill-in-the-blank parameter. Fix: send the query as raw text, not through "
         "the library's parameter system."),
        ("Vertices don't parse as JSON.", "AGE tags returned nodes with a ::vertex marker that breaks "
         "JSON reading. Fix: always return plain maps of the fields we want, e.g. {name:.., rank:..}."),
        ("Writes vanished.", "Our create/merge statements weren't being saved because we never told "
         "the database to commit. Fix: commit after each query."),
    ])

    p.h2("6. How we know it works")
    p.table(
        ["Check", "Result"],
        [["All 5 node types loaded", "Crew 40, Vessel 5, Port 9, Certificate 16, Contract 20"],
         ["Total graph size", "90 nodes, 189 connections"],
         ["Search by rank+cert+port", "Correct single match returned"],
         ["Multi-hop traversal", "Crew -> Vessel -> Port path returned"],
         ["Speed", "5-37 ms (limit was 3000 ms)"],
         ["No duplicates", "5 vessels & 9 ports, not 40 of each"]],
        [70, 104])

    p.h2("7. How to run it yourself")
    p.code(
        "cd backend\n"
        "echo GRAPH_BACKEND=age >> .env          # turn the graph on\n"
        "python -m scripts.seed_crew             # load 40 crew rows\n"
        "python -m L2Knowledge_graph.scripts.seed_entity_map       # build the graph\n"
        "curl localhost:8000/api/v1/graph/summary")

    p.h2("8. What's next (not built yet)")
    p.bullets([
        ("OpsMap", "- add the live workflow state (sign-off -> search -> match -> onboard) as extra "
         "connections on the SAME crew nodes."),
        ("OrgMap", "- add Company and Fleet nodes above the ships, and turn rank into a shared node, "
         "to answer 'how many Chief Officers does this fleet need vs have?'."),
        ("The golden rule", "for both: only ADD connections to existing nodes - never recreate Crew "
         "or Vessel - so the graph stays one clean picture with no duplicates."),
    ])
    p.output(path)
    print("wrote", path)


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    build_understanding(f"{out_dir}/L2_understanding.pdf")
    build_implementation(f"{out_dir}/L2ImplementationSimpleWords.pdf")
