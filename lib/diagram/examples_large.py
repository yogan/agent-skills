"""The third sample set: the size end, one diagram.

`examples.py` and `examples_repo.py` are both one scenario drawn five ways, and both observe
the content limits — five states, six messages, tables showing only the columns a change
touched. That is what a *legible* diagram looks like, and it is most of what the engine has to
get right. It is not everything: a reader asked the `visualize` skill for a warehouse schema
and got eight tables, twelve foreign keys and a legend, three times the area of anything else
here.

**Crowding only exists at this size.** Two edge labels cannot compete for the same stretch of
line, and two arrowheads cannot land a few px apart, on a drawing with five boxes in it: there
is too much room. Every rule about a label keeping clear of somebody else's arrow, and about
arrivals at one box merging or staying apart, is unexercised by the other two sets — they came
back byte-identical when those rules were added, which is the tell.

So one diagram, and one kind. A second architecture or state machine at this scale would cost
a browser and a d2 compile per check to re-prove what the ER already proves; if a defect turns
up that needs one, add it then.

**One note, where the original had four, and the reason is what a note COSTS.** Note placement
is the most expensive thing the engine does — every anchor tried is a d2 compile of the whole
graph plus a browser page, and on a diagram this size that is ~5s a note, which is also what
arranging and checking the entire diagram costs. Four notes made this one diagram cost more
than the other ten together (25.3s against 20.9s); with one it is 10.6s.

They also bought nothing here: all four find an anchor covering no line, no corner and no
arrowhead, so none of them exercises the search. The one kept is the one inside the drawing
rather than out in the margin, and it is here to show that a callout still lands cleanly on a
canvas this dense — not to test placement, which `test_place_slow` does properly.

Transcribed from a real run and anonymised: the table, column and cardinality strings are
invented, and their LENGTHS are matched to the originals within a character, because ELK sizes
a table to its widest row and the gap between two layers to the label that sits in it. Change
the wording here and the geometry moves with it — this spec is a fixture, not a document.
"""

# Eight tables, all reachable from the receipt/order fact table at the left. Four of the twelve
# foreign keys point at one row of one table, which is the arrival crowding; the two long
# horizontal runs across the middle are what the edge labels compete for.
SCHEMA = {
    "kind": "er",
    "title": "Warehouse data models",
    "legend": {"store": "queryable today", "ext": "grant still pending"},
    "tables": [
        {
            "id": "tbl00000412_v1 (Receipt x Order)",
            "role": "ext",
            "columns": [
                {"name": "docno", "type": "nvarchar(10)"},
                {"name": "vno", "type": "nvarchar(10)"},
                {"name": "ledno", "type": "nvarchar(4)"},
                {"name": "buorg", "type": "nvarchar(4)"},
                {"name": "fctry", "type": "nvarchar(4)"},
                {"name": "country_code2", "type": "nvarchar(2)"},
                {"name": "pstdt", "type": "date"},
                {"name": "xref1", "type": "nvarchar(128)"},
            ],
        },
        {
            "id": "tbl00000731_v1 (OHDR)",
            "role": "ext",
            "columns": [
                {"name": "order_doc_num", "type": "nvarchar(10)", "key": "pk"},
                {"name": "factory", "type": "nchar(4)", "key": "pk"},
                {"name": "vendor_no", "type": "nvarchar(10)", "key": "fk"},
                {"name": "cached_at", "type": "timestamp"},
            ],
        },
        {
            "id": "tbl00000205_v5 (SITES)",
            "role": "ext",
            "columns": [
                {"name": "factory", "type": "text", "key": "pk"},
                {"name": "valuation_group", "type": "text", "key": "fk"},
                {"name": "buying_unit", "type": "text"},
                {"name": "zone", "type": "text"},
            ],
        },
        {
            "id": "tbl00000668_v3 (VALGR)",
            "role": "ext",
            "columns": [
                {"name": "valuation_group", "type": "text", "key": "pk"},
                {"name": "factory_zone", "type": "text"},
                {"name": "factory_no", "type": "text"},
                {"name": "ledger_entity", "type": "text", "key": "fk"},
            ],
        },
        {
            "id": "tbl00000349_v2 (BUORG)",
            "role": "ext",
            "note": "ledger entity may be blank",
            "columns": [
                {"name": "buying_unit", "type": "text", "key": "pk"},
                {"name": "ledger_entity", "type": "text", "key": "fk"},
                {"name": "description", "type": "text"},
            ],
        },
        {
            "id": "tbl00000127_v1 (LEDG)",
            "role": "store",
            "columns": [
                {"name": "ledger_entity", "type": "text", "key": "pk"},
                {"name": "title", "type": "text"},
                {"name": "zone", "type": "text"},
            ],
        },
        {
            "id": "tbl00000583_v6 (SUPP)",
            "role": "store",
            "columns": [
                {"name": "supplier", "type": "text", "key": "pk"},
                {"name": "name1", "type": "text"},
                {"name": "vat_id_no", "type": "text"},
                {"name": "tax_num_1", "type": "text"},
            ],
        },
        {
            "id": "tbl00000094_v3 (SUPL)",
            "role": "store",
            "columns": [
                {"name": "supplier", "type": "text", "key": "fk"},
                {"name": "ledger_entity", "type": "text", "key": "fk"},
            ],
        },
    ],
    "edges": [
        {"from": "tbl00000412_v1 (Receipt x Order).docno",
         "to": "tbl00000731_v1 (OHDR).order_doc_num",
         "label": "n GRN lines : 1 PO document"},
        {"from": "tbl00000731_v1 (OHDR).vendor_no",
         "to": "tbl00000583_v6 (SUPP).supplier",
         "label": "n PO documents : 1 supplier"},
        {"from": "tbl00000731_v1 (OHDR).factory",
         "to": "tbl00000205_v5 (SITES).factory",
         "label": "n PO documents : 1 factory"},
        {"from": "tbl00000412_v1 (Receipt x Order).vno",
         "to": "tbl00000583_v6 (SUPP).supplier",
         "label": "n GRN lines : 1 supplier"},
        {"from": "tbl00000412_v1 (Receipt x Order).fctry",
         "to": "tbl00000205_v5 (SITES).factory",
         "label": "n GRN lines : 1 factory"},
        {"from": "tbl00000412_v1 (Receipt x Order).buorg",
         "to": "tbl00000349_v2 (BUORG).buying_unit",
         "label": "n GRN lines : 1 buying unit"},
        {"from": "tbl00000412_v1 (Receipt x Order).ledno",
         "to": "tbl00000127_v1 (LEDG).ledger_entity",
         "label": "n GRN lines : 1 ledger entity"},
        {"from": "tbl00000205_v5 (SITES).valuation_group",
         "to": "tbl00000668_v3 (VALGR).valuation_group",
         "label": "1 factory : 1 valuation group"},
        {"from": "tbl00000668_v3 (VALGR).ledger_entity",
         "to": "tbl00000127_v1 (LEDG).ledger_entity",
         "label": "n valuation groups : 1 ledger entity"},
        {"from": "tbl00000349_v2 (BUORG).ledger_entity",
         "to": "tbl00000127_v1 (LEDG).ledger_entity",
         "label": "n buying unit : 1 ledger entity"},
        {"from": "tbl00000094_v3 (SUPL).supplier",
         "to": "tbl00000583_v6 (SUPP).supplier",
         "label": "n ledger rows : 1 supplier"},
        {"from": "tbl00000094_v3 (SUPL).ledger_entity",
         "to": "tbl00000127_v1 (LEDG).ledger_entity",
         "label": "n ledger rows : 1 ledger entity"},
    ],
}

LARGE = {"er": SCHEMA}
