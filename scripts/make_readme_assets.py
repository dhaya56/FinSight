"""Render the README's diagrams, so the pictures are reproducible rather than drawn once.

A diagram that exists only as a PNG rots: a number changes, nobody can regenerate it, and
the image quietly starts contradicting the text. Keeping the source here means a figure
can be corrected in the place the figure is defined.

Four images:

* ``banner.png``        the hero, with the headline figures that survive scrutiny
* ``architecture.png``  deployment tiers, trust boundaries and what each one may reach
* ``evidence-gate.png`` how a generated sentence becomes a released claim, or does not
* ``pipeline.png``      the indexing and query pipelines over the stores they share

Run from the repository root:

    python scripts/make_readme_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parents[1] / "assets"
FONT_DIR = Path("C:/Windows/Fonts")

# Dark hero, light diagrams: the banner competes with a GitHub page header, the
# diagrams sit inside prose and must not.
NAVY = "#0b1220"
NAVY_SOFT = "#111c33"
WHITE = "#ffffff"
SLATE = "#94a3b8"
MIST = "#cbd5e1"

INDIGO = "#6366f1"
CYAN = "#22d3ee"
EMERALD = "#34d399"
AMBER = "#fbbf24"
ROSE = "#fb7185"

INK = "#0f172a"
MUTED = "#475569"
LINE = "#334155"
FILL = "#f1f5f9"
FILL_EDGE = "#ede9fe"
FILL_APP = "#e0e7ff"
FILL_DATA = "#e0f2fe"
FILL_HOST = "#fef3c7"
FILL_OK = "#dcfce7"
FILL_NO = "#ffe4e6"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (name, "segoeui.ttf", "arial.ttf"):
        path = FONT_DIR / candidate
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


BOLD, REG, LIGHT, MONO = "segoeuib.ttf", "segoeui.ttf", "segoeuil.ttf", "consola.ttf"


def centre(draw: ImageDraw.ImageDraw, text: str, fnt, y: int, width: int, fill: str) -> None:
    draw.text(((width - draw.textlength(text, font=fnt)) / 2, y), text, font=fnt, fill=fill)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, limit: float) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=fnt) <= limit:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def card(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    title: str,
    detail: str = "",
    fill: str = FILL,
    outline: str = LINE,
    title_size: int = 30,
    detail_size: int = 24,
    radius: int = 16,
) -> None:
    x0, y0, x1, y1 = rect
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=3)
    f_title, f_detail = font(BOLD, title_size), font(REG, detail_size)
    pad = 20
    title_lines = wrap(draw, title, f_title, x1 - x0 - 2 * pad)
    detail_lines = wrap(draw, detail, f_detail, x1 - x0 - 2 * pad) if detail else []
    th, dh = title_size + 8, detail_size + 6
    block = len(title_lines) * th + (len(detail_lines) * dh + 10 if detail_lines else 0)
    y = y0 + ((y1 - y0) - block) // 2
    for line in title_lines:
        w = draw.textlength(line, font=f_title)
        draw.text(((x0 + x1 - w) / 2, y), line, font=f_title, fill=INK)
        y += th
    if detail_lines:
        y += 10
        for line in detail_lines:
            w = draw.textlength(line, font=f_detail)
            draw.text(((x0 + x1 - w) / 2, y), line, font=f_detail, fill=MUTED)
            y += dh


def arrow(draw: ImageDraw.ImageDraw, start, end, colour: str = LINE, width: int = 4) -> None:
    x0, y0 = start
    x1, y1 = end
    draw.line([start, end], fill=colour, width=width)
    if x0 == x1:
        head = 16 if y1 > y0 else -16
        draw.polygon([(x1, y1), (x1 - 10, y1 - head), (x1 + 10, y1 - head)], fill=colour)
    else:
        head = 16 if x1 > x0 else -16
        draw.polygon([(x1, y1), (x1 - head, y1 - 10), (x1 - head, y1 + 10)], fill=colour)


# ----------------------------------------------------------------------- banner
def banner() -> Path:
    width, height = 2400, 760
    image = Image.new("RGB", (width, height), NAVY)
    draw = ImageDraw.Draw(image)

    # A faint grid, so the field is not a flat rectangle at full width.
    for x in range(0, width, 60):
        draw.line([(x, 0), (x, height)], fill=NAVY_SOFT, width=1)
    for y in range(0, height, 60):
        draw.line([(0, y), (width, y)], fill=NAVY_SOFT, width=1)

    # Accent rule, four segments in the palette the diagrams reuse.
    seg = width // 4
    for index, colour in enumerate((INDIGO, CYAN, EMERALD, AMBER)):
        draw.rectangle([(index * seg, 0), ((index + 1) * seg, 9)], fill=colour)

    centre(draw, "FinSight", font(BOLD, 150), 92, width, WHITE)
    centre(
        draw,
        "Evidence-grounded question answering over company financial filings",
        font(LIGHT, 46),
        270,
        width,
        MIST,
    )
    centre(
        draw,
        "Self-hosted  ·  hybrid retrieval"
        "  ·  every released figure traced to the page it came from",
        font(REG, 32),
        344,
        width,
        SLATE,
    )

    stats = [
        ("1,403", "pages indexed", CYAN),
        ("4,859", "passages embedded", INDIGO),
        ("573 → 0", "unreachable passages", EMERALD),
        # Not "faster re-index": 811x is the cache *hit path* (2,360.77 s cold against
        # 2.91 s warm, ENV-013). A real re-index mixes hits and misses and measured 6.3x.
        ("811×", "faster on cached text", AMBER),  # noqa: RUF001 — rendered glyph, not code
        ("1,794", "tests passing", ROSE),
    ]
    box_w, gap = 400, 28
    total = len(stats) * box_w + (len(stats) - 1) * gap
    x = (width - total) // 2
    y0, y1 = 452, 644
    for value, label, colour in stats:
        draw.rounded_rectangle(
            [(x, y0), (x + box_w, y1)], radius=18, fill=NAVY_SOFT, outline=colour, width=3
        )
        f_value = font(BOLD, 58)
        w = draw.textlength(value, font=f_value)
        draw.text((x + (box_w - w) / 2, y0 + 26), value, font=f_value, fill=colour)
        f_label = font(REG, 26)
        w = draw.textlength(label, font=f_label)
        draw.text((x + (box_w - w) / 2, y0 + 110), label, font=f_label, fill=SLATE)
        x += box_w + gap

    centre(
        draw,
        "Python 3.12  ·  FastAPI  ·  PostgreSQL  ·  Qdrant  ·  Ollama  ·  Docker",
        font(REG, 28),
        686,
        width,
        SLATE,
    )

    out = ASSETS / "banner.png"
    image.save(out, "PNG")
    return out


# ----------------------------------------------------------------- architecture
def architecture() -> Path:
    width, height = 2400, 1540
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "System Architecture", font(BOLD, 62), 40, width, INK)
    centre(
        draw,
        "Three tiers, two networks, one host-native model runtime"
        " — a tier may reach only what it is attached to",
        font(REG, 30),
        122,
        width,
        MUTED,
    )

    margin = 70
    inner = width - 2 * margin

    def zone(rect, label, note, colour) -> None:
        """A labelled boundary. Both captions sit on an opaque patch, because an
        outline drawn through a word is the fastest way to make a diagram look
        unfinished."""
        x0, y0, x1, _ = rect
        draw.rounded_rectangle(rect, radius=20, outline=colour, width=4)
        f_label, f_note = font(BOLD, 28), font(REG, 24)
        label_w = draw.textlength(label, font=f_label)
        draw.rectangle([(x0 + 28, y0 - 20), (x0 + 50 + label_w, y0 + 20)], fill=WHITE)
        draw.text((x0 + 38, y0 - 16), label, font=f_label, fill=colour)
        note_w = draw.textlength(note, font=f_note)
        draw.rectangle([(x1 - 40 - note_w, y0 - 18), (x1 - 20, y0 + 18)], fill=WHITE)
        draw.text((x1 - 30 - note_w, y0 - 14), note, font=f_note, fill=MUTED)

    # ---- edge tier
    #
    # Two cards, not three. An earlier revision put the operator CLI in this zone,
    # which was wrong: the CLI runs on the host and talks to the datastores directly,
    # so drawing it inside the edge boundary claimed an isolation it does not have.
    # It is noted in the footer instead.
    edge_y0, edge_y1 = 230, 440
    zone(
        (margin, edge_y0, margin + inner, edge_y1), "EDGE NETWORK", "browser-facing tier", INDIGO
    )
    half = (inner - 120 - 60) // 2
    card(
        draw,
        (margin + 60, edge_y0 + 46, margin + 60 + half, edge_y1 - 40),
        "Streamlit UI",
        "non-root uid 10002 · read-only root filesystem"
        " · reaches the API over HTTP and nothing else",
        FILL_EDGE,
    )
    card(
        draw,
        (margin + 120 + half, edge_y0 + 46, margin + inner - 60, edge_y1 - 40),
        "No datastore client in the image",
        "no database driver, no ORM, no object-store client, no vector client"
        " — isolation by contents, not by policy",
        FILL_NO,
        ROSE,
    )

    # ---- application tier
    app_y0, app_y1 = 560, 790
    zone(
        (margin, app_y0, margin + inner, app_y1), "APPLICATION", "bridges both networks", EMERALD
    )
    card(
        draw,
        (margin + 60, app_y0 + 46, margin + 900, app_y1 - 46),
        "FastAPI service",
        "bearer auth on every non-health route · readiness probes · QueryTrace",
        FILL_APP,
    )
    card(
        draw,
        (margin + 960, app_y0 + 46, margin + 1560, app_y1 - 46),
        "Retrieval + generation",
        "filters · BM25 + dense · RRF · rerank · Evidence Gate",
        FILL_APP,
    )
    card(
        draw,
        (margin + 1620, app_y0 + 46, margin + inner - 60, app_y1 - 46),
        "Migrator (one-shot)",
        "alembic upgrade head, must exit 0 before the API starts",
        FILL_OK,
    )

    # ---- data tier
    data_y0, data_y1 = 910, 1140
    zone(
        (margin, data_y0, margin + inner, data_y1),
        "BACKBONE NETWORK",
        "not reachable from the edge",
        CYAN,
    )
    tiles = [
        ("PostgreSQL 18.6", "authoritative: source elements, chunks,\ngenerations, answers, audit"),
        ("Qdrant 1.19", "derived index: 768-d dense + BM25 sparse,\n9 indexed filter fields"),
        ("Object store (S3)", "immutable originals, content-addressed"),
        # Not a fourth service: it is a table inside the PostgreSQL above. Said on the
        # tile, because a box in a service row otherwise reads as one.
        ("Embedding cache", "a PostgreSQL table: content-keyed vectors,\nbit-identical on hit"),
    ]
    tile_w = (inner - 120 - 3 * 40) // 4
    x = margin + 60
    for title, detail in tiles:
        card(
            draw,
            (x, data_y0 + 46, x + tile_w, data_y1 - 46),
            title,
            detail.replace("\n", " "),
            FILL_DATA,
        )
        x += tile_w + 40

    # ---- host tier
    host_y0, host_y1 = 1262, 1422
    zone(
        (margin, host_y0, margin + inner, host_y1),
        "HOST (NOT CONTAINERISED)",
        "no outbound internet from any model",
        AMBER,
    )
    card(
        draw,
        (margin + 60, host_y0 + 34, margin + 1100, host_y1 - 30),
        "Ollama — nomic-embed-text (768-d)",
        "embeds passages and queries; reached over host.docker.internal",
        FILL_HOST,
    )
    card(
        draw,
        (margin + 1160, host_y0 + 34, margin + inner - 60, host_y1 - 30),
        "Ollama — llama3.1:8b (Q4_K_M)",
        "schema-constrained generation; no tools, no filesystem, no network",
        FILL_HOST,
    )

    mid = width // 2
    arrow(draw, (mid, edge_y1 + 6), (mid, app_y0 - 6), INDIGO)
    arrow(draw, (mid, app_y1 + 6), (mid, data_y0 - 6), EMERALD)
    draw.text((mid + 26, edge_y1 + 20), "HTTP + bearer token only", font=font(REG, 24), fill=MUTED)
    draw.text(
        (mid + 26, app_y1 + 20), "SQL, vector search, object reads", font=font(REG, 24), fill=MUTED
    )

    # No arrow is drawn from the data tier to the host tier.
    #
    # It is the application that calls the model runtime, and two earlier revisions
    # got this wrong in different ways: first by running the arrow out of the
    # backbone zone because it sat directly above — asserting that PostgreSQL talks
    # to Ollama — and then by routing around the data tier, which crossed two labels
    # and a card to say something a sentence says better. The relationship is stated
    # in the host zone's own caption instead.
    draw.text(
        (margin + 60, data_y1 + 46),
        "The host model runtime below is called by the APPLICATION tier, never by a datastore.",
        font=font(REG, 26),
        fill=MUTED,
    )

    footer = [
        "Every published port binds 127.0.0.1.  Containers run non-root with cap_drop ALL,"
        " no-new-privileges and a read-only root filesystem.",
        "The operator CLI runs on the host, outside both networks, and reaches PostgreSQL"
        " and Qdrant through those loopback ports.",
    ]
    y = height - 86
    for line in footer:
        draw.text((margin, y), line, font=font(REG, 25), fill=MUTED)
        y += 36

    out = ASSETS / "architecture.png"
    image.save(out, "PNG")
    return out


# ---------------------------------------------------------------- evidence gate
def evidence_gate() -> Path:
    width, height = 2400, 1210
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "How a sentence becomes a released claim", font(BOLD, 60), 40, width, INK)
    centre(
        draw,
        "The model proposes. Code disposes."
        " Every check below runs outside the model, on stored evidence.",
        font(REG, 30),
        120,
        width,
        MUTED,
    )

    margin = 70
    col_w = (width - 2 * margin - 4 * 36) // 5
    y0, y1 = 250, 470
    stages = [
        ("1 · Model output", "a claim plus the passage numbers it says it used", FILL),
        (
            "2 · Citation resolution",
            "passage numbers mapped to stored source element ids, by code",
            FILL_APP,
        ),
        ("3 · Numeral check", "every figure matched against the spans this claim cites", FILL_APP),
        ("4 · Scope check", "issuer, period and reporting basis must agree", FILL_APP),
        ("5 · Decision", "release, withhold the claim, or abstain entirely", FILL_OK),
    ]
    x = margin
    for index, (title, detail, fill) in enumerate(stages):
        card(draw, (x, y0, x + col_w, y1), title, detail, fill, title_size=29, detail_size=24)
        if index:
            arrow(draw, (x - 36 + 4, (y0 + y1) // 2), (x - 6, (y0 + y1) // 2))
        x += col_w + 36

    # Outcomes
    out_y0, out_y1 = 620, 830
    third = (width - 2 * margin - 2 * 40) // 3
    outcomes = [
        (
            "Released",
            "the claim cites at least one stored span, and every numeral in it appears there",
            FILL_OK,
            EMERALD,
        ),
        (
            "Withheld",
            "the claim is removed from the answer and the reason is recorded",
            FILL_NO,
            ROSE,
        ),
        ("Abstained", "no evidence survived, so the model is never called at all", FILL, MUTED),
    ]
    centres = []
    x = margin
    for title, detail, fill, outline in outcomes:
        card(draw, (x, out_y0, x + third, out_y1), title, detail, fill, outline, title_size=34)
        centres.append(x + third // 2)
        x += third + 40

    # Fanned out of stage 5, not out of the middle of the row.
    #
    # An earlier revision dropped one arrow from the centre of the figure, which
    # landed under "Numeral check" and read as though the numeral check produced the
    # three outcomes. The decision is what branches, so the fan starts there.
    decision_centre = margin + 4 * (col_w + 36) + col_w // 2
    bus_y = out_y0 - 70
    draw.line([(decision_centre, y1 + 6), (decision_centre, bus_y)], fill=LINE, width=4)
    draw.line([(min(centres), bus_y), (decision_centre, bus_y)], fill=LINE, width=4)
    for centre_x in centres:
        arrow(draw, (centre_x, bus_y), (centre_x, out_y0 - 6))

    # What the model is never allowed to do
    bar_y0, bar_y1 = 900, 1150
    draw.rounded_rectangle(
        (margin, bar_y0, width - margin, bar_y1), radius=18, fill="#fff1f2", outline=ROSE, width=3
    )
    draw.text((margin + 36, bar_y0 + 26), "The model never:", font=font(BOLD, 34), fill=INK)
    # Bullet 1 is deliberately not "never writes a figure". ADR-009 removed the typed
    # placeholders and the deterministic substitution that would have made that literally
    # true; under citation resolution the model does write the numeral and the Gate checks
    # it against the spans the claim itself cites. Stating the stronger claim here would
    # describe a design this project measured and replaced.
    never = [
        "releases a figure on its own word — every numeral is matched against the spans"
        " that claim cites",
        "transcribes a quotation — spans are rendered by code from the stored source region",
        "cites outside its evidence — passage numbers are resolved against the passages"
        " it was given",
        "reaches a tool, a filesystem, a database or the network — it has no such capability",
    ]
    y = bar_y0 + 78
    for line in never:
        draw.ellipse((margin + 44, y + 10, margin + 55, y + 21), fill=ROSE)
        draw.text((margin + 72, y), line, font=font(REG, 27), fill=MUTED)
        y += 38

    out = ASSETS / "evidence-gate.png"
    image.save(out, "PNG")
    return out


# --------------------------------------------------------------------- pipeline
def pipeline() -> Path:
    """The two pipelines and the store band they share.

    A different picture from ``architecture.png`` on purpose. That one answers "what is
    deployed where, and what may reach what". This one answers "what are the two
    pipelines and what do they have in common" — indexing runs once per document
    version, querying runs once per question, and the only thing joining them is the
    persisted representation.

    The store band sits *between* the lanes so indexing writes downward into it and
    querying reads upward out of it, which makes every connector a short vertical line
    instead of a diagonal across the figure.
    """
    width, height = 2400, 1350
    margin = 70
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "Input to Output — the two pipelines", font(BOLD, 58), 38, width, INK)
    centre(
        draw,
        "Indexing runs once per document version. Querying runs once per question."
        " They meet only in the stores.",
        font(REG, 30),
        116,
        width,
        MUTED,
    )

    usable = width - 2 * margin
    cols, gap = 8, 37
    bw = (usable - (cols - 1) * gap) // cols

    def col_x(index: int) -> tuple[int, int]:
        x0 = margin + index * (bw + gap)
        return x0, x0 + bw

    def lane_label(x: int, y: int, text: str, note: str) -> None:
        draw.text((x, y), text, font=font(BOLD, 30), fill=INK)
        offset = draw.textlength(text, font=font(BOLD, 30)) + 18
        draw.text((x + offset, y + 5), note, font=font(REG, 24), fill=MUTED)

    # ---------------------------------------------------------- indexing lane
    lane_label(margin, 182, "INDEXING PIPELINE", "runs once per document version")
    top_y0, top_y1 = 228, 356
    indexing = [
        ("Source filing", "PDF, as published", FILL_EDGE),
        ("Intake", "SHA-256, type, size limits", FILL),
        ("Extraction", "PyMuPDF, page order", FILL),
        ("Normalisation", "glyph artefacts decoded", FILL),
        ("Chunking", "parent + child units", FILL),
        ("Embedding", "Nomic, 768-dim", FILL_HOST),
        ("Indexing", "transactional outbox", FILL),
        ("Activation", "shadow to active", FILL_OK),
    ]
    for index, (name, detail, fill) in enumerate(indexing):
        x0, x1 = col_x(index)
        card(draw, (x0, top_y0, x1, top_y1), name, detail, fill, title_size=27, detail_size=23)
        if index:
            mid_y = (top_y0 + top_y1) // 2
            arrow(draw, (col_x(index - 1)[1] + 5, mid_y), (x0 - 6, mid_y))

    # ------------------------------------------------------------- store band
    store_y0, store_y1 = 506, 648
    scols, sgap = 4, 60
    sw = (usable - (scols - 1) * sgap) // scols
    stores = [
        ("PostgreSQL", "authoritative: elements, chunks, generations, answers, audit"),
        ("Qdrant", "derived index: dense vectors + BM25 sparse, 9 filter fields"),
        ("Object store", "immutable originals, S3 protocol"),
        ("Embedding cache", "a PostgreSQL table: content-keyed vectors, 84% reuse on re-index"),
    ]
    for index, (name, detail) in enumerate(stores):
        x0 = margin + index * (sw + sgap)
        card(
            draw,
            (x0, store_y0, x0 + sw, store_y1),
            name,
            detail,
            FILL_DATA,
            title_size=28,
            detail_size=23,
        )

    # One connector, not one per store.
    #
    # An earlier revision drew a vertical arrow from each indexing stage to whichever
    # store sat directly beneath it, which was convenient and wrong: it showed
    # normalisation writing Qdrant and embedding writing the object store. A diagram
    # that asserts a false edge is worse than one that asserts less, so the lanes
    # connect to the band as a whole and each store says what it holds.
    bus_x = width // 2
    arrow(draw, (bus_x, top_y1 + 6), (bus_x, store_y0 - 6))
    draw.multiline_text(
        (bus_x + 30, (top_y1 + store_y0) // 2 - 36),
        "originals, elements, chunks and vectors are written\nas each stage completes",
        font=font(REG, 23),
        fill=MUTED,
    )

    # ------------------------------------------------------------- query lane
    lane_label(margin, 712, "QUERY PIPELINE", "runs once per question")
    bot_y0, bot_y1 = 758, 886
    query = [
        ("Question", "as typed", FILL_EDGE),
        ("Hard filters", "issuer, period, basis", FILL),
        ("Lexical + dense", "BM25 and vector search", FILL),
        ("Fusion", "reciprocal rank fusion", FILL),
        ("Reranking", "cross-encoder, CPU", FILL_HOST),
        ("Context", "bounded parent expansion", FILL),
        ("Generation", "schema-constrained LLM", FILL_HOST),
        ("Evidence Gate", "citations and numerals checked", FILL_OK),
    ]
    for index, (name, detail, fill) in enumerate(query):
        x0, x1 = col_x(index)
        card(draw, (x0, bot_y0, x1, bot_y1), name, detail, fill, title_size=27, detail_size=23)
        if index:
            mid_y = (bot_y0 + bot_y1) // 2
            arrow(draw, (col_x(index - 1)[1] + 5, mid_y), (x0 - 6, mid_y))

    arrow(draw, (bus_x, store_y1 + 6), (bus_x, bot_y0 - 6))
    draw.multiline_text(
        (bus_x + 30, (store_y1 + bot_y0) // 2 - 30),
        "retrieval reads candidates and filters; the Gate reads\nthe stored spans each claim cites",
        font=font(REG, 23),
        fill=MUTED,
    )

    # ---------------------------------------------------------------- outputs
    #
    # Both outputs fan out of the *end* of the lane. An earlier revision dropped one
    # arrow from the centre of each output box, which landed under "Hard filters" and
    # "Generation" — asserting that the filter stage emits the trace and that the model
    # emits the answer. Neither is true: the trace and the answer are both produced once
    # the pipeline completes, and the answer in particular leaves through the Gate.
    out_y0, out_y1 = 980, 1098
    trace_x0, trace_x1 = col_x(0)[0], col_x(2)[1]
    answer_x0, answer_x1 = col_x(5)[0], col_x(7)[1]
    card(
        draw,
        (trace_x0, out_y0, trace_x1, out_y1),
        "QueryTrace",
        "retriever contributions, scores, filters and degradation flags, persisted",
        FILL_DATA,
        title_size=30,
        detail_size=23,
    )
    card(
        draw,
        (answer_x0, out_y0, answer_x1, out_y1),
        "Answer, or a recorded refusal",
        "every released claim cites stored source spans; unsupported numerals are withheld",
        FILL_EDGE,
        title_size=30,
        detail_size=23,
    )

    gate_centre = (col_x(7)[0] + col_x(7)[1]) // 2
    bus_y = out_y0 - 58
    centres = [(trace_x0 + trace_x1) // 2, (answer_x0 + answer_x1) // 2]
    draw.line([(gate_centre, bot_y1 + 6), (gate_centre, bus_y)], fill=LINE, width=4)
    draw.line([(min(centres), bus_y), (gate_centre, bus_y)], fill=LINE, width=4)
    for centre_x in centres:
        arrow(draw, (centre_x, bus_y), (centre_x, out_y0 - 6))

    notes = [
        "Ollama runs host-native. No model has tool, filesystem, database or network access.",
        "Qdrant is rebuildable from PostgreSQL, so losing it costs time, never evidence.",
        "Reprocessing builds a shadow generation and activates it atomically,"
        " so a reader is never served a half-built index.",
    ]
    y = 1150
    for note in notes:
        draw.ellipse((margin, y + 9, margin + 11, y + 20), fill=MUTED)
        draw.text((margin + 26, y), note, font=font(REG, 24), fill=MUTED)
        y += 38

    out = ASSETS / "pipeline.png"
    image.save(out, "PNG")
    return out


# ---------------------------------------------------------------- kpi helpers
def panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    title: str,
    note: str,
) -> None:
    """A titled chart frame. Charts sit on a tint so the bars read as data and the
    page around them reads as prose."""
    x0, y0, _, _ = rect
    draw.rounded_rectangle(rect, radius=20, fill="#f8fafc", outline="#e2e8f0", width=3)
    draw.text((x0 + 34, y0 + 26), title, font=font(BOLD, 36), fill=INK)
    draw.text((x0 + 34, y0 + 76), note, font=font(REG, 25), fill=MUTED)


def hbar(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    label: str,
    value: float,
    maximum: float,
    reading: str,
    colour: str,
    *,
    label_width: int = 330,
    track: int = 470,
    height: int = 46,
) -> None:
    """One horizontal bar: label, track, filled proportion, and the reading as text.

    The reading is always printed. A bar is a proportion and a reader comparing two
    figures needs the figures, so the geometry carries the impression and the text
    carries the number.
    """
    x, y = origin
    f_label = font(REG, 26)
    draw.text((x, y + (height - 26) / 2 - 2), label, font=f_label, fill=MUTED)
    bar_x = x + label_width
    draw.rounded_rectangle([(bar_x, y), (bar_x + track, y + height)], radius=10, fill="#e8edf4")
    # A floor of 7 px, so a value that rounds to nothing still shows that it exists.
    filled = max(7, round(track * value / maximum)) if maximum else 7
    draw.rounded_rectangle([(bar_x, y), (bar_x + filled, y + height)], radius=10, fill=colour)
    draw.text(
        (bar_x + track + 20, y + (height - 27) / 2 - 2), reading, font=font(BOLD, 27), fill=INK
    )


def layout_chips(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    width: int,
    items: tuple[str, ...],
    *,
    size: int = 25,
    height: int = 54,
    gap: int = 14,
) -> tuple[list[tuple[tuple[int, int, int, int], str]], int]:
    """Measure a wrapped row of chips before anything is drawn.

    Layout is separated from drawing because the band behind the chips has to be
    painted first, and its height is not known until the chips have been placed.
    """
    f = font(REG, size)
    x, y = origin
    placed: list[tuple[tuple[int, int, int, int], str]] = []
    cx, cy = x, y
    for text in items:
        w = round(draw.textlength(text, font=f)) + 40
        if cx > x and cx + w > x + width:
            cx = x
            cy += height + gap
        placed.append(((cx, cy, cx + w, cy + height), text))
        cx += w + gap
    return placed, cy + height


def draw_chips(
    draw: ImageDraw.ImageDraw,
    placed: list[tuple[tuple[int, int, int, int], str]],
    fill: str,
    outline: str,
    *,
    size: int = 25,
) -> None:
    f = font(REG, size)
    for (x0, y0, x1, y1), text in placed:
        draw.rounded_rectangle(
            (x0, y0, x1, y1), radius=(y1 - y0) // 2, fill=fill, outline=outline, width=2
        )
        draw.text((x0 + 20, y0 + (y1 - y0 - size) / 2 - 3), text, font=f, fill=INK)


# -------------------------------------------------------------- kpi dashboard
def kpi_dashboard() -> Path:
    """Four measured results, each from a named record.

    Every figure here is a stopwatch or a count taken on the development corpus and
    recorded in ENV-010, ENV-013 or ENV-014. Nothing on this chart is a quality
    metric: there is no golden question set yet, so precision and recall have no
    figure to plot and none is invented.
    """
    width, height = 2400, 1560
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "Measured Results", font(BOLD, 62), 40, width, INK)
    centre(
        draw,
        "Every number below is a stopwatch reading or a defect count on the development corpus — "
        "none is a retrieval-quality claim",
        font(REG, 29),
        122,
        width,
        MUTED,
    )

    margin, gap = 70, 50
    pw = (width - 2 * margin - gap) // 2
    ph = 630
    rows = (186, 186 + ph + 60)
    cols = (margin, margin + pw + gap)

    # ---- panel 1: where a query's time goes (ENV-014 §3)
    rect = (cols[0], rows[0], cols[0] + pw, rows[0] + ph)
    panel(
        draw,
        rect,
        "Query latency, warm",
        "ENV-014 §3 · depth 25, in the container, cross-encoder resident",
    )
    y = rows[0] + 150
    peak = 2248.0
    for label, value, reading, colour in (
        ("Cross-encoder rerank", 2248, "2,248 ms", ROSE),
        ("Dense search + embed", 118, "118 ms", INDIGO),
        ("Lexical BM25 search", 62, "62 ms", CYAN),
        ("Resolve chunk text", 12, "12 ms", EMERALD),
        ("Reciprocal rank fusion", 0.4, "<1 ms", AMBER),
    ):
        hbar(draw, (cols[0] + 34, y), label, value, peak, reading, colour)
        y += 76
    draw.text(
        (cols[0] + 34, rows[0] + ph - 96),
        "Reranking is 92% of a warm request. Turn it off and the\n"
        "same query returns in 175 ms — accuracy bought with latency.",
        font=font(REG, 25),
        fill=MUTED,
    )

    # ---- panel 2: the embedding cache (ENV-013 §4)
    rect = (cols[1], rows[0], cols[1] + pw, rows[0] + ph)
    panel(
        draw,
        rect,
        "Embedding cache, full corpus",
        "ENV-013 §4 · 4,859 child chunks re-embedded, 100% hit rate",
    )
    y = rows[0] + 160
    for label, value, reading, colour in (
        ("Cold — cache empty", 2360.77, "2,360.77 s", ROSE),
        ("Warm — every text a hit", 2.91, "2.91 s", EMERALD),
    ):
        hbar(draw, (cols[1] + 34, y), label, value, 2360.77, reading, colour, height=62)
        y += 104
    big = font(BOLD, 110)
    # The glyphs below are the ones the rendered chart must show: a real multiplication
    # sign and a real minus sign, not their ASCII lookalikes.
    draw.text((cols[1] + 54, rows[0] + 388), "811×", font=big, fill=INDIGO)  # noqa: RUF001
    draw.text(
        (cols[1] + 400, rows[0] + 404),
        "faster on unchanged text,\n"
        "and a hit is bit-identical: 200 of 200\n"
        "vectors matched to exactly 0.0",
        font=font(REG, 26),
        fill=MUTED,
    )
    draw.text(
        (cols[1] + 34, rows[0] + ph - 66),
        "A real re-index mixes hits and misses: 39.3 min → 6.3 min, 6.3×.",  # noqa: RUF001
        font=font(REG, 25),
        fill=MUTED,
    )

    # ---- panel 3: text-quality defects (ENV-013 §1)
    rect = (cols[0], rows[1], cols[0] + pw, rows[1] + ph)
    panel(
        draw,
        rect,
        "Text-quality defects eliminated",
        "ENV-013 §1 · extraction config 2 → config 3, same bytes",
    )
    y = rows[1] + 150
    worst = 874.0
    for label, value, reading, colour in (
        ("Words split at a line break", 874, "874 → 1", INDIGO),
        ("Unreachable passages", 573, "573 → 0", ROSE),
        ("Chunks carrying a ligature", 441, "441 → 0", AMBER),
        ("Non-breaking spaces", 182, "182 → 0", CYAN),
        ("C0/C1 control characters", 127, "127 → 0", EMERALD),
    ):
        hbar(draw, (cols[0] + 34, y), label, value, worst, reading, colour)
        y += 76
    draw.text(
        (cols[0] + 34, rows[1] + ph - 96),
        "573 passages spelled an ordinary word with a ligature and could not\n"
        "be found by typing it normally. That count is now zero.",
        font=font(REG, 25),
        fill=MUTED,
    )

    # ---- panel 4: image size (ENV-014 §1)
    rect = (cols[1], rows[1], cols[1] + pw, rows[1] + ph)
    panel(
        draw,
        rect,
        "Container images slimmed",
        "ENV-014 §1 · one recursive chmod was storing the venv twice",
    )
    y = rows[1] + 150
    for label, value, reading, colour in (
        ("API — before", 3150, "3.15 GB", "#94a3b8"),
        ("API — after", 1660, "1.66 GB  −47%", INDIGO),  # noqa: RUF001
        ("UI — before", 1190, "1.19 GB", "#94a3b8"),
        ("UI — after", 683, "683 MB  −43%", EMERALD),  # noqa: RUF001
    ):
        hbar(draw, (cols[1] + 34, y), label, value, 3150.0, reading, colour)
        y += 76
    draw.text(
        (cols[1] + 34, rows[1] + ph - 150),
        "A recursive chown/chmod rewrites every file it touches, and a rewritten\n"
        "file is a new layer entry — so the virtual environment was stored twice,\n"
        "the second copy differing by a permission bit. Removing it freed 1.14 GB.",
        font=font(REG, 25),
        fill=MUTED,
    )

    draw.text(
        (margin, height - 56),
        "Host for every figure: Intel i7-1165G7, 4 cores / 8 threads, 15.7 GB, no accelerator. "
        "One host, one corpus — no cross-host claim is made.",
        font=font(REG, 25),
        fill=MUTED,
    )

    out = ASSETS / "kpi-dashboard.png"
    image.save(out, "PNG")
    return out


# ----------------------------------------------------------------- corpus funnel
def corpus_funnel() -> Path:
    """What three filings become, stage by stage, with the real counts."""
    width, height = 2400, 700
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "From Filing to Searchable Evidence", font(BOLD, 58), 40, width, INK)
    centre(
        draw,
        "The development corpus, counted at every stage — three real filings, nothing synthetic",
        font(REG, 29),
        118,
        width,
        MUTED,
    )

    margin, gap = 70, 40
    stages = [
        ("3", "filings acquired", "27.2 MB, official sources", FILL_EDGE, INDIGO),
        ("1,403", "pages extracted", "1 coverage gap, recorded", FILL, LINE),
        ("40,476", "text blocks", "verbatim, with coordinates", FILL, LINE),
        ("861", "tables detected", "23,613 cells, each citable", FILL, LINE),
        ("5,628", "chunks built", "parent + child, config 4", FILL_DATA, CYAN),
        ("4,859", "vectors indexed", "768-d dense + BM25 sparse", FILL_OK, EMERALD),
    ]
    cw = (width - 2 * margin - (len(stages) - 1) * gap) // len(stages)
    y0, y1 = 200, 478
    x = margin
    for index, (value, label, note, fill, outline) in enumerate(stages):
        draw.rounded_rectangle(
            [(x, y0), (x + cw, y1)], radius=18, fill=fill, outline=outline, width=3
        )
        f_value = font(BOLD, 72)
        vw = draw.textlength(value, font=f_value)
        draw.text((x + (cw - vw) / 2, y0 + 46), value, font=f_value, fill=INK)
        f_label = font(BOLD, 28)
        lw = draw.textlength(label, font=f_label)
        draw.text((x + (cw - lw) / 2, y0 + 150), label, font=f_label, fill=INK)
        for offset, line in enumerate(wrap(draw, note, font(REG, 23), cw - 40)):
            nw = draw.textlength(line, font=font(REG, 23))
            draw.text(
                (x + (cw - nw) / 2, y0 + 196 + offset * 30), line, font=font(REG, 23), fill=MUTED
            )
        if index:
            arrow(draw, (x - gap + 4, (y0 + y1) // 2), (x - 8, (y0 + y1) // 2))
        x += cw + gap

    notes = [
        "61,150 elements carry text. Every one keeps its page, its coordinates and the bytes"
        " it was read from, so a claim can point at it.",
        "A page that yields no text is stored as a recorded coverage gap with a reason"
        " — never silently dropped, so a reader can be told a region was never searched.",
        "PostgreSQL holds 127.44 MiB for the whole corpus; Qdrant holds 81.1 MiB resident"
        " and is rebuildable from it.",
    ]
    y = 540
    for note in notes:
        draw.ellipse((margin, y + 9, margin + 11, y + 20), fill=MUTED)
        draw.text((margin + 28, y), note, font=font(REG, 25), fill=MUTED)
        y += 40

    out = ASSETS / "corpus-funnel.png"
    image.save(out, "PNG")
    return out


# -------------------------------------------------------------------- tech stack
def tech_stack() -> Path:
    """The stack as layers, with the rule each layer obeys.

    Drawn as bands rather than a logo wall because the interesting claim is the
    dependency direction: the domain core sits above the adapters and imports none
    of them, which is what makes the stores replaceable.
    """
    width = 2400
    margin = 70
    inner = width - 2 * margin

    layers = (
        (
            "INTERFACE",
            "API-only. Holds no data-store credential.",
            INDIGO,
            FILL_EDGE,
            ("Streamlit 1.65.0", "argparse CLI", "5 pages", "no DB driver in the image"),
        ),
        (
            "API",
            "Bearer auth on every non-health route.",
            EMERALD,
            FILL_APP,
            ("FastAPI 0.141.1", "Pydantic 2.13.5", "uvicorn 0.52.4", "OpenAPI /docs", "7 routes"),
        ),
        (
            "DOMAIN CORE",
            "Imports no adapter. Pure Python 3.12, fully typed.",
            AMBER,
            "#fffbeb",
            (
                "chunking",
                "lexical · BM25",
                "retrieval · RRF",
                "reranking",
                "generation",
                "Evidence Gate",
                "Decimal arithmetic",
                "langchain-text-splitters",
            ),
        ),
        (
            "ADAPTERS",
            "One SDK, one module. Nothing else imports it.",
            CYAN,
            FILL_DATA,
            (
                "psycopg 3.3.5",
                "SQLAlchemy 2.0.52",
                "Alembic 1.20.0",
                "qdrant-client 1.19.1",
                "boto3 1.43.96",
                "httpx 0.28.1",
                "PyMuPDF 1.28.2",
                "pdfplumber",
                "Docling 2.133.0",
                "transformers 5.18.0",
                "torch 2.14.1+cpu",
            ),
        ),
        (
            "INFRASTRUCTURE",
            "Single-node Compose. Ollama stays host-native.",
            ROSE,
            "#fff1f2",
            (
                "PostgreSQL 18.6",
                "Qdrant 1.19.1",
                "SeaweedFS 4.47",
                "Ollama · nomic-embed-text",
                "Ollama · llama3.1:8b",
                "Docker Compose",
            ),
        ),
        (
            "QUALITY GATES",
            "Run locally before anything is reported.",
            "#8b5cf6",
            "#f5f3ff",
            (
                "pytest 9.1.1 · 1,794 tests",
                "ruff 0.16.7",
                "mypy 2.3.1 strict",
                "pip-audit",
                "pip check",
            ),
        ),
    )

    # Two passes: measure every band, then draw on a canvas of the right height.
    probe = ImageDraw.Draw(Image.new("RGB", (width, 10), WHITE))
    bands: list[tuple[int, int, list[tuple[tuple[int, int, int, int], str]]]] = []
    y = 196
    for _, _, _, _, chips in layers:
        placed, bottom = layout_chips(probe, (margin + 330, y + 32), inner - 360, chips)
        band_height = max(152, bottom - y + 32)
        bands.append((y, band_height, placed))
        y += band_height + 26

    height = y + 70
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "Technology Stack", font(BOLD, 58), 38, width, INK)
    centre(
        draw,
        "Six layers, and the dependency arrow points only downwards"
        " — the domain core imports no adapter, "
        "which is what makes a store replaceable",
        font(REG, 28),
        114,
        width,
        MUTED,
    )

    for (name, rule, accent, fill, _), (band_y, band_height, placed) in zip(
        layers, bands, strict=True
    ):
        draw.rounded_rectangle(
            [(margin, band_y), (margin + inner, band_y + band_height)],
            radius=18,
            fill=fill,
            outline=accent,
            width=3,
        )
        draw.rectangle(
            [(margin, band_y + 12), (margin + 10, band_y + band_height - 12)], fill=accent
        )
        draw.text((margin + 36, band_y + 26), name, font=font(BOLD, 31), fill=INK)
        for offset, line in enumerate(wrap(draw, rule, font(REG, 23), 270)):
            draw.text(
                (margin + 36, band_y + 68 + offset * 30), line, font=font(REG, 23), fill=MUTED
            )
        draw_chips(draw, placed, WHITE, accent)

    draw.text(
        (margin, height - 56),
        "Python 3.12 throughout. 165 pinned dependencies in one requirements.txt;"
        " the API image installs the 57 it actually imports.",
        font=font(REG, 25),
        fill=MUTED,
    )

    out = ASSETS / "tech-stack.png"
    image.save(out, "PNG")
    return out


# -------------------------------------------------------------------- data model
def data_model() -> Path:
    """The fourteen tables, grouped by what they are for, with measured row counts.

    Children are indented under their parent with an elbow connector rather than
    chained with arrows: ``source_elements`` and ``source_tables`` are siblings of
    one run, and a straight chain through them would assert a relation that does
    not exist.
    """
    width, height = 2400, 1110
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    centre(draw, "Data Model — PostgreSQL is Authoritative", font(BOLD, 56), 38, width, INK)
    centre(
        draw,
        "Fourteen tables, 46 CHECK constraints, every schema change a reversible Alembic revision. "
        "Row counts are the development corpus.",
        font(REG, 28),
        112,
        width,
        MUTED,
    )

    margin, gap = 70, 44
    groups = (
        (
            "INTAKE",
            INDIGO,
            FILL_EDGE,
            (
                (0, "documents", "one row per filing identity"),
                (1, "document_versions", "content-addressed, SHA-256"),
                (2, "document_metadata", "issuer, period, basis, currency"),
            ),
        ),
        (
            "SOURCE REPRESENTATION",
            CYAN,
            FILL_DATA,
            (
                (0, "extraction_runs", "config version, state, gaps"),
                (1, "source_elements", "108,268 rows · 52.59 MiB"),
                (1, "source_tables", "861 rows, each with a verdict"),
                (2, "source_table_cells", "23,613 rows, every one citable"),
            ),
        ),
        (
            "RETRIEVAL",
            EMERALD,
            FILL_OK,
            (
                (0, "generations", "shadow → active → superseded"),
                (1, "chunks", "5,759 rows · tsvector lexemes"),
                (2, "chunk_sources", "66,928 rows · 76% index"),
                (1, "index_outbox", "transactional outbox, drained"),
                (0, "embedding_cache", "content-keyed, double precision"),
            ),
        ),
        (
            "ANSWER & AUDIT",
            AMBER,
            FILL_HOST,
            (
                (0, "answers", "one decision per answer"),
                (1, "answer_claims", "released and withheld, with reasons"),
            ),
        ),
    )
    gw = (width - 2 * margin - 3 * gap) // 4
    zone_y0, zone_y1 = 200, 872
    x = margin
    for name, accent, fill, tables in groups:
        draw.rounded_rectangle(
            [(x, zone_y0), (x + gw, zone_y1)], radius=18, outline=accent, width=4
        )
        label_w = draw.textlength(name, font=font(BOLD, 27))
        draw.rectangle([(x + 26, zone_y0 - 19), (x + 48 + label_w, zone_y0 + 19)], fill=WHITE)
        draw.text((x + 36, zone_y0 - 15), name, font=font(BOLD, 27), fill=accent)

        y = zone_y0 + 48
        previous: dict[int, int] = {}
        for depth, table, note in tables:
            indent = depth * 34
            cx0, cx1 = x + 26 + indent, x + gw - 26
            card_h = 104
            draw.rounded_rectangle(
                [(cx0, y), (cx1, y + card_h)], radius=12, fill=fill, outline=accent, width=2
            )
            draw.text((cx0 + 18, y + 16), table, font=font(MONO, 27), fill=INK)
            for offset, line in enumerate(wrap(draw, note, font(REG, 22), cx1 - cx0 - 36)):
                draw.text((cx0 + 18, y + 54 + offset * 26), line, font=font(REG, 22), fill=MUTED)
            # An elbow from the parent's left edge down into this child's left edge.
            if depth and depth - 1 in previous:
                px = x + 26 + (depth - 1) * 34 + 16
                draw.line([(px, previous[depth - 1]), (px, y + card_h // 2)], fill=accent, width=3)
                draw.line([(px, y + card_h // 2), (cx0 - 4, y + card_h // 2)], fill=accent, width=3)
                draw.polygon(
                    [
                        (cx0, y + card_h // 2),
                        (cx0 - 12, y + card_h // 2 - 7),
                        (cx0 - 12, y + card_h // 2 + 7),
                    ],
                    fill=accent,
                )
            previous[depth] = y + card_h
            y += card_h + 18
        x += gw + gap

    for index in range(3):
        cx = margin + (index + 1) * gw + index * gap + gap // 2
        mid_y = (zone_y0 + zone_y1) // 2
        arrow(draw, (cx - gap // 2 + 4, mid_y), (cx + gap // 2 - 6, mid_y))

    notes = [
        "Nothing is ever updated in place. A re-extraction writes a new run, a re-chunk writes"
        " a new generation, and the previous one keeps serving queries until the replacement"
        " is indexed and reconciled.",
        "Superseded rows are kept, because a citation issued against them must keep resolving"
        " — that audit trail is the reason generations exist.",
        "Qdrant holds no text. A vector search answers with identifiers, and the text is read"
        " back from PostgreSQL, so the index can be rebuilt without losing evidence.",
    ]
    y = 920
    for note in notes:
        draw.ellipse((margin, y + 9, margin + 11, y + 20), fill=MUTED)
        draw.text((margin + 28, y), note, font=font(REG, 25), fill=MUTED)
        y += 40

    out = ASSETS / "data-model.png"
    image.save(out, "PNG")
    return out


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    producers = (
        banner,
        architecture,
        evidence_gate,
        pipeline,
        kpi_dashboard,
        corpus_funnel,
        tech_stack,
        data_model,
    )
    for produce in producers:
        path = produce()
        size = path.stat().st_size / 1024
        with Image.open(path) as rendered:
            print(f"  {path.name:22} {rendered.width}x{rendered.height}  {size:6.0f} KiB")


if __name__ == "__main__":
    main()
