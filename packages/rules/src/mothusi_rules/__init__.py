"""Mothusi rules: deterministic checks over the contracts. No model calls live here."""

from mothusi_rules.accounting import foreign_entry_penalty, one_part_correct
from mothusi_rules.arithmetic import BOUNDS, TOTALS, check_bounds, check_script, check_totals
from mothusi_rules.blind import BLIND, check_brief_blind
from mothusi_rules.catchup import CATCH_UP, check_catch_up, load_atp_weeks
from mothusi_rules.citations import MEMO_LINE, check_citations, check_memo_lines
from mothusi_rules.cognitive import COGNITIVE_GRID, check_cognitive_grid
from mothusi_rules.evidence import EVIDENCE, check_evidence
from mothusi_rules.exhibits import LICENCES, MAX_MODEL_BYTES, check_exhibit
from mothusi_rules.items import item_analysis
from mothusi_rules.marksheet import mark_sheet_csv
from mothusi_rules.moderation import disagreements, moderation_sample, should_widen
from mothusi_rules.notes import NOTE_GROUNDING, check_note_grounding
from mothusi_rules.numeric import equivalent
from mothusi_rules.pack import PACK_WEIGHTS, check_pack_weights, load_pack
from mothusi_rules.poa import convert_mark, find_task
from mothusi_rules.reading import flag_uncertain_evidence
from mothusi_rules.report import class_feedback_md, moderation_report_md

__all__ = [
    "BLIND",
    "BOUNDS",
    "CATCH_UP",
    "COGNITIVE_GRID",
    "EVIDENCE",
    "LICENCES",
    "MAX_MODEL_BYTES",
    "MEMO_LINE",
    "NOTE_GROUNDING",
    "PACK_WEIGHTS",
    "TOTALS",
    "check_bounds",
    "check_brief_blind",
    "check_catch_up",
    "check_citations",
    "check_cognitive_grid",
    "check_evidence",
    "check_exhibit",
    "check_memo_lines",
    "check_note_grounding",
    "check_pack_weights",
    "check_script",
    "check_totals",
    "class_feedback_md",
    "convert_mark",
    "disagreements",
    "equivalent",
    "find_task",
    "flag_uncertain_evidence",
    "foreign_entry_penalty",
    "item_analysis",
    "load_atp_weeks",
    "load_pack",
    "mark_sheet_csv",
    "moderation_report_md",
    "moderation_sample",
    "one_part_correct",
    "should_widen",
]
